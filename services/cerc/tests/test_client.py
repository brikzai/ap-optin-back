from dotenv import load_dotenv
load_dotenv()

import json
import os

import httpx
import pytest
import respx

os.environ.setdefault("CERC_AUTH_URL", "https://api.int.cerc.com/oauth/token")
os.environ.setdefault("CERC_API_BASE_URL", "https://ap-homolog.cerc.inf.br")

FINANCIADOR_TESTE = "12345678000199"

from services.cerc import client, token_provider  # noqa: E402
from shared.cloudsql_client import get_db  # noqa: E402


def _mock_token():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )


def _multistatus(protocolo="P-1", referencia="OPTIN-2026-000001", status="0"):
    return [
        {
            "protocolo": protocolo,
            "referenciaExterna": referencia,
            "dataHoraProcessamento": "2026-08-17T12:00:00.000Z",
            "status": status,
            "erros": [],
        }
    ]


@pytest.fixture(autouse=True)
def _reset_state():
    token_provider._caches.clear()
    token_provider._locks.clear()
    import shared.tenant_config as tenant_config_module
    tenant_config_module._cache.clear()

    db = get_db(FINANCIADOR_TESTE)
    db.table("cerc_requisicao").delete().eq("correlacao_id", "corr-1").execute()
    yield
    db.table("cerc_requisicao").delete().eq("correlacao_id", "corr-1").execute()
    tenant_config_module._cache.clear()


@respx.mock
def test_registrar_optin_sends_array_body_with_tipo_operacao_c():
    _mock_token()
    route = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(207, json=_multistatus())
    )

    result = client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert result == _multistatus()
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == [{"cnpjFinanciador": "12345678000199", "tipoOperacao": "C"}]

    logged = get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] == 207
    assert logged.data[0]["recurso"] == "/opt_in"
    assert logged.data[0]["tentativa"] == 1


@respx.mock
def test_registrar_optin_retries_once_on_401():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "tok-expired", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "tok-fresh", "expires_in": 3600}),
        ]
    )
    opt_in_route = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        side_effect=[
            httpx.Response(401, json={"erro": "token expirado"}),
            httpx.Response(207, json=_multistatus()),
        ]
    )

    result = client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert result == _multistatus()
    assert opt_in_route.call_count == 2

    logged = (
        get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*")
        .eq("correlacao_id", "corr-1").order("tentativa").execute()
    )
    assert [row["tentativa"] for row in logged.data] == [1, 2]
    assert logged.data[0]["http_status"] == 401
    assert logged.data[1]["http_status"] == 207


@respx.mock
def test_registrar_optin_raises_cerc_api_error_on_4xx():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(422, json={"codigo": "104804", "mensagem": "duplicado"})
    )

    with pytest.raises(client.CercApiError) as exc:
        client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert exc.value.status_code == 422
    assert exc.value.body == {"codigo": "104804", "mensagem": "duplicado"}

    logged = get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] == 422


@respx.mock
def test_registrar_optin_logs_before_raising_on_transport_failure():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with pytest.raises(httpx.ConnectError):
        client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    logged = get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] is None
    assert logged.data[0]["tentativa"] == 1


@respx.mock
def test_atualizar_optin_calls_opt_in_with_tipo_operacao_a_e_protocolo():
    _mock_token()
    route = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(207, json=_multistatus(status="0"))
    )

    result = client.atualizar_optin(FINANCIADOR_TESTE, "P-1", {"vigenciaFim": "2027-01-01"}, correlacao_id="corr-1")

    assert result[0]["status"] == "0"
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == [{"vigenciaFim": "2027-01-01", "tipoOperacao": "A", "protocolo": "P-1"}]


@respx.mock
def test_encerrar_optin_sends_array_body_to_opt_out():
    _mock_token()
    route = respx.post("https://ap-homolog.cerc.inf.br/opt_out").mock(
        return_value=httpx.Response(207, json=_multistatus(status="0"))
    )

    result = client.encerrar_optin(FINANCIADOR_TESTE, "P-1", {"referenciaExterna": "OPTOUT-2026-000001"}, correlacao_id="corr-1")

    assert result[0]["status"] == "0"
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == [{"referenciaExterna": "OPTOUT-2026-000001", "protocoloOptIn": "P-1"}]
