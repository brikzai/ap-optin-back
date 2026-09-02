import json
import os

import pytest
import sqlalchemy

import shared.cloudsql_client as cloudsql_client_module
import shared.tenant_config as tenant_config_module
from apps.tenants import provisioning
from apps.tenants.provisioning import BancoJaExiste, TenantInfoDivergente
from apps.tenants.registry import RegistroTenantsInvalido
from apps.tenants.tests.conftest import _engine_admin, _url_para

CNPJ_A = "11111111000191"
CNPJ_B = "22222222000191"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()
    yield
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()


def _configura(monkeypatch, cnpj: str, banco: str):
    monkeypatch.setenv(f"TENANT_{cnpj}_CONFIG", json.dumps({"database_url": _url_para(banco)}))


def _dropa(nome: str):
    admin = _engine_admin()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
    admin.dispose()


def test_provisionar_cria_banco_tenant_info_e_aplica_baseline(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    _dropa(f"ap_{CNPJ_A}")
    try:
        aplicadas = provisioning.provisionar(CNPJ_A)
        assert aplicadas == ["0001_baseline.sql"]
        engine = sqlalchemy.create_engine(_url_para(f"ap_{CNPJ_A}"))
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT financiador_id FROM tenant_info").scalar() == CNPJ_A
            assert conn.exec_driver_sql("SELECT to_regclass('public.optin')").scalar() is not None
        engine.dispose()
    finally:
        _dropa(f"ap_{CNPJ_A}")


def test_provisionar_recusa_banco_existente_sem_flag(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    _dropa(f"ap_{CNPJ_A}")
    try:
        provisioning.provisionar(CNPJ_A)
        with pytest.raises(BancoJaExiste):
            provisioning.provisionar(CNPJ_A)
        assert provisioning.provisionar(CNPJ_A, existente=True) == []  # idempotente
    finally:
        _dropa(f"ap_{CNPJ_A}")


def test_provisionar_recusa_config_com_nome_de_banco_errado(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, "banco_livre")
    with pytest.raises(RegistroTenantsInvalido):
        provisioning.provisionar(CNPJ_A)


def test_provisionar_recusa_cnpj_fora_de_tenant_ids(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_B)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    with pytest.raises(RegistroTenantsInvalido):
        provisioning.provisionar(CNPJ_A)


def test_provisionar_recusa_colisao_com_banco_de_outro_tenant_ja_registrado(monkeypatch):
    # Guarda de colisão (registry.detectar_colisao) exercitada de ponta a ponta
    # dentro de provisionar — não só isoladamente em test_registry.py.
    #
    # Subtileza: validar_config roda ANTES de detectar_colisao dentro de
    # provisionar, e validar_config rejeita qualquer config cujo banco não
    # seja ap_<próprio cnpj>. Por isso o cenário não pode ser "B aponta pro
    # banco de A" — validar_config(B, ...) já rejeitaria isso sem nunca
    # chegar em detectar_colisao. Em vez disso: o tenant sendo provisionado
    # (A) tem config corretamente nomeada (ap_<CNPJ_A>, passa validar_config);
    # é um OUTRO tenant já registrado (B) que tem sua config apontando pro
    # MESMO banco de A. detectar_colisao é o único guard capaz de pegar isso.
    monkeypatch.setenv("TENANT_IDS", f"{CNPJ_A},{CNPJ_B}")
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    _configura(monkeypatch, CNPJ_B, f"ap_{CNPJ_A}")  # mesmo banco de A — colisão
    with pytest.raises(RegistroTenantsInvalido) as exc:
        provisioning.provisionar(CNPJ_A)
    assert CNPJ_A in str(exc.value) and CNPJ_B in str(exc.value) and "mesmo banco" in str(exc.value)


def test_garantir_tenant_info_recusa_outro_dono(banco_descartavel):
    engine, _ = banco_descartavel
    provisioning.garantir_tenant_info(engine, CNPJ_A)
    provisioning.garantir_tenant_info(engine, CNPJ_A)  # idempotente
    with pytest.raises(TenantInfoDivergente):
        provisioning.garantir_tenant_info(engine, CNPJ_B)


def test_provisionar_usa_create_engine_para_a_config_admin(monkeypatch):
    # Regressão do bug crítico: o engine ADMIN tem que passar por _create_engine.
    # Se alguém voltar a construí-lo com sqlalchemy.create_engine(config["database_url"]),
    # o caminho Cloud SQL (ADMIN_DB_CONFIG sem database_url) quebra com KeyError em
    # homolog/produção — e este teste falha.
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    _dropa(f"ap_{CNPJ_A}")

    vistos = []
    real = provisioning._create_engine

    def espiao(config):
        vistos.append(config)
        return real(config)

    monkeypatch.setattr(provisioning, "_create_engine", espiao)
    try:
        provisioning.provisionar(CNPJ_A)
    finally:
        _dropa(f"ap_{CNPJ_A}")

    assert vistos, "_create_engine nunca foi chamado"
    assert vistos[0] == provisioning.config_admin(), (
        "a primeira chamada de _create_engine deve ser a config admin — "
        "o engine admin não pode ser construído fora de _create_engine"
    )
