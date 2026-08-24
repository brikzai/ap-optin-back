# optin/shared/tests/test_cloudsql_client.py
from dotenv import load_dotenv
load_dotenv()

import json
import os

import pytest

FINANCIADOR_TESTE = "12345678000199"
FINANCIADOR_TESTE_2 = "99999999000191"

from shared.cloudsql_client import get_db  # noqa: E402
import shared.cloudsql_client as cloudsql_client_module  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_dominio_arranjo():
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
    cloudsql_client_module._clients.clear()
    # Aponta o "segundo tenant" para a MESMA config do tenant de teste — o
    # objetivo aqui é provar que o cache é chaveado por financiador_id (dois
    # tenants diferentes nunca compartilham o mesmo CloudSQLClient), não
    # provisionar um segundo Cloud SQL real só para este teste.
    monkeypatch.setenv(
        f"TENANT_{FINANCIADOR_TESTE_2}_CONFIG",
        os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"],
    )

    db1a = get_db(FINANCIADOR_TESTE)
    db1b = get_db(FINANCIADOR_TESTE)
    db2 = get_db(FINANCIADOR_TESTE_2)

    assert db1a is db1b
    assert db1a is not db2

    cloudsql_client_module._clients.pop(FINANCIADOR_TESTE_2, None)
