import datetime
import json
import logging

from django.http import JsonResponse

from apps.optin import repository
from apps.optin.cerc_mapping import correlacionar_por_referencia, interpretar_item_opt_in
from apps.optin.idempotency import idempotente
from apps.optin.validation import (
    ValidationError,
    mascarar_documento,
    validar_arranjos,
    validar_credenciadoras,
    validar_documento,
    validar_evidencia,
    validar_vigencia,
)
from services.cerc.client import CercApiError, registrar_optin
from shared.jwt_auth import jwt_required
from shared.tenant_config import get_tenant_config

logger = logging.getLogger(__name__)


def health(request):
    return JsonResponse({"status": "ok"})


def _erro_json(codigo: str, mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"erro": codigo, "mensagem": mensagem}, status=status)


def _serializar_optin(optin: dict) -> dict:
    return {
        "id": optin["id"],
        "referenciaExterna": optin["referencia_externa"],
        "protocoloCerc": optin.get("protocolo_cerc"),
        "origem": optin["origem"],
        "status": optin["status"],
        "cnpjSolicitante": optin["cnpj_solicitante"],
        "cnpjFinanciador": optin["cnpj_financiador"],
        "usuarioFinalRecebedor": optin["documento_ufr"],
        "titular": optin.get("documento_titular"),
        "dataAssinatura": str(optin["data_assinatura"]),
        "vigenciaInicio": str(optin["vigencia_inicio"]),
        "vigenciaFim": str(optin["vigencia_fim"]),
        "carteira": optin.get("carteira"),
        "credenciadoras": optin.get("credenciadoras", []),
        "arranjos": optin.get("arranjos", []),
        "criadoEm": optin["criado_em"].isoformat() if hasattr(optin["criado_em"], "isoformat") else optin["criado_em"],
    }


@jwt_required
@idempotente("optin_create")
def criar_optin(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    try:
        documento_ufr, tipo_ufr = validar_documento(payload.get("usuarioFinalRecebedor", ""))
        titular_raw = payload.get("titular") or payload.get("usuarioFinalRecebedor", "")
        documento_titular, _ = validar_documento(titular_raw)

        credenciadoras = payload.get("credenciadoras") or []
        arranjos = payload.get("arranjos") or []
        validar_credenciadoras(credenciadoras)
        validar_arranjos(arranjos, repository.arranjos_ativos(request.financiador_id))
        validar_evidencia(payload.get("evidenciaAutorizacaoId"))

        data_assinatura = datetime.date.fromisoformat(payload["dataAssinatura"])
        vigencia_inicio = datetime.date.fromisoformat(payload["vigenciaInicio"])
        vigencia_fim = datetime.date.fromisoformat(payload["vigenciaFim"])
        validar_vigencia(data_assinatura, vigencia_inicio, vigencia_fim)
    except ValidationError as exc:
        return _erro_json(exc.codigo, exc.mensagem, 422)
    except (KeyError, TypeError, ValueError):
        return _erro_json("VAL_CAMPO_OBRIGATORIO", "campo obrigatório ausente ou mal formatado", 422)

    if repository.existe_optin_ativo_equivalente(
        request.financiador_id, documento_ufr, documento_titular, set(credenciadoras), set(arranjos), vigencia_inicio, vigencia_fim
    ):
        return _erro_json("VAL010", "opt-in equivalente já ativo", 409)

    cnpj_solicitante = get_tenant_config(request.financiador_id)["cerc_cnpj_solicitante"]

    optin = repository.criar_optin_pendente(request.financiador_id, {
        "cnpj_solicitante": cnpj_solicitante,
        "cnpj_financiador": request.financiador_id,
        "documento_ufr": documento_ufr,
        "documento_ufr_tipo": tipo_ufr,
        "documento_titular": documento_titular,
        "data_assinatura": data_assinatura,
        "vigencia_inicio": vigencia_inicio,
        "vigencia_fim": vigencia_fim,
        "carteira": payload.get("carteira"),
        "evidencia_id": payload["evidenciaAutorizacaoId"],
        "credenciadoras": credenciadoras,
        "arranjos": arranjos,
    })

    logger.info(
        "optin criado PENDENTE referencia=%s ufr=%s", optin["referencia_externa"], mascarar_documento(documento_ufr)
    )

    payload_cerc = {
        "referenciaExterna": optin["referencia_externa"],
        "cnpjSolicitante": cnpj_solicitante,
        "cnpjFinanciador": request.financiador_id,
        "dataAssinaturaOptIn": str(data_assinatura),
        "carteira": optin.get("carteira"),
        "definicaoUnidadeRecebivel": {
            "listaCnpjCredenciadora": credenciadoras,
            "listaCodigoArranjoPagamento": arranjos,
            "documentoUsuarioFinalRecebedor": documento_ufr,
            "documentoTitular": documento_titular,
            "dataInicio": str(vigencia_inicio),
            "dataFim": str(vigencia_fim),
        },
    }

    try:
        resposta = registrar_optin(request.financiador_id, payload_cerc, correlacao_id=optin["referencia_externa"])
    except Exception as exc:  # noqa: BLE001 - transporte (httpx) e negócio (CercApiError) tratados juntos aqui; classificação fina retentável/não-retentável (§9.2) fica no job de reconciliação, fora de escopo
        repository.atualizar_status(request.financiador_id, optin["id"], "FALHA_ENVIO")
        logger.warning("falha ao enviar optin %s para CERC: %s", optin["referencia_externa"], exc)
        return _erro_json("CERC_INDISPONIVEL", "falha ao registrar opt-in na CERC", 502)

    item = correlacionar_por_referencia(resposta, optin["referencia_externa"])
    resultado = interpretar_item_opt_in(item)

    if resultado.status_local == "ATIVO":
        optin_final = repository.atualizar_status(request.financiador_id, optin["id"], "ATIVO", protocolo_cerc=resultado.protocolo)
        return JsonResponse(_serializar_optin(optin_final), status=201)

    repository.atualizar_status(request.financiador_id, optin["id"], "REJEITADO")
    return _erro_json(resultado.erro_codigo or "REJEITADO", resultado.erro_mensagem or "opt-in rejeitado pela CERC", 422)


@jwt_required
def listar_optins(request):
    filtros = {
        "status": request.GET.get("status"),
        "documento_ufr": request.GET.get("usuarioFinalRecebedor"),
        "origem": request.GET.get("origem"),
        "carteira": request.GET.get("carteira"),
        "vigente_em": request.GET.get("vigenteEm"),
    }
    limit = min(int(request.GET.get("limit", 50)), 200)
    resultado = repository.listar(request.financiador_id, filtros, limit)
    return JsonResponse({"dados": [_serializar_optin(o) for o in resultado]})


@jwt_required
def detalhar_optin(request, optin_id):
    optin = repository.buscar_por_id(request.financiador_id, optin_id)
    if optin is None:
        return _erro_json("OPTIN_NAO_ENCONTRADO", "opt-in não encontrado", 404)
    return JsonResponse(_serializar_optin(optin))


def optin_detail(request, optin_id):
    if request.method == "GET":
        return detalhar_optin(request, optin_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)


def optins_collection(request):
    if request.method == "POST":
        return criar_optin(request)
    if request.method == "GET":
        return listar_optins(request)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
