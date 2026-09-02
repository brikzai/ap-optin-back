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

    from sqlalchemy.engine import make_url

    from shared.tenant_config import get_tenant_config

    config = get_tenant_config(FINANCIADOR_TESTE)
    url = config.get("database_url")
    if not url or make_url(url).host not in ("localhost", "127.0.0.1", "::1"):
        pytest.exit(
            f"tenant de teste {FINANCIADOR_TESTE} não aponta para um Postgres local — "
            "a suíte é destrutiva e nunca deve rodar contra Cloud SQL (spec §7)"
        )

    from apps.tenants.provisioning import provisionar

    provisionar(FINANCIADOR_TESTE, existente=True)
