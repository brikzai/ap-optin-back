# optin/shared/tests/test_cloudsql_client.py
from dotenv import load_dotenv
load_dotenv()

import json
import os
import threading
import time

import pytest

FINANCIADOR_TESTE = "12345678000199"
FINANCIADOR_TESTE_2 = "99999999000191"
FINANCIADOR_TESTE_3 = "11111111000100"

from shared.cloudsql_client import get_db  # noqa: E402
import shared.cloudsql_client as cloudsql_client_module  # noqa: E402
import shared.tenant_config as tenant_config_module  # noqa: E402


@pytest.fixture(autouse=True)
def _limpa_caches_de_tenant():
    # Estado global por processo: sem isto, um assert que falha no meio de um teste
    # deixa _clients/_locks sujos e derruba os testes seguintes.
    def limpar():
        for cnpj in (FINANCIADOR_TESTE_2, FINANCIADOR_TESTE_3):
            cloudsql_client_module._clients.pop(cnpj, None)
            cloudsql_client_module._locks.pop(cnpj, None)
        tenant_config_module._cache.pop(FINANCIADOR_TESTE_2, None)
        tenant_config_module._cache.pop(FINANCIADOR_TESTE_3, None)

    limpar()
    yield
    limpar()


@pytest.fixture(autouse=True)
def _clean_dominio_arranjo(request):
    # Skip cleanup for tests marked com sem_banco (não precisam de Postgres rodando)
    if request.node.get_closest_marker("sem_banco"):
        yield
        return
    db = get_db(FINANCIADOR_TESTE)
    db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    yield
    db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()


def test_insert_select_update_delete_round_trip():
    db = get_db(FINANCIADOR_TESTE)

    inserted = db.table("dominio_arranjo").insert({
        "codigo": "VCC",
        "descricao": "Visa Crédito",
        "ativo": True,
        "atualizado_em": "2026-08-19T00:00:00-03:00",
    }).execute()
    assert inserted.data[0]["codigo"] == "VCC"

    found = db.table("dominio_arranjo").select("*").eq("codigo", "VCC").execute()
    assert len(found.data) == 1
    assert found.data[0]["ativo"] is True

    updated = db.table("dominio_arranjo").update({"ativo": False}).eq("codigo", "VCC").execute()
    assert updated.data[0]["ativo"] is False

    deleted = db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    assert len(deleted.data) == 1

    empty = db.table("dominio_arranjo").select("*").eq("codigo", "VCC").execute()
    assert empty.data == []


def test_get_db_cacheia_por_financiador_id(monkeypatch):
    monkeypatch.setenv(f"TENANT_{FINANCIADOR_TESTE_2}_CONFIG", os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"])
    # Engine fake + guarda desligada: o que se prova aqui é só o cache por id.
    monkeypatch.setattr(cloudsql_client_module, "_create_engine", lambda config: object())
    monkeypatch.setattr(cloudsql_client_module, "_verificar_tenant", lambda engine, fid: None)

    db1a = get_db(FINANCIADOR_TESTE)
    db1b = get_db(FINANCIADOR_TESTE)
    db2 = get_db(FINANCIADOR_TESTE_2)

    assert db1a is db1b
    assert db1a is not db2


def test_get_db_recusa_tenant_apontando_para_banco_de_outro(monkeypatch):
    # Reproduz o incidente do HANDOFF (dois tenants no mesmo banco): o banco do
    # tenant de teste tem tenant_info = FINANCIADOR_TESTE; pedir get_db de outro
    # id com a MESMA config tem que explodir, não servir dados alheios.
    monkeypatch.setenv(f"TENANT_{FINANCIADOR_TESTE_2}_CONFIG", os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"])

    with pytest.raises(cloudsql_client_module.TenantMismatchError):
        get_db(FINANCIADOR_TESTE_2)

    assert FINANCIADOR_TESTE_2 not in cloudsql_client_module._clients  # nada cacheado


def test_get_db_single_flight_on_concurrent_first_access(monkeypatch):
    # Reproduz o cenário do finding: duas (aqui, dez) threads tentando
    # get_db() pela primeira vez para o MESMO financiador_id ainda não
    # cacheado, ao mesmo tempo. Sem o lock por-tenant, cada uma chamaria
    # _create_engine (engine + connector reais) e a perdedora vazaria um
    # pool de conexões nunca fechado. Aqui trocamos _create_engine por um
    # fake lento para alargar a janela de corrida e contamos quantas vezes
    # ele é de fato chamado.
    monkeypatch.setenv(
        f"TENANT_{FINANCIADOR_TESTE_3}_CONFIG",
        os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"],
    )

    call_count = 0
    count_lock = threading.Lock()

    def _slow_fake_engine(config):
        nonlocal call_count
        with count_lock:
            call_count += 1
        time.sleep(0.05)  # alarga a janela pra forçar a corrida
        return object()

    monkeypatch.setattr(cloudsql_client_module, "_create_engine", _slow_fake_engine)
    monkeypatch.setattr(cloudsql_client_module, "_verificar_tenant", lambda engine, fid: None)

    results = []

    def _call():
        results.append(get_db(FINANCIADOR_TESTE_3))

    threads = [threading.Thread(target=_call) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count == 1  # engine construído uma única vez
    assert len({id(r) for r in results}) == 1  # todas as threads recebem o mesmo client


def test_gte_lte_filters_range_query():
    db = get_db(FINANCIADOR_TESTE)
    db.table("dominio_arranjo").delete().eq("codigo", "GTE1").execute()
    db.table("dominio_arranjo").delete().eq("codigo", "GTE2").execute()
    try:
        db.table("dominio_arranjo").insert({
            "codigo": "GTE1", "descricao": "A", "ativo": True,
            "atualizado_em": "2026-01-01T00:00:00-03:00",
        }).execute()
        db.table("dominio_arranjo").insert({
            "codigo": "GTE2", "descricao": "B", "ativo": True,
            "atualizado_em": "2026-06-01T00:00:00-03:00",
        }).execute()

        recentes = db.table("dominio_arranjo").select("*").gte(
            "atualizado_em", "2026-03-01T00:00:00-03:00"
        ).eq("ativo", True).execute()
        codigos = {r["codigo"] for r in recentes.data}
        assert "GTE2" in codigos and "GTE1" not in codigos

        antigos = db.table("dominio_arranjo").select("*").lte(
            "atualizado_em", "2026-03-01T00:00:00-03:00"
        ).eq("ativo", True).execute()
        codigos_antigos = {r["codigo"] for r in antigos.data}
        assert "GTE1" in codigos_antigos and "GTE2" not in codigos_antigos
    finally:
        db.table("dominio_arranjo").delete().eq("codigo", "GTE1").execute()
        db.table("dominio_arranjo").delete().eq("codigo", "GTE2").execute()


@pytest.mark.sem_banco
def test_create_engine_usa_database_url_quando_presente():
    # create_engine é lazy: não conecta, só monta a URL — dá pra testar sem banco.
    engine = cloudsql_client_module._create_engine({
        "database_url": "postgresql+pg8000://u:p@localhost:5432/ap_12345678000199",
    })
    assert engine.url.database == "ap_12345678000199"
    assert engine.url.host == "localhost"
