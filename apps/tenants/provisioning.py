"""Provisionamento de tenant: banco ap_<cnpj> + tenant_info + migrations.

Spec: docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md §3.
tenant_info é identidade de infraestrutura (quem é o dono deste banco) — vive
aqui, não numa migration, porque get_db (§5) precisa dela antes de qualquer schema.
"""

import json
import logging

from sqlalchemy import text

from apps.tenants import registry, runner
from shared.cloudsql_client import _create_engine
from shared.secrets import get_secret
from shared.tenant_config import get_tenant_config

logger = logging.getLogger(__name__)


class BancoJaExiste(RuntimeError):
    pass


class TenantInfoDivergente(RuntimeError):
    pass


def config_admin() -> dict:
    return json.loads(get_secret("ADMIN_DB_CONFIG"))


def banco_existe(conn, nome: str) -> bool:
    return conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": nome}).scalar() is not None


def criar_banco(conn, nome: str) -> None:
    registry.nome_banco(nome.removeprefix("ap_"))  # revalida o formato antes de interpolar
    conn.exec_driver_sql(f'CREATE DATABASE "{nome}"')


def garantir_tenant_info(engine, financiador_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tenant_info (
              financiador_id TEXT PRIMARY KEY,
              criado_em      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        dono = conn.execute(text("SELECT financiador_id FROM tenant_info")).scalar()
        if dono is None:
            conn.execute(
                text("INSERT INTO tenant_info (financiador_id) VALUES (:f) ON CONFLICT (financiador_id) DO NOTHING"),
                {"f": financiador_id},
            )
        elif dono != financiador_id:
            raise TenantInfoDivergente(f"banco já pertence ao tenant {dono}, não a {financiador_id}")


def provisionar(financiador_id: str, existente: bool = False) -> list[str]:
    if financiador_id not in registry.tenant_ids():
        raise registry.RegistroTenantsInvalido(f"{financiador_id} não está em TENANT_IDS")
    config = get_tenant_config(financiador_id)
    registry.validar_config(financiador_id, config)
    colisao = registry.detectar_colisao(financiador_id, config)
    if colisao:
        raise registry.RegistroTenantsInvalido(f"{financiador_id} e {colisao} apontam para o mesmo banco")

    nome = registry.nome_banco(financiador_id)
    # Engine SEMPRE via _create_engine (trata database_url e Cloud SQL Connector),
    # com AUTOCOMMIT porque CREATE DATABASE não roda em transação.
    #
    # UMA única conexão para checar e criar: numa conexão RECICLADA do pool o
    # AUTOCOMMIT não vale a tempo (o pool_pre_ping roda antes) e o CREATE DATABASE
    # falha com 25001 "não pode ser executado dentro de um bloco de transação".
    # Não separe isto em duas conexões.
    engine_admin = _create_engine(config_admin()).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with engine_admin.connect() as conn:
            if banco_existe(conn, nome):
                if not existente:
                    raise BancoJaExiste(f"{nome} já existe (use --existente para reaproveitar)")
            else:
                criar_banco(conn, nome)
                logger.info("[provisionar] banco %s criado", nome)
    finally:
        engine_admin.dispose()

    engine = _create_engine(config)
    try:
        garantir_tenant_info(engine, financiador_id)
        return runner.aplicar(engine, runner.MIGRATIONS_DIR)
    finally:
        engine.dispose()
