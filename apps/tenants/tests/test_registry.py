import json

import pytest

import shared.tenant_config as tenant_config_module
from apps.tenants import registry
from apps.tenants.registry import RegistroTenantsInvalido


@pytest.fixture(autouse=True)
def _sem_gcp_e_sem_cache(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    tenant_config_module._cache.clear()
    yield
    tenant_config_module._cache.clear()


def _config_local(banco: str) -> str:
    return json.dumps({"database_url": f"postgresql+pg8000://u:p@localhost:5432/{banco}"})


def test_tenant_ids_le_lista_sem_espacos_nem_duplicatas(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", " 12345678000199, 99999999000191 ,12345678000199")
    assert registry.tenant_ids() == ["12345678000199", "99999999000191"]


def test_tenant_ids_vazio_e_erro(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", " , ")
    with pytest.raises(RegistroTenantsInvalido):
        registry.tenant_ids()


def test_nome_banco_segue_convencao():
    assert registry.nome_banco("12345678000199") == "ap_12345678000199"


@pytest.mark.parametrize("ruim", ["123", "1234567800019A", "ap_12345678000199", ""])
def test_nome_banco_rejeita_cnpj_invalido(ruim):
    with pytest.raises(RegistroTenantsInvalido):
        registry.nome_banco(ruim)


def test_nome_banco_da_config_database_url_e_cloudsql():
    assert registry.nome_banco_da_config({"database_url": "postgresql+pg8000://u:p@h/ap_1"}) == "ap_1"
    assert registry.nome_banco_da_config({"cloudsql_connection_name": "p:r:i", "cloudsql_db_name": "ap_2"}) == "ap_2"


def test_validar_config_aceita_banco_correto():
    registry.validar_config("12345678000199", {"database_url": "postgresql+pg8000://u:p@h/ap_12345678000199"})


def test_validar_config_rejeita_banco_de_outro_tenant():
    with pytest.raises(RegistroTenantsInvalido):
        registry.validar_config("12345678000199", {"database_url": "postgresql+pg8000://u:p@h/ap_99999999000191"})


def test_detectar_colisao_encontra_outro_tenant_no_mesmo_banco(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", "12345678000199,99999999000191")
    monkeypatch.setenv("TENANT_12345678000199_CONFIG", _config_local("ap_12345678000199"))
    monkeypatch.setenv("TENANT_99999999000191_CONFIG", _config_local("ap_12345678000199"))  # errado de propósito
    config = json.loads(_config_local("ap_12345678000199"))
    assert registry.detectar_colisao("12345678000199", config) == "99999999000191"


def test_detectar_colisao_none_quando_bancos_distintos(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", "12345678000199,99999999000191")
    monkeypatch.setenv("TENANT_12345678000199_CONFIG", _config_local("ap_12345678000199"))
    monkeypatch.setenv("TENANT_99999999000191_CONFIG", _config_local("ap_99999999000191"))
    config = json.loads(_config_local("ap_12345678000199"))
    assert registry.detectar_colisao("12345678000199", config) is None
