"""Bootstrap da suíte: tenant de teste provisionado/migrado no Postgres local uma vez por sessão.

Idempotente (provisionar(..., existente=True) + runner com ledger). Se TENANT_IDS não
estiver no .env, a suíte para com instrução — nunca cai num Cloud SQL real (spec §7).
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

FINANCIADOR_TESTE = "12345678000199"


@pytest.fixture(scope="session", autouse=True)
def _tenant_de_teste_provisionado():
    if os.getenv("GOOGLE_CLOUD_PROJECT"):
        pytest.exit("GOOGLE_CLOUD_PROJECT setado — a suíte só roda contra Postgres local (spec 2026-09-02 §7)")
    if FINANCIADOR_TESTE not in (os.getenv("TENANT_IDS") or ""):
        pytest.exit(f"TENANT_IDS sem {FINANCIADOR_TESTE} — configure o .env (ver .env.example)")

    from apps.tenants.provisioning import provisionar

    provisionar(FINANCIADOR_TESTE, existente=True)
