# optin-service — Plan 06: CERC OAuth2 Token Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One function, `get_cerc_token()`, that always returns a valid CERC access token — fetched via OAuth2 client-credentials, cached in-process, renewed proactively before it expires, and safe under concurrent callers (single-flight, no duplicate token requests).

**Architecture:** `services/cerc/token_provider.py`. Module-level cache (`access_token`, `expires_at`) guarded by a `threading.Lock`, double-checked locking so the common case (valid cached token) never blocks on the lock. `client_secret` read through Plan 05's `get_secret()`, never logged.

**Tech Stack:** httpx, threading (stdlib), pytest, respx (mocks the CERC token endpoint — no real network calls in tests).

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§4). Series: plan 6 of 7.

**Depends on:** `2026-08-19-optin-plan-01-scaffold.md` (repo layout, `httpx`/`respx` in requirements); `2026-08-19-optin-plan-05-secrets.md` (`get_secret()` for `CERC_CLIENT_SECRET`).

## Global Constraints

- The access token and `client_secret` are **never** logged in plaintext (SPEC-01 §3, design §4).
- Renewal is proactive at **80% of `expires_in`** — a call arriving after that point always triggers a fresh fetch, never returns a token close to expiry (design §4).
- Concurrent callers during a cold/expired cache must **not** each fire their own token request — exactly one HTTP call happens per renewal, the rest wait and reuse its result (single-flight, design §4).
- On `401` from a downstream CERC API call, the caller (Plan 07's `services/cerc/client.py`) invalidates the cache via `invalidate_token()` and retries the original call once — this plan only provides the invalidation hook, the retry-once behavior itself lives in Plan 07.

---

### Task 1: `services/cerc/token_provider.py`

**Files:**
- Create: `optin/services/__init__.py`
- Create: `optin/services/cerc/__init__.py`
- Create: `optin/services/cerc/token_provider.py`
- Test: `optin/services/cerc/tests/__init__.py`
- Test: `optin/services/cerc/tests/test_token_provider.py`

**Interfaces:**
- Consumes: `CERC_AUTH_URL`, `CERC_CLIENT_ID` (env vars); `CERC_CLIENT_SECRET` (via `shared.secrets.get_secret`).
- Produces: `get_cerc_token() -> str`; `invalidate_token() -> None`. Plan 07's `services/cerc/client.py` imports both.

- [ ] **Step 1: Write the failing test**

```python
# optin/services/cerc/tests/test_token_provider.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/cerc/tests/test_token_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.cerc.token_provider'`

- [ ] **Step 3: Write `services/__init__.py`, `services/cerc/__init__.py`, `services/cerc/tests/__init__.py`**

Empty files, all three.

- [ ] **Step 4: Write `services/cerc/token_provider.py`**

```python
"""OAuth2 client-credentials — obtém e cacheia o access token da CERC.

Cache em memória por processo. Renovação proativa a 80% de expires_in (uma
chamada depois desse ponto sempre busca um token novo, nunca devolve um
perto de vencer). Single-flight via threading.Lock com double-checked
locking: o caminho comum (token em cache, ainda válido) nunca bloqueia; só
quem chega com o cache frio/vencido disputa o lock, e só um deles de fato
faz a chamada HTTP — os demais reaproveitam o resultado.

Em 401 numa chamada à API da CERC, quem fez a chamada (services/cerc/client.py,
Plano 07) invalida o cache com invalidate_token() e tenta de novo uma única
vez — o retry em si não é responsabilidade deste módulo.
"""

import os
import threading
import time

import httpx

from shared.secrets import get_secret

_lock = threading.Lock()
_cache = {"access_token": None, "expires_at": 0.0}


def _fetch_token() -> dict:
    response = httpx.post(
        os.environ["CERC_AUTH_URL"],
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["CERC_CLIENT_ID"],
            "client_secret": get_secret("CERC_CLIENT_SECRET"),
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def get_cerc_token() -> str:
    now = time.time()
    if _cache["access_token"] and now < _cache["expires_at"]:
        return _cache["access_token"]

    with _lock:
        now = time.time()
        if _cache["access_token"] and now < _cache["expires_at"]:
            return _cache["access_token"]

        payload = _fetch_token()
        _cache["access_token"] = payload["access_token"]
        _cache["expires_at"] = now + 0.8 * payload["expires_in"]
        return _cache["access_token"]


def invalidate_token() -> None:
    with _lock:
        _cache["access_token"] = None
        _cache["expires_at"] = 0.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest services/cerc/tests/test_token_provider.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add services/__init__.py services/cerc/__init__.py services/cerc/token_provider.py services/cerc/tests/__init__.py services/cerc/tests/test_token_provider.py
git commit -m "feat: CERC OAuth2 token provider (cache, 80% proactive renewal, single-flight)"
```

---

## Self-Review Notes

- **Spec coverage:** design §4 (token cache, 80% renewal, single-flight, invalidate-on-401 hook) — fully covered. The retry-once-on-401 behavior itself is explicitly deferred to Plan 07, which is the actual caller of the CERC API.
- **Placeholder scan:** none — every step has runnable code, tests mock the token endpoint with `respx` (no real network/credentials needed to pass).
- **Type consistency:** `get_cerc_token() -> str` and `invalidate_token() -> None` are the exact names Plan 07 imports.

**Next:** `2026-08-19-optin-plan-07-cerc-client.md` (CERC REST client — registrar/atualizar/encerrar opt-in).
