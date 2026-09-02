"""Banco temporário para testar runner/provisionamento sem tocar o tenant de teste."""
import json
import os
import uuid

import pytest
import sqlalchemy
from dotenv import load_dotenv

load_dotenv()


def _engine_admin():
    url = json.loads(os.environ["ADMIN_DB_CONFIG"])["database_url"]
    return sqlalchemy.create_engine(url, isolation_level="AUTOCOMMIT")


def _url_para(nome_banco: str) -> str:
    admin_url = json.loads(os.environ["ADMIN_DB_CONFIG"])["database_url"]
    return sqlalchemy.engine.make_url(admin_url).set(database=nome_banco).render_as_string(hide_password=False)


@pytest.fixture
def banco_descartavel():
    nome = f"test_tmp_{uuid.uuid4().hex[:12]}"
    admin = _engine_admin()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{nome}"')
    engine = sqlalchemy.create_engine(_url_para(nome))
    try:
        yield engine, nome
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{nome}" WITH (FORCE)')
        admin.dispose()
