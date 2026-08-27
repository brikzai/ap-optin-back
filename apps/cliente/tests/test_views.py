import json

from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOCUMENTO_TESTE = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"

CORPO_VALIDO = {
    "documento": DOCUMENTO_TESTE,
    "nome": "Cliente Teste",
    "email": "teste@example.com",
    "telefone": "11999999999",
}


def _limpar():
    get_db(FINANCIADOR_TESTE).table("cliente").delete().eq("documento", DOCUMENTO_TESTE).execute()


def test_criar_cliente_sucesso_retorna_201(client, auth_headers):
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 201
        body = json.loads(response.content)
        assert body["documento"] == DOCUMENTO_TESTE
        assert body["documentoTipo"] == "CNPJ"
        assert body["nome"] == "Cliente Teste"
        assert body["id"].startswith("cli_")
    finally:
        _limpar()


def test_criar_cliente_sem_jwt_retorna_401(client):
    response = client.post("/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json")
    assert response.status_code == 401


def test_criar_cliente_documento_invalido_retorna_422(client, auth_headers):
    corpo = {**CORPO_VALIDO, "documento": "11111111111111"}
    response = client.post(
        "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
    )
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL002"


def test_criar_cliente_sem_nome_retorna_422(client, auth_headers):
    corpo = {"documento": DOCUMENTO_TESTE, "email": None, "telefone": None}
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "CLI001"
    finally:
        _limpar()


def test_criar_cliente_duplicado_retorna_409(client, auth_headers):
    _limpar()
    try:
        client.post("/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers)
        response = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 409
        assert json.loads(response.content)["erro"] == "CLIENTE_JA_CADASTRADO"
    finally:
        _limpar()


def test_listar_clientes_filtra_por_documento(client, auth_headers):
    _limpar()
    try:
        criado_resp = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        criado = json.loads(criado_resp.content)

        response = client.get(f"/api/v1/clientes?documento={DOCUMENTO_TESTE}", **auth_headers)
        assert response.status_code == 200
        ids = [item["id"] for item in json.loads(response.content)["dados"]]
        assert criado["id"] in ids
    finally:
        _limpar()


def test_listar_clientes_filtra_por_documento_mascarado(client, auth_headers):
    _limpar()
    try:
        criado_resp = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        criado = json.loads(criado_resp.content)

        documento_mascarado = "22.751.826/0001-25"
        response = client.get(f"/api/v1/clientes?documento={documento_mascarado}", **auth_headers)
        assert response.status_code == 200
        ids = [item["id"] for item in json.loads(response.content)["dados"]]
        assert criado["id"] in ids
    finally:
        _limpar()


def test_detalhar_cliente_retorna_200(client, auth_headers):
    _limpar()
    try:
        criado_resp = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        criado = json.loads(criado_resp.content)

        response = client.get(f"/api/v1/clientes/{criado['id']}", **auth_headers)
        assert response.status_code == 200
        assert json.loads(response.content)["id"] == criado["id"]
    finally:
        _limpar()


def test_detalhar_cliente_404_quando_nao_existe(client, auth_headers):
    response = client.get("/api/v1/clientes/cli_inexistente", **auth_headers)
    assert response.status_code == 404
    assert json.loads(response.content)["erro"] == "CLIENTE_NAO_ENCONTRADO"


def test_criar_cliente_status_invalido_retorna_422(client, auth_headers):
    corpo = {**CORPO_VALIDO, "status": "banana"}
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "CLI003"
    finally:
        _limpar()


def test_criar_cliente_grava_status_explicito(client, auth_headers):
    corpo = {**CORPO_VALIDO, "status": "active"}
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
        )
        body = json.loads(response.content)
        assert body["status"] == "active"
        assert "atualizadoEm" in body
    finally:
        _limpar()


def test_atualizar_cliente_sucesso_altera_campos(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({"nome": "Nome Editado", "status": "active"}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["nome"] == "Nome Editado"
        assert body["status"] == "active"
        assert body["email"] == CORPO_VALIDO["email"]
    finally:
        _limpar()


def test_atualizar_cliente_ignora_documento_no_payload(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({"documento": "00000000000000", "nome": "Nome Editado"}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["documento"] == DOCUMENTO_TESTE
        assert body["nome"] == "Nome Editado"
    finally:
        _limpar()


def test_atualizar_cliente_404_quando_nao_existe(client, auth_headers):
    response = client.patch(
        "/api/v1/clientes/cli_inexistente",
        data=json.dumps({"nome": "Novo Nome"}),
        content_type="application/json",
        **auth_headers,
    )
    assert response.status_code == 404
    assert json.loads(response.content)["erro"] == "CLIENTE_NAO_ENCONTRADO"


def test_atualizar_cliente_status_invalido_retorna_422(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({"status": "banana"}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "CLI003"
    finally:
        _limpar()


def test_atualizar_cliente_corpo_vazio_nao_muda_nada(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["nome"] == CORPO_VALIDO["nome"]
        assert body["status"] == "pending"
    finally:
        _limpar()
