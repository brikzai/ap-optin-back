import json

from dotenv import load_dotenv
load_dotenv()

from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"

from apps.cliente import repository as cliente_repository


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _criar_ativo():
    import datetime

    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cliente_id": _cliente_id_teste(),
        "cnpj_solicitante": "12345678000199",
        "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR,
        "documento_ufr_tipo": "CNPJ",
        "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10),
        "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10),
        "carteira": None,
        "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"],
        "arranjos": ["VCC"],
    })
    return repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")


def test_detalhar_optin_retorna_200(client, auth_headers):
    _limpar()
    try:
        optin = _criar_ativo()

        response = client.get(f"/api/v1/optins/{optin['id']}", **auth_headers)
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["id"] == optin["id"]
        assert body["status"] == "ATIVO"
        assert body["usuarioFinalRecebedor"] == DOC_UFR
    finally:
        _limpar()


def test_detalhar_optin_404_quando_nao_existe(client, auth_headers):
    response = client.get("/api/v1/optins/opt_inexistente", **auth_headers)
    assert response.status_code == 404
    assert json.loads(response.content)["erro"] == "OPTIN_NAO_ENCONTRADO"


def test_listar_optins_filtra_por_status(client, auth_headers):
    _limpar()
    try:
        optin = _criar_ativo()

        response = client.get(f"/api/v1/optins?status=ATIVO&usuarioFinalRecebedor={DOC_UFR}", **auth_headers)
        assert response.status_code == 200
        ids = [item["id"] for item in json.loads(response.content)["dados"]]
        assert optin["id"] in ids

        # Filtro com status que não bate não deve devolver o mesmo optin. Sem esta segunda
        # chamada, uma implementação que ignorasse os filtros (devolvendo todos os optins do
        # financiador_id) passaria igualmente na asserção acima — este segundo request garante
        # que o filtro por status é de fato aplicado na query, não apenas aceito e descartado.
        response_sem_match = client.get(f"/api/v1/optins?status=PENDENTE&usuarioFinalRecebedor={DOC_UFR}", **auth_headers)
        assert response_sem_match.status_code == 200
        ids_sem_match = [item["id"] for item in json.loads(response_sem_match.content)["dados"]]
        assert optin["id"] not in ids_sem_match
    finally:
        _limpar()


def test_listar_optins_sem_jwt_retorna_401(client):
    response = client.get("/api/v1/optins")
    assert response.status_code == 401
