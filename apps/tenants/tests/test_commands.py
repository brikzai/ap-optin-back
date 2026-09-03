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
    # 99T (curinga interno) + os 47 códigos oficiais da CERC.
    assert db.table("dominio_arranjo").select("codigo", count="exact").execute().count == 48
    assert db.table("dominio_arranjo").select("codigo").eq("codigo", "VCC").execute().data == [{"codigo": "VCC"}]


def test_provisionar_duas_vezes_falha_sem_existente():
    call_command("provisionar_tenant", CNPJ, stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("provisionar_tenant", CNPJ, stdout=StringIO())
    call_command("provisionar_tenant", CNPJ, "--existente", stdout=StringIO())


def test_migrate_tenants_falha_se_tenant_nao_provisionado():
    with pytest.raises(CommandError):
        call_command("migrate_tenants", stdout=StringIO())


def test_migrate_tenants_isola_falha_de_um_tenant(monkeypatch):
    # Contrato da spec §4.3: um tenant quebrado não impede os outros, e o comando
    # ainda termina com erro nomeando quem falhou. O CNPJ ruim vem PRIMEIRO na lista
    # justamente para provar que o loop continua depois da falha.
    call_command("provisionar_tenant", CNPJ, stdout=StringIO())  # TENANT_IDS ainda limpo (fixture)

    cnpj_ruim = "123"
    monkeypatch.setenv("TENANT_IDS", f"{cnpj_ruim},{CNPJ}")
    tenant_config_module._cache.clear()

    out, err = StringIO(), StringIO()
    with pytest.raises(CommandError) as exc:
        call_command("migrate_tenants", stdout=out, stderr=err)

    assert cnpj_ruim in str(exc.value)                       # nomeia o que falhou
    assert cnpj_ruim in err.getvalue()                       # logou o erro do ruim
    assert f"ap_{CNPJ}: nada pendente" in out.getvalue()     # e AINDA processou o bom


def test_migrate_tenants_dry_run_com_migration_pendente_nao_escreve(monkeypatch):
    # Banco criado e com tenant_info, mas sem migrations: 0001 fica pendente.
    # Prova o ramo "seria aplicada" e que o dry-run não escreve nada no banco.
    from apps.tenants import provisioning
    from shared.cloudsql_client import _create_engine
    from shared.tenant_config import get_tenant_config

    admin = _engine_admin()
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "ap_{CNPJ}"')
    finally:
        admin.dispose()

    engine = _create_engine(get_tenant_config(CNPJ))
    try:
        provisioning.garantir_tenant_info(engine, CNPJ)
    finally:
        engine.dispose()

    out = StringIO()
    call_command("migrate_tenants", "--dry-run", stdout=out)
    assert "0001_baseline.sql seria aplicada" in out.getvalue()

    engine = _create_engine(get_tenant_config(CNPJ))
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT to_regclass('public.optin')").scalar() is None
            assert conn.exec_driver_sql("SELECT to_regclass('public.schema_aplicado')").scalar() is None
    finally:
        engine.dispose()
