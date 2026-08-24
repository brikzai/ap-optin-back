import json

import httpx
import pytest
import respx
from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"
CORPO_VALIDO = {
    "usuarioFinalRecebedor": DOC_UFR,
    "credenciadoras": ["99T"],
    "arranjos": ["VCC"],
    "vigenciaInicio": "2026-08-11",
    "vigenciaFim": "2027-08-10",
    "dataAssinatura": "2026-08-10",
    "evidenciaAutorizacaoId": "doc_teste",
}


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _limpar_idempotencia(chave: str):
    # Diferente dos demais testes deste arquivo (que só verificam status HTTP/corpo,
    # sempre estáveis mesmo se a resposta vier do cache de idempotência), estes dois
    # testes verificam o estado gravado no banco (status FALHA_ENVIO/REJEITADO na
    # tabela optin). Sem limpar a chave de idempotência, uma reexecução no mesmo banco
    # (Cloud SQL real, não efêmero) faria o decorator `idempotente` devolver a resposta
    # cacheada sem chamar a view de novo — nenhuma linha optin seria recriada e o
    # teste falharia com uma contagem de linhas errada, não com o bug que deveria pegar.
    get_db(FINANCIADOR_TESTE).table("idempotency_key").delete().eq("recurso", "optin_create").eq("chave", chave).execute()


@pytest.fixture(autouse=True)
def _seed_dominio_arranjo():
    get_db(FINANCIADOR_TESTE).table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    get_db(FINANCIADOR_TESTE).table("dominio_arranjo").insert({
        "codigo": "VCC", "descricao": "Visa Crédito", "ativo": True, "atualizado_em": "2026-01-01T00:00:00-03:00",
    }).execute()
    _limpar()
    yield
    _limpar()
    get_db(FINANCIADOR_TESTE).table("dominio_arranjo").delete().eq("codigo", "VCC").execute()


@respx.mock
def test_criar_optin_sucesso_retorna_201_ativo(client, auth_headers):
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )

    # A referenciaExterna real é gerada pelo serviço (sequência Postgres), só
    # conhecida depois do insert — o mock ecoa a que veio no corpo da requisição
    # em vez de fixar um valor, para bater com correlacionar_por_referencia().
    def _resposta(request):
        enviado = json.loads(request.content)[0]
        return httpx.Response(207, json=[{
            "protocolo": "P-1", "referenciaExterna": enviado["referenciaExterna"], "status": "0", "erros": [],
        }])

    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(side_effect=_resposta)

    response = client.post(
        "/api/v1/optins",
        data=json.dumps(CORPO_VALIDO),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-criar-1",
        **auth_headers,
    )

    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["status"] == "ATIVO"
    assert body["protocoloCerc"] == "P-1"
    assert body["referenciaExterna"].startswith("OPTIN-")


def test_criar_optin_sem_jwt_retorna_401(client):
    response = client.post("/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json")
    assert response.status_code == 401


def test_criar_optin_sem_idempotency_key_retorna_422(client, auth_headers):
    response = client.post(
        "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers
    )
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL011"


def test_criar_optin_vigencia_invalida_retorna_422(client, auth_headers):
    corpo = {**CORPO_VALIDO, "vigenciaFim": "2026-01-01"}
    response = client.post(
        "/api/v1/optins",
        data=json.dumps(corpo),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-criar-invalida",
        **auth_headers,
    )
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL003"


@respx.mock
def test_criar_optin_duplicado_retorna_409_sem_chamar_cerc(client, auth_headers):
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )

    def _resposta(request):
        enviado = json.loads(request.content)[0]
        return httpx.Response(207, json=[{
            "protocolo": "P-1", "referenciaExterna": enviado["referenciaExterna"], "status": "0", "erros": [],
        }])

    rota_cerc = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(side_effect=_resposta)

    client.post(
        "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-dup-1", **auth_headers,
    )
    chamadas_apos_primeira = rota_cerc.call_count

    response = client.post(
        "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-dup-2", **auth_headers,
    )

    assert response.status_code == 409
    assert rota_cerc.call_count == chamadas_apos_primeira


@respx.mock
def test_criar_optin_falha_transporte_cerc_retorna_502_e_marca_falha_envio(client, auth_headers):
    chave = "key-falha-transporte"
    _limpar_idempotencia(chave)
    try:
        respx.post("https://api.int.cerc.com/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(side_effect=httpx.ConnectError("connection refused"))

        response = client.post(
            "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave, **auth_headers,
        )

        assert response.status_code == 502
        assert json.loads(response.content)["erro"] == "CERC_INDISPONIVEL"

        rows = get_db(FINANCIADOR_TESTE).table("optin").select("*").eq("documento_ufr", DOC_UFR).execute().data
        assert len(rows) == 1
        assert rows[0]["status"] == "FALHA_ENVIO"
    finally:
        _limpar_idempotencia(chave)


@respx.mock
def test_criar_optin_rejeitado_pela_cerc_retorna_422_e_marca_rejeitado(client, auth_headers):
    chave = "key-rejeitado"
    _limpar_idempotencia(chave)
    try:
        respx.post("https://api.int.cerc.com/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )

        def _resposta(request):
            enviado = json.loads(request.content)[0]
            return httpx.Response(207, json=[{
                "referenciaExterna": enviado["referenciaExterna"],
                "status": "1",
                "erros": [{"codigo": "104806", "mensagem": "dataInicio menor que dataAssinaturaOptIn"}],
            }])

        respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(side_effect=_resposta)

        response = client.post(
            "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave, **auth_headers,
        )

        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "104806"

        rows = get_db(FINANCIADOR_TESTE).table("optin").select("*").eq("documento_ufr", DOC_UFR).execute().data
        assert len(rows) == 1
        assert rows[0]["status"] == "REJEITADO"
    finally:
        _limpar_idempotencia(chave)
