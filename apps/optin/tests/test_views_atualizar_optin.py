import json

import httpx
import respx
from dotenv import load_dotenv
load_dotenv()

from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _criar_ativo():
    import datetime

    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"], "arranjos": ["VCC"],
    })
    return repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")


@respx.mock
def test_atualizar_optin_sucesso(client, auth_headers):
    _limpar()
    try:
        optin = _criar_ativo()
        respx.post("https://api.int.cerc.com/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
            return_value=httpx.Response(207, json=[{"protocolo": "P-1", "referenciaExterna": optin["referencia_externa"], "status": "0", "erros": []}])
        )

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({"vigenciaFim": "2028-01-01"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-update-1",
            **auth_headers,
        )

        assert response.status_code == 200
        assert json.loads(response.content)["vigenciaFim"] == "2028-01-01"
    finally:
        _limpar()


def test_atualizar_optin_rejeita_campo_nao_atualizavel_sem_chamar_cerc(client, auth_headers):
    _limpar()
    try:
        optin = _criar_ativo()

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({"referenciaExterna": "OUTRA"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-update-2",
            **auth_headers,
        )
        assert response.status_code == 422
    finally:
        _limpar()


def test_atualizar_optin_404_quando_nao_existe(client, auth_headers):
    response = client.patch(
        "/api/v1/optins/opt_inexistente",
        data=json.dumps({"vigenciaFim": "2028-01-01"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-update-3",
        **auth_headers,
    )
    assert response.status_code == 404
