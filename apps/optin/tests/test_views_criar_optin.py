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
