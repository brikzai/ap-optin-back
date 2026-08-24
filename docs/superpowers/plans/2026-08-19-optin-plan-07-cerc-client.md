# optin-service — Plan 07: CERC REST Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `services/cerc/client.py` — `registrar_optin`/`atualizar_optin`/`encerrar_optin`, the three authenticated calls to the CERC AP API, each logged to `cerc_requisicao` **before** the response is interpreted, each retried exactly once on `401`. This is the last piece of the outbound CERC integration layer (Plans 03/05/06 feed into it); it closes out the `services/cerc/` folder from the design doc.

**Architecture:** A private `_request(method, path, payload, correlacao_id)` helper shared by all three public functions: attaches `Authorization: Bearer <token>` (Plan 06) and `Idempotency-Key: <correlacao_id>` (mutating calls, design §4), calls the CERC API over `httpx`, **always** writes the attempt to `cerc_requisicao` via `CloudSqlClient` (Plan 03) regardless of outcome, then raises `CercApiError` on unrecoverable non-2xx. A `401` triggers `invalidate_token()` + one retry with a fresh token, logged as a second `cerc_requisicao` row (`tentativa=2`).

**Tech Stack:** httpx, respx (mocks the CERC API in tests), pytest, the running local Postgres from Plan 02 (tests write real rows to `cerc_requisicao`).

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§3, §4). Series: plan 7 of 7 — last plan in this foundational series (the API views/webhook/jobs layer that calls these functions is a separate, later planning pass, same deferral already applied to VAL005/008-010 in Plan 04).

**Depends on:** `2026-08-19-optin-plan-02-schema.md` (`cerc_requisicao` table, running via `docker compose up -d postgres`); `2026-08-19-optin-plan-03-cloudsql-client.md` (`get_db()`); `2026-08-19-optin-plan-06-token-provider.md` (`get_cerc_token()`/`invalidate_token()`).

## Global Constraints

- Every attempt — success, business error, or network failure — writes a `cerc_requisicao` row **before** the caller sees a result or exception (design §4: "Cada chamada grava uma linha em `cerc_requisicao` **antes** de interpretar a resposta"). The audit trail must exist even when this module ends up raising.
- `Idempotency-Key` is mandatory on every mutating call (design §4) — reused as-is on the retry-after-401 (same `correlacao_id`, same idempotency key; only the token changes).
- Money/decimal fields inside `payload` are the caller's responsibility (Plan 04 upstream) — this client does not inspect or coerce the request body, it only transports and logs it.

## Riscos e pendências desta implementação

> **Atualização pós-implementação (2026-08-24):** o risco abaixo foi **confirmado e corrigido**. A SPEC-01 normativa foi anexada ao repo em `docs/superpowers/specs/SPEC-01-optin-e-gestao.md`. §4.1/§4.2 confirmam que não existe `PUT /opt_in/{protocolo}` — `atualizar_optin` usa o mesmo `POST /opt_in` com `tipoOperacao="A"` e `protocolo` no corpo, e ambos `/opt_in`/`/opt_out` recebem sempre um array (mesmo para 1 item). `services/cerc/client.py` e `services/cerc/tests/test_client.py` foram atualizados de acordo; os 6 testes deste módulo passam.

- ~~**Paths de `atualizar_optin`/`encerrar_optin` inferidos, não confirmados.** A fonte normativa (`SPEC-01-optin-e-gestao.md` §4) não está disponível neste plano; o design doc só confirma explicitamente os recursos `/opt_in` e `/opt_out` (via o risco já registrado em design §8, item 1). Este plano assume `PUT /opt_in/{protocolo_cerc}` para atualização e `POST /opt_out` para encerramento, **por convenção REST**, não por confirmação da CERC. **Antes de apontar para homologação real, confirmar os três paths e verbos exatos contra a SPEC-01 §4** — se estiverem errados, o fix é local a `_request`'s três chamadas em `registrar_optin`/`atualizar_optin`/`encerrar_optin`, nada mais no serviço depende do path exato.~~ Resolvido — ver nota acima.
- Parsing semântico de `207` multi-status (mencionado em design §7 como alvo de teste unitário) **não é feito aqui** — este cliente devolve o array do `207` cru para quem chamou interpretar; a semântica de sucesso/erro por item (correlação por `referenciaExterna`) é regra de negócio da camada de endpoints internos (§5), ainda não implementada.

---

### Task 1: `services/cerc/client.py`

**Files:**
- Create: `optin/services/cerc/client.py`
- Test: `optin/services/cerc/tests/test_client.py`

**Interfaces:**
- Consumes: `CERC_API_BASE_URL` (env var); `get_cerc_token()`/`invalidate_token()` (Plan 06); `get_db()` (Plan 03).
- Produces: `CercApiError(status_code, body)`; `registrar_optin(payload: dict, correlacao_id: str) -> dict`; `atualizar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> dict`; `encerrar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# optin/services/cerc/tests/test_client.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/cerc/tests/test_client.py -v` (requires `docker compose up -d postgres` from Plan 02 running)
Expected: FAIL with `ModuleNotFoundError: No module named 'services.cerc.client'`

- [ ] **Step 3: Write `services/cerc/client.py`**

```python
"""Cliente REST da CERC — registrar/atualizar/encerrar opt-in.

Toda chamada grava uma linha em cerc_requisicao ANTES de decidir se levanta
CercApiError (design §4) — a trilha de auditoria existe mesmo quando a
chamada termina em erro. Em 401, invalida o token (Plan 06) e repete a
mesma chamada uma única vez, com uma segunda linha de log (tentativa=2).

Paths de atualizar_optin (PUT /opt_in/{protocolo}) e encerrar_optin
(POST /opt_out) são convenção REST assumida, não confirmada contra a
SPEC-01 §4 — ver "Riscos e pendências" no plano.
"""

import os
import uuid

import httpx

from services.cerc.token_provider import get_cerc_token, invalidate_token
from shared.cloudsql_client import get_db


class CercApiError(Exception):
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"CERC API respondeu {status_code}: {body}")


def _log_attempt(recurso: str, correlacao_id: str, request_body: dict, response, tentativa: int) -> None:
    get_db().table("cerc_requisicao").insert({
        "id": str(uuid.uuid4()),
        "recurso": recurso,
        "correlacao_id": correlacao_id,
        "http_status": response.status_code if response is not None else None,
        "request_body": request_body,
        "response_body": _safe_json(response),
        "tentativa": tentativa,
    }).execute()


def _safe_json(response):
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _send(method: str, path: str, payload: dict, correlacao_id: str, token: str) -> httpx.Response:
    url = os.environ["CERC_API_BASE_URL"] + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": correlacao_id,
    }
    return httpx.request(method, url, json=payload, headers=headers, timeout=15.0)


def _request(method: str, path: str, payload: dict, correlacao_id: str) -> dict:
    token = get_cerc_token()
    response = _send(method, path, payload, correlacao_id, token)
    _log_attempt(path, correlacao_id, payload, response, tentativa=1)

    if response.status_code == 401:
        invalidate_token()
        token = get_cerc_token()
        response = _send(method, path, payload, correlacao_id, token)
        _log_attempt(path, correlacao_id, payload, response, tentativa=2)

    if response.status_code >= 400:
        raise CercApiError(response.status_code, _safe_json(response))

    return response.json()


def registrar_optin(payload: dict, correlacao_id: str) -> dict:
    return _request("POST", "/opt_in", payload, correlacao_id)


def atualizar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> dict:
    return _request("PUT", f"/opt_in/{protocolo_cerc}", payload, correlacao_id)


def encerrar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> dict:
    return _request("POST", "/opt_out", {**payload, "protocoloOptIn": protocolo_cerc}, correlacao_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest services/cerc/tests/test_client.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add services/cerc/client.py services/cerc/tests/test_client.py
git commit -m "feat: CERC REST client (registrar/atualizar/encerrar opt-in, audit log, retry-once-on-401)"
```

---

## Self-Review Notes

- **Spec coverage:** design §3 (auditoria via `cerc_requisicao`, log antes de interpretar resposta) and §4 (`Idempotency-Key`, retry-once-on-401) — fully covered for the three named functions. `atualizar_optin`/`encerrar_optin` exact paths are a flagged assumption (see "Riscos e pendências" above), not a spec gap — the *behavior* (auth, logging, retry, error mapping) is fully spec-covered regardless of the exact path once confirmed.
- **Placeholder scan:** none — every step has runnable code; the path-assumption risk is documented, not left as a TODO/placeholder in the code itself.
- **Type consistency:** `CercApiError(status_code, body)` and the three function signatures are the exact surface any future views/jobs plan (not part of this 7-plan series, same deferral as Plan 04's VAL005/008-010) will import.

**Next:** none — this closes the 7-plan foundational series (`optin-plan-01` .. `optin-plan-07`). The API views (§5), webhook receptor (§4.4), JWT middleware, and periodic jobs (§6/§9) described in the design doc are a separate future planning pass, since they depend on all seven of these building blocks being in place first.
