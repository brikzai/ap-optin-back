import json

from django.http import JsonResponse

from apps.cliente import repository
from apps.optin.validation import ValidationError, normalizar_documento, validar_documento
from shared.jwt_auth import jwt_required

STATUS_VALIDOS = {"active", "inactive", "pending"}


def _erro_json(codigo: str, mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"erro": codigo, "mensagem": mensagem}, status=status)


def _serializar_cliente(cliente: dict) -> dict:
    return {
        "id": cliente["id"],
        "documento": cliente["documento"],
        "documentoTipo": cliente["documento_tipo"],
        "nome": cliente["nome"],
        "email": cliente.get("email"),
        "telefone": cliente.get("telefone"),
        "status": cliente["status"],
        "criadoEm": cliente["criado_em"].isoformat() if hasattr(cliente["criado_em"], "isoformat") else cliente["criado_em"],
        "atualizadoEm": cliente["atualizado_em"].isoformat() if hasattr(cliente["atualizado_em"], "isoformat") else cliente["atualizado_em"],
    }


@jwt_required
def criar_cliente(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    try:
        documento, tipo = validar_documento(payload.get("documento", ""))
    except ValidationError as exc:
        return _erro_json(exc.codigo, exc.mensagem, 422)

    nome = payload.get("nome")
    if not nome:
        return _erro_json("CLI001", "nome é obrigatório", 422)

    status = payload.get("status")
    if status is not None and status not in STATUS_VALIDOS:
        return _erro_json("CLI003", "status inválido", 422)

    if repository.buscar_por_documento(request.financiador_id, documento):
        return _erro_json("CLIENTE_JA_CADASTRADO", "já existe cliente cadastrado com esse documento", 409)

    cliente = repository.criar(request.financiador_id, {
        "documento": documento,
        "documento_tipo": tipo,
        "nome": nome,
        "email": payload.get("email"),
        "telefone": payload.get("telefone"),
        "status": status,
    })
    return JsonResponse(_serializar_cliente(cliente), status=201)


@jwt_required
def listar_clientes(request):
    documento_raw = request.GET.get("documento")
    documento_filtro = documento_raw
    if documento_raw:
        try:
            documento_filtro = normalizar_documento(documento_raw)
        except ValidationError:
            pass
    filtros = {"documento": documento_filtro}
    limit = min(int(request.GET.get("limit", 50)), 200)
    resultado = repository.listar(request.financiador_id, filtros, limit)
    return JsonResponse({"dados": [_serializar_cliente(c) for c in resultado]})


@jwt_required
def detalhar_cliente(request, cliente_id):
    cliente = repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)
    return JsonResponse(_serializar_cliente(cliente))


@jwt_required
def atualizar_cliente(request, cliente_id):
    cliente = repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    status = payload.get("status")
    if status is not None and status not in STATUS_VALIDOS:
        return _erro_json("CLI003", "status inválido", 422)

    campos = {}
    for chave in ("nome", "email", "telefone", "status"):
        if chave in payload:
            campos[chave] = payload[chave]

    if campos:
        cliente = repository.atualizar(request.financiador_id, cliente_id, campos)

    return JsonResponse(_serializar_cliente(cliente))


def clientes_collection(request):
    if request.method == "POST":
        return criar_cliente(request)
    if request.method == "GET":
        return listar_clientes(request)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)


def cliente_detail(request, cliente_id):
    if request.method == "GET":
        return detalhar_cliente(request, cliente_id)
    if request.method == "PATCH":
        return atualizar_cliente(request, cliente_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
