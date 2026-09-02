"""Runner de migrations SQL por banco — forward-only, ledger schema_aplicado.

Mesmo ledger (nome, checksum, recusa de arquivo editado) do scripts/apply_schema.py
de ap-back-consulta-agenda/ap-back-contratos; aqui generalizado para N tenants.
Spec: docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md §4.
"""

import hashlib
import logging
import re
from pathlib import Path

import sqlparse
from sqlalchemy import text

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "db" / "migrations"
LEDGER = "schema_aplicado"
_NOME = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")


class MigrationEditada(RuntimeError):
    pass


class NomeMigrationInvalido(RuntimeError):
    pass


def listar_migrations(diretorio: Path) -> list:
    arquivos = sorted(p for p in Path(diretorio).iterdir() if p.is_file())
    for p in arquivos:
        if not _NOME.match(p.name):
            raise NomeMigrationInvalido(f"{p.name}: esperado NNNN_descricao_snake.sql")
    return arquivos


def checksum(conteudo: str) -> str:
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def garantir_ledger(conn) -> None:
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {LEDGER} (
          arquivo     TEXT PRIMARY KEY,
          checksum    TEXT NOT NULL,
          aplicado_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))


def aplicadas(conn) -> dict:
    rows = conn.execute(text(f"SELECT arquivo, checksum FROM {LEDGER}")).all()
    return {arquivo: chk for arquivo, chk in rows}


def _ledger_existe(conn) -> bool:
    return bool(conn.execute(text(f"SELECT to_regclass('public.{LEDGER}') IS NOT NULL")).scalar())


def pendentes(engine, diretorio: Path, criar_ledger: bool = True) -> list:
    with engine.begin() as conn:
        if criar_ledger:
            garantir_ledger(conn)
            ja = aplicadas(conn)
        elif _ledger_existe(conn):
            ja = aplicadas(conn)
        else:
            ja = {}
    restantes = []
    for arquivo in listar_migrations(diretorio):
        chk = checksum(arquivo.read_text(encoding="utf-8"))
        if arquivo.name in ja:
            if ja[arquivo.name] != chk:
                raise MigrationEditada(
                    f"{arquivo.name} já foi aplicado com outro checksum — arquivo aplicado foi editado; "
                    "crie um novo numerado em vez de editar"
                )
            continue
        restantes.append(arquivo)
    return restantes


def aplicar(engine, diretorio: Path, dry_run: bool = False) -> list:
    nomes = []
    for arquivo in pendentes(engine, diretorio, criar_ledger=not dry_run):
        nomes.append(arquivo.name)
        if dry_run:
            continue
        conteudo = arquivo.read_text(encoding="utf-8")
        statements = [s.strip() for s in sqlparse.split(conteudo) if s.strip()]
        with engine.begin() as conn:  # um arquivo = uma transação
            for stmt in statements:
                conn.exec_driver_sql(stmt)
            conn.execute(
                text(f"INSERT INTO {LEDGER} (arquivo, checksum) VALUES (:a, :c)"),
                {"a": arquivo.name, "c": checksum(conteudo)},
            )
        logger.info("[migrate] %s aplicada", arquivo.name)
    return nomes
