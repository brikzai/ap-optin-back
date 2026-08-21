# optin/shared/tests/test_cloudsql_client.py
from dotenv import load_dotenv
load_dotenv()

import os
import pytest

os.environ.setdefault("LOCAL_DATABASE_URL", "postgresql+pg8000://optin:optin@localhost:5433/optin")

from shared.cloudsql_client import get_db  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_dominio_arranjo():
    db = get_db()
    db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    yield
    db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()


def test_insert_select_update_delete_round_trip():
    db = get_db()

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
