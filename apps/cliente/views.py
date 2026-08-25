import json

from django.http import JsonResponse

from apps.cliente import repository
from apps.optin.validation import ValidationError, validar_documento
from shared.jwt_auth import jwt_required


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
        "criadoEm": cliente["criado_em"].isoformat() if hasattr(cliente["criado_em"], "isoformat") else cliente["criado_em"],
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

    if repository.buscar_por_documento(request.financiador_id, documento):
        return _erro_json("CLIENTE_JA_CADASTRADO", "já existe cliente cadastrado com esse documento", 409)

    cliente = repository.criar(request.financiador_id, {
        "documento": documento,
        "documento_tipo": tipo,
        "nome": nome,
        "email": payload.get("email"),
        "telefone": payload.get("telefone"),
    })
    return JsonResponse(_serializar_cliente(cliente), status=201)


@jwt_required
def listar_clientes(request):
    filtros = {"documento": request.GET.get("documento")}
    limit = min(int(request.GET.get("limit", 50)), 200)
    resultado = repository.listar(request.financiador_id, filtros, limit)
    return JsonResponse({"dados": [_serializar_cliente(c) for c in resultado]})


@jwt_required
def detalhar_cliente(request, cliente_id):
    cliente = repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)
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
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
