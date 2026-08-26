import datetime
import json
import logging

from django.http import JsonResponse

from apps.cliente import repository as cliente_repository
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
from services.cerc.client import CercApiError, atualizar_optin as atualizar_optin_cerc, encerrar_optin, registrar_optin
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
        "clienteId": optin["cliente_id"],
        "clienteNome": optin.get("cliente_nome"),
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

    cliente_id = payload.get("clienteId")
    if not cliente_id:
        return _erro_json("CLI002", "clienteId é obrigatório", 422)

    cliente = cliente_repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)

    documento_ufr, tipo_ufr = cliente["documento"], cliente["documento_tipo"]

    try:
        titular_raw = payload.get("titular") or documento_ufr
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
        "cliente_id": cliente["id"],
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


CAMPOS_NAO_ATUALIZAVEIS = {"referenciaExterna", "cnpjSolicitante"}


@jwt_required
@idempotente("optin_update")
def atualizar_optin_view(request, optin_id):
    optin = repository.buscar_por_id(request.financiador_id, optin_id)
    if optin is None:
        return _erro_json("OPTIN_NAO_ENCONTRADO", "opt-in não encontrado", 404)

    if optin["status"] != "ATIVO":
        return _erro_json("OPTIN_NAO_ATIVO", "só é possível atualizar opt-in ATIVO", 409)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    campos_proibidos = CAMPOS_NAO_ATUALIZAVEIS & payload.keys()
    if campos_proibidos:
        return _erro_json("CAMPO_NAO_ATUALIZAVEL", f"campos não atualizáveis: {sorted(campos_proibidos)}", 422)

    if not optin.get("protocolo_cerc"):
        return _erro_json("PROTOCOLO_AUSENTE", "opt-in sem protocolo_cerc não pode ser atualizado", 422)

    credenciadoras = optin["credenciadoras"]
    arranjos = optin["arranjos"]
    vigencia_inicio = optin["vigencia_inicio"]
    vigencia_fim = payload.get("vigenciaFim")

    try:
        if vigencia_fim:
            vigencia_fim = datetime.date.fromisoformat(vigencia_fim)
            validar_vigencia(optin["data_assinatura"], vigencia_inicio, vigencia_fim)
        else:
            vigencia_fim = optin["vigencia_fim"]
        if "arranjos" in payload:
            arranjos = payload["arranjos"]
            validar_arranjos(arranjos, repository.arranjos_ativos(request.financiador_id))
        if "credenciadoras" in payload:
            credenciadoras = payload["credenciadoras"]
            validar_credenciadoras(credenciadoras)
    except ValidationError as exc:
        return _erro_json(exc.codigo, exc.mensagem, 422)

    payload_cerc = {
        "referenciaExterna": optin["referencia_externa"],
        "cnpjSolicitante": get_tenant_config(request.financiador_id)["cerc_cnpj_solicitante"],
        "cnpjFinanciador": payload.get("cnpjFinanciador", optin["cnpj_financiador"]),
        "dataAssinaturaOptIn": str(optin["data_assinatura"]),
        "carteira": payload.get("carteira", optin.get("carteira")),
        "definicaoUnidadeRecebivel": {
            "listaCnpjCredenciadora": credenciadoras,
            "listaCodigoArranjoPagamento": arranjos,
            "documentoUsuarioFinalRecebedor": optin["documento_ufr"],
            "documentoTitular": optin["documento_titular"],
            "dataInicio": str(vigencia_inicio),
            "dataFim": str(vigencia_fim),
        },
    }

    try:
        resposta = atualizar_optin_cerc(request.financiador_id, optin["protocolo_cerc"], payload_cerc, correlacao_id=optin["referencia_externa"])
    except Exception as exc:  # noqa: BLE001 - mesmo tratamento uniforme de Task 7
        logger.warning("falha ao atualizar optin %s na CERC: %s", optin["referencia_externa"], exc)
        return _erro_json("CERC_INDISPONIVEL", "falha ao atualizar opt-in na CERC", 502)

    item = correlacionar_por_referencia(resposta, optin["referencia_externa"])
    resultado = interpretar_item_opt_in(item)

    if resultado.status_local != "ATIVO":
        return _erro_json(resultado.erro_codigo or "REJEITADO", resultado.erro_mensagem or "atualização rejeitada pela CERC", 422)

    if "arranjos" in payload:
        repository.atualizar_arranjos(request.financiador_id, optin_id, arranjos)
    if "credenciadoras" in payload:
        repository.atualizar_credenciadoras(request.financiador_id, optin_id, credenciadoras)

    optin_final = repository.atualizar_campos(request.financiador_id, optin_id, {
        "vigencia_fim": vigencia_fim,
        "carteira": payload.get("carteira", optin.get("carteira")),
        "cnpj_financiador": payload.get("cnpjFinanciador", optin["cnpj_financiador"]),
    })
    return JsonResponse(_serializar_optin(optin_final))


def optin_detail(request, optin_id):
    if request.method == "GET":
        return detalhar_optin(request, optin_id)
    if request.method == "PATCH":
        return atualizar_optin_view(request, optin_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)


def optins_collection(request):
    if request.method == "POST":
        return criar_optin(request)
    if request.method == "GET":
        return listar_optins(request)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
