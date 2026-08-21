from dotenv import load_dotenv
load_dotenv()

import os
import httpx
import pytest
import respx

os.environ.setdefault("LOCAL_DATABASE_URL", "postgresql+pg8000://optin:optin@localhost:5433/optin")
os.environ.setdefault("CERC_AUTH_URL", "https://api.int.cerc.com/oauth/token")
os.environ.setdefault("CERC_CLIENT_ID", "client-123")
os.environ.setdefault("CERC_CLIENT_SECRET", "segredo-local")
os.environ.setdefault("CERC_API_BASE_URL", "https://ap-homolog.cerc.inf.br")

from services.cerc import client, token_provider  # noqa: E402
from shared.cloudsql_client import get_db  # noqa: E402


def _mock_token():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )


@pytest.fixture(autouse=True)
def _reset_state():
    token_provider._cache["access_token"] = None
    token_provider._cache["expires_at"] = 0.0
    db = get_db()
    db.table("cerc_requisicao").delete().eq("correlacao_id", "corr-1").execute()
    yield
    db.table("cerc_requisicao").delete().eq("correlacao_id", "corr-1").execute()


@respx.mock
def test_registrar_optin_logs_before_returning():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(201, json={"protocolo": "P-1"})
    )

    result = client.registrar_optin({"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert result == {"protocolo": "P-1"}
    logged = get_db().table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] == 201
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
            httpx.Response(201, json={"protocolo": "P-1"}),
        ]
    )

    result = client.registrar_optin({"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert result == {"protocolo": "P-1"}
    assert opt_in_route.call_count == 2

    logged = get_db().table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").order("tentativa").execute()
    assert [row["tentativa"] for row in logged.data] == [1, 2]
    assert logged.data[0]["http_status"] == 401
    assert logged.data[1]["http_status"] == 201


@respx.mock
def test_registrar_optin_raises_cerc_api_error_on_4xx():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(422, json={"codigo": "104804", "mensagem": "duplicado"})
    )

    with pytest.raises(client.CercApiError) as exc:
        client.registrar_optin({"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert exc.value.status_code == 422
    assert exc.value.body == {"codigo": "104804", "mensagem": "duplicado"}


@respx.mock
def test_atualizar_optin_calls_expected_path():
    _mock_token()
    respx.put("https://ap-homolog.cerc.inf.br/opt_in/P-1").mock(
        return_value=httpx.Response(200, json={"protocolo": "P-1", "status": "ATIVO"})
    )

    result = client.atualizar_optin("P-1", {"vigenciaFim": "2027-01-01"}, correlacao_id="corr-1")
    assert result["status"] == "ATIVO"


@respx.mock
def test_encerrar_optin_calls_expected_path():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_out").mock(
        return_value=httpx.Response(201, json={"protocolo": "P-1", "status": "ENCERRADO"})
    )

    result = client.encerrar_optin("P-1", {"motivo": "solicitado pelo titular"}, correlacao_id="corr-1")
    assert result["status"] == "ENCERRADO"
