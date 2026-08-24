from dotenv import load_dotenv
load_dotenv()

import sqlalchemy

from shared.cloudsql_client import get_db

FINANCIADOR_TESTE = "12345678000199"


def test_idempotency_key_table_round_trip():
    db = get_db(FINANCIADOR_TESTE)
    db.table("idempotency_key").delete().eq("chave", "test-key-plan08").execute()

    inserted = db.table("idempotency_key").insert({
        "recurso": "optin_create",
        "chave": "test-key-plan08",
        "http_status": 201,
        "response_body": {"id": "opt_1"},
    }).execute()
    assert inserted.data[0]["chave"] == "test-key-plan08"

    found = db.table("idempotency_key").select("*").eq("chave", "test-key-plan08").execute()
    assert found.data[0]["response_body"] == {"id": "opt_1"}
    assert found.data[0]["recurso"] == "optin_create"

    db.table("idempotency_key").delete().eq("chave", "test-key-plan08").execute()


def test_referencia_sequences_increment():
    db = get_db(FINANCIADOR_TESTE)
    with db._engine.connect() as conn:
        primeiro = conn.execute(sqlalchemy.text("SELECT nextval('optin_referencia_seq')")).scalar()
        segundo = conn.execute(sqlalchemy.text("SELECT nextval('optin_referencia_seq')")).scalar()
    assert segundo == primeiro + 1
