from pathlib import Path

import pytest
import sqlalchemy

from apps.tenants import runner
from apps.tenants.runner import MigrationEditada, NomeMigrationInvalido


def _escreve(dir_: Path, nome: str, sql: str) -> Path:
    p = dir_ / nome
    p.write_text(sql, encoding="utf-8")
    return p


def test_listar_migrations_ordena_e_valida_nome(tmp_path):
    _escreve(tmp_path, "0002_b.sql", "select 1;")
    _escreve(tmp_path, "0001_a.sql", "select 1;")
    assert [p.name for p in runner.listar_migrations(tmp_path)] == ["0001_a.sql", "0002_b.sql"]


@pytest.mark.parametrize("nome", ["1_a.sql", "0001-a.sql", "0001_A.sql", "0001_a.txt"])
def test_listar_migrations_rejeita_nome_fora_do_padrao(tmp_path, nome):
    _escreve(tmp_path, nome, "select 1;")
    with pytest.raises(NomeMigrationInvalido):
        runner.listar_migrations(tmp_path)


def test_listar_migrations_ignora_markdown(tmp_path):
    _escreve(tmp_path, "0001_a.sql", "select 1;")
    (tmp_path / "README.md").write_text("convenção de nomes", encoding="utf-8")
    assert [p.name for p in runner.listar_migrations(tmp_path)] == ["0001_a.sql"]


def test_listar_migrations_rejeita_numero_duplicado(tmp_path):
    _escreve(tmp_path, "0001_a.sql", "select 1;")
    _escreve(tmp_path, "0001_b.sql", "select 1;")
    with pytest.raises(NomeMigrationInvalido):
        runner.listar_migrations(tmp_path)


def test_aplicar_cria_ledger_e_aplica_em_ordem(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    _escreve(tmp_path, "0001_cria.sql", "CREATE TABLE t (id INT PRIMARY KEY);")
    _escreve(tmp_path, "0002_insere.sql", "INSERT INTO t VALUES (1); INSERT INTO t VALUES (2);")

    aplicadas = runner.aplicar(engine, tmp_path)

    assert aplicadas == ["0001_cria.sql", "0002_insere.sql"]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM t").scalar() == 2
        ledger = conn.exec_driver_sql("SELECT arquivo FROM schema_aplicado ORDER BY arquivo").scalars().all()
    assert ledger == ["0001_cria.sql", "0002_insere.sql"]


def test_aplicar_e_idempotente(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    _escreve(tmp_path, "0001_cria.sql", "CREATE TABLE t (id INT);")
    runner.aplicar(engine, tmp_path)
    assert runner.aplicar(engine, tmp_path) == []


def test_aplicar_recusa_arquivo_editado_apos_aplicado(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    p = _escreve(tmp_path, "0001_cria.sql", "CREATE TABLE t (id INT);")
    runner.aplicar(engine, tmp_path)
    p.write_text("CREATE TABLE t (id INT, extra TEXT);", encoding="utf-8")
    with pytest.raises(MigrationEditada):
        runner.aplicar(engine, tmp_path)


def test_aplicar_faz_rollback_do_arquivo_que_falhou(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    _escreve(tmp_path, "0001_ok.sql", "CREATE TABLE t (id INT);")
    _escreve(tmp_path, "0002_quebra.sql", "INSERT INTO t VALUES (1); INSERT INTO nao_existe VALUES (1);")
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        runner.aplicar(engine, tmp_path)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM t").scalar() == 0  # insert do 0002 desfeito
        ledger = conn.exec_driver_sql("SELECT arquivo FROM schema_aplicado").scalars().all()
    assert ledger == ["0001_ok.sql"]


def test_dry_run_nao_toca_o_banco(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    _escreve(tmp_path, "0001_cria.sql", "CREATE TABLE t (id INT);")
    assert runner.aplicar(engine, tmp_path, dry_run=True) == ["0001_cria.sql"]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT to_regclass('public.t')").scalar() is None
        assert conn.exec_driver_sql("SELECT to_regclass('public.schema_aplicado')").scalar() is None


def test_dry_run_com_ledger_existente_nao_aplica_nada(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    _escreve(tmp_path, "0001_cria.sql", "CREATE TABLE t (id INT);")
    runner.aplicar(engine, tmp_path)                      # cria ledger e aplica
    _escreve(tmp_path, "0002_outra.sql", "CREATE TABLE t2 (id INT);")
    assert runner.aplicar(engine, tmp_path, dry_run=True) == ["0002_outra.sql"]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT to_regclass('public.t2')").scalar() is None
        aplicadas = conn.exec_driver_sql("SELECT arquivo FROM schema_aplicado ORDER BY arquivo").scalars().all()
    assert aplicadas == ["0001_cria.sql"]


def test_split_respeita_dollar_quoting(tmp_path, banco_descartavel):
    engine, _ = banco_descartavel
    _escreve(tmp_path, "0001_fn.sql", """
        CREATE FUNCTION dois() RETURNS INT AS $$ SELECT 1; SELECT 2; $$ LANGUAGE sql;
        CREATE TABLE t (id INT);
    """)
    runner.aplicar(engine, tmp_path)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT dois()").scalar() == 2


def test_baseline_real_aplica_limpo(banco_descartavel):
    engine, _ = banco_descartavel
    assert runner.aplicar(engine, runner.MIGRATIONS_DIR) == ["0001_baseline.sql"]
    with engine.connect() as conn:
        tabelas = set(conn.exec_driver_sql(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ).scalars().all())
    assert {"cliente", "optin", "optin_credenciadora", "optin_arranjo", "optout",
            "cerc_requisicao", "webhook_inbox", "dominio_arranjo", "idempotency_key",
            "schema_aplicado"} <= tabelas
