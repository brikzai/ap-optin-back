import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

import shared.cloudsql_client as cloudsql_client_module
import shared.tenant_config as tenant_config_module
from apps.tenants.tests.conftest import _engine_admin, _url_para

CNPJ = "33333333000191"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("TENANT_IDS", CNPJ)
    monkeypatch.setenv(f"TENANT_{CNPJ}_CONFIG", json.dumps({"database_url": _url_para(f"ap_{CNPJ}")}))
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()
    yield
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()
    admin = _engine_admin()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "ap_{CNPJ}" WITH (FORCE)')
    admin.dispose()


def test_provisionar_depois_migrate_e_seed(monkeypatch):
    out = StringIO()
    call_command("provisionar_tenant", CNPJ, stdout=out)
    assert "0001_baseline.sql" in out.getvalue()

    out = StringIO()
    call_command("migrate_tenants", stdout=out)
    assert f"ap_{CNPJ}: nada pendente" in out.getvalue()

    out = StringIO()
    call_command("migrate_tenants", "--dry-run", stdout=out)
    assert f"ap_{CNPJ}: nada pendente" in out.getvalue()

    call_command("seed_dominio_arranjo", "--tenant", CNPJ, stdout=StringIO())
    call_command("seed_dominio_arranjo", "--tenant", CNPJ, stdout=StringIO())  # idempotente
    db = cloudsql_client_module.get_db(CNPJ)
    assert db.table("dominio_arranjo").select("codigo").eq("codigo", "99T").execute().data == [{"codigo": "99T"}]


def test_provisionar_duas_vezes_falha_sem_existente():
    call_command("provisionar_tenant", CNPJ, stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("provisionar_tenant", CNPJ, stdout=StringIO())
    call_command("provisionar_tenant", CNPJ, "--existente", stdout=StringIO())


def test_migrate_tenants_falha_se_tenant_nao_provisionado():
    with pytest.raises(CommandError):
        call_command("migrate_tenants", stdout=StringIO())
