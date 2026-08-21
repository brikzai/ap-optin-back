import threading

import httpx
import pytest
import respx

from services.cerc import token_provider


@pytest.fixture(autouse=True)
def _reset_cache_and_env(monkeypatch):
    monkeypatch.setenv("CERC_AUTH_URL", "https://api.int.cerc.com/oauth/token")
    monkeypatch.setenv("CERC_CLIENT_ID", "client-123")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("CERC_CLIENT_SECRET", "segredo-local")
    token_provider._cache["access_token"] = None
    token_provider._cache["expires_at"] = 0.0
    yield


@respx.mock
def test_get_cerc_token_fetches_and_caches():
    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )

    token = token_provider.get_cerc_token()
    assert token == "tok-1"
    assert route.call_count == 1

    token_again = token_provider.get_cerc_token()
    assert token_again == "tok-1"
    assert route.call_count == 1  # cached, no second call


@respx.mock
def test_get_cerc_token_refetches_after_80_percent_expiry():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    token_provider.get_cerc_token()

    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600})
    )
    token_provider._cache["expires_at"] = 0.0  # simulate 80%-of-expires_in elapsed

    token = token_provider.get_cerc_token()
    assert token == "tok-2"
    assert route.call_count == 1


@respx.mock
def test_get_cerc_token_single_flight_under_concurrency():
    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )

    results = []

    def _call():
        results.append(token_provider.get_cerc_token())

    threads = [threading.Thread(target=_call) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["tok-1"] * 10
    assert route.call_count == 1


@respx.mock
def test_invalidate_token_forces_refetch():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    token_provider.get_cerc_token()

    token_provider.invalidate_token()

    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600})
    )
    assert token_provider.get_cerc_token() == "tok-2"
    assert route.call_count == 1
