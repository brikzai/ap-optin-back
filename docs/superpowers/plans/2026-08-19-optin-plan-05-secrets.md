# optin-service — Plan 05: Secrets Reader — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One function to read a secret (like the CERC `client_secret`) — from Google Secret Manager in deployed environments, from an env var in local dev — so no secret ever needs to be committed in plaintext.

**Architecture:** `shared/secrets.py`, branching on whether `GOOGLE_CLOUD_PROJECT` is set.

**Tech Stack:** google-cloud-secret-manager, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§4, §5). Series: plan 5 of 7.

**Depends on:** `2026-08-19-optin-plan-01-scaffold.md` (repo layout).

## Global Constraints

- `client_secret` and other secrets are **never** logged or committed in plaintext (SPEC-01 §3). `.env` (local, git-ignored) holds real values for dev; `.env.example` holds only keys.

---

### Task 1: `shared/secrets.py`

**Files:**
- Create: `optin/shared/secrets.py`
- Test: `optin/shared/tests/test_secrets.py`

**Interfaces:**
- Produces: `get_secret(name: str) -> str`. Plan 06 (`token_provider`) reads `CERC_CLIENT_SECRET` through this.

- [ ] **Step 1: Write the failing test**

```python
# optin/shared/tests/test_secrets.py
from shared.secrets import get_secret
import pytest


def test_get_secret_reads_env_var_when_no_gcp_project(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("MY_SECRET", "valor-local")
    assert get_secret("MY_SECRET") == "valor-local"


def test_get_secret_raises_when_missing_locally(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("NAO_EXISTE", raising=False)
    with pytest.raises(RuntimeError):
        get_secret("NAO_EXISTE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest shared/tests/test_secrets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.secrets'`

- [ ] **Step 3: Write `shared/secrets.py`**

```python
"""Leitura de segredos — Secret Manager em produção/homolog, env var em dev local.

Dev local: sem GOOGLE_CLOUD_PROJECT setado, lê a env var com o mesmo nome do
segredo (ex.: CERC_CLIENT_SECRET no .env). Em produção/homolog, lê do Secret
Manager do projeto (versão "latest").
"""

import os


def get_secret(name: str) -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        value = os.getenv(name)
        if not value:
            raise RuntimeError(f"Secret '{name}' não configurado (defina a env var localmente)")
        return value

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{project}/secrets/{name}/versions/latest"
    response = client.access_secret_version(name=path)
    return response.payload.data.decode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest shared/tests/test_secrets.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/secrets.py shared/tests/test_secrets.py
git commit -m "feat: secrets reader (Secret Manager / env var fallback)"
```

---

## Self-Review Notes

- **Spec coverage:** design §4/§5 (secret handling) — covered for the local-fallback path. The Secret Manager branch is exercised functionally only against real GCP credentials in homolog/prod (consistent with the design doc's "Homologação/certificação CERC concluída" being a later deployment gate, not a unit-test concern).
- **Placeholder scan:** none.
- **Type consistency:** `get_secret(name: str) -> str` is the exact signature Plan 06 calls.

**Next:** `2026-08-19-optin-plan-06-token-provider.md` (CERC OAuth2 token provider).
