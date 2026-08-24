# optin-service — Plan 03: CloudSqlClient Data Access Wrapper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Supabase/PostgREST-style data access wrapper (`shared/cloudsql_client.py`) over SQLAlchemy — the only way any code in this service touches the database.

**Architecture:** `QueryBuilder` with a chainable `.table(name).select()/.insert()/.update()/.delete()/.eq()/.order()/.limit()/.execute()` API, connecting to local Postgres (dev/test, via `LOCAL_DATABASE_URL`) or Cloud SQL (prod/homolog, via the Cloud SQL Python Connector). Adapted from the pattern already validated in `etl-back-ingestion-main/shared/cloudsql_client.py`, with the RLS/multi-tenant hooks removed (this service has its own dedicated instance — no row-level security needed).

**Tech Stack:** SQLAlchemy 2.x, pg8000, google-cloud-sql-connector, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§2, §3). Series: plan 3 of 7.

**Depends on:** `2026-08-19-optin-plan-01-scaffold.md` (repo layout); `2026-08-19-optin-plan-02-schema.md` (local Postgres with the `dominio_arranjo` table used by this plan's test).

## Global Constraints

- Money columns stay `NUMERIC(18,2)` end to end; this wrapper does no numeric coercion of its own — callers are responsible for passing `decimal.Decimal`, never `float`.
- Secrets (`CLOUDSQL_DB_PASSWORD`) never committed; read from env vars only.

---

### Task 1: `shared/cloudsql_client.py`

**Files:**
- Create: `optin/shared/__init__.py`
- Create: `optin/shared/cloudsql_client.py`
- Test: `optin/shared/tests/__init__.py`
- Test: `optin/shared/tests/test_cloudsql_client.py`

**Interfaces:**
- Consumes: `LOCAL_DATABASE_URL` (dev/test) or `CLOUDSQL_CONNECTION_NAME`/`CLOUDSQL_DB_USER`/`CLOUDSQL_DB_PASSWORD`/`CLOUDSQL_DB_NAME` (Cloud SQL) env vars.
- Produces: `get_db() -> CloudSQLClient | None`; `CloudSQLClient.table(name) -> QueryBuilder`; `QueryBuilder.select()/.insert()/.update()/.delete()/.eq()/.order()/.limit()` (all return `self`, chainable) and `.execute() -> ExecuteResult(data: list[dict], count: int | None)`. Every later plan that touches the database imports `get_db` from here.

- [ ] **Step 1: Write the failing test**

```python
# optin/shared/tests/test_cloudsql_client.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest shared/tests/test_cloudsql_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.cloudsql_client'`

- [ ] **Step 3: Write `shared/__init__.py` and `shared/tests/__init__.py`**

Empty files, both.

- [ ] **Step 4: Write `shared/cloudsql_client.py`**

```python
"""Cliente Cloud SQL — API estilo Supabase/PostgREST sobre SQLAlchemy.

    get_db().table("optin").select("*").eq("status", "ATIVO").limit(10).execute()
    get_db().table("optin").insert({...}).execute()

Sem Django ORM (design §2/§3): DATABASES={} no settings, todo acesso passa
por aqui. Conecta a Postgres local via LOCAL_DATABASE_URL (dev/test) ou a
Cloud SQL via Connector (CLOUDSQL_CONNECTION_NAME) em produção/homolog.
"""

import json
import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class ExecuteResult:
    def __init__(self, data=None, count: Optional[int] = None):
        self.data = data or []
        self.count = count


class QueryBuilder:
    def __init__(self, engine, table_name: str):
        self._engine = engine
        self._table = table_name
        self._select_fields = "*"
        self._count_mode: Optional[str] = None
        self._filters: List[tuple] = []
        self._order_by: List[tuple] = []
        self._limit_val: Optional[int] = None
        self._op = "select"
        self._insert_data = None
        self._update_data: Optional[dict] = None

    def select(self, fields: str = "*", count: Optional[str] = None) -> "QueryBuilder":
        self._select_fields = fields
        self._count_mode = count
        return self

    def eq(self, field: str, value: Any) -> "QueryBuilder":
        self._filters.append(("eq", field, value))
        return self

    def order(self, field: str, desc: bool = False) -> "QueryBuilder":
        self._order_by.append((field, desc))
        return self

    def limit(self, n: int) -> "QueryBuilder":
        self._limit_val = n
        return self

    def insert(self, data) -> "QueryBuilder":
        self._op = "insert"
        self._insert_data = data
        return self

    def update(self, data: dict) -> "QueryBuilder":
        self._op = "update"
        self._update_data = data
        return self

    def delete(self) -> "QueryBuilder":
        self._op = "delete"
        return self

    def execute(self) -> ExecuteResult:
        try:
            return {
                "select": self._exec_select,
                "insert": self._exec_insert,
                "update": self._exec_update,
                "delete": self._exec_delete,
            }[self._op]()
        except Exception:
            logger.exception("[CloudSQL] Erro em %s.%s", self._table, self._op)
            raise

    def _build_where(self):
        if not self._filters:
            return "", {}
        clauses, params = [], {}
        for i, (op, field, val) in enumerate(self._filters):
            pname = f"p{i}"
            if op == "eq":
                clauses.append(f"{field} = :{pname}")
                params[pname] = val
        return "WHERE " + " AND ".join(clauses), params

    @staticmethod
    def _serialize(v: Any) -> Any:
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False, default=str)
        return v

    @staticmethod
    def _deserialize_row(row: dict) -> dict:
        result = {}
        for k, v in row.items():
            if isinstance(v, str) and len(v) > 1 and v[0] in ("{", "["):
                try:
                    result[k] = json.loads(v)
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass
            result[k] = v
        return result

    def _exec_select(self) -> ExecuteResult:
        from sqlalchemy import text

        where, params = self._build_where()
        with self._engine.connect() as conn:
            if self._count_mode == "exact":
                sql = f"SELECT COUNT(*) FROM {self._table} {where}"
                return ExecuteResult(data=[], count=conn.execute(text(sql), params).scalar())

            order_clause = ""
            if self._order_by:
                parts = [f"{f} {'DESC' if d else 'ASC'}" for f, d in self._order_by]
                order_clause = "ORDER BY " + ", ".join(parts)
            limit_clause = f"LIMIT {self._limit_val}" if self._limit_val else ""

            sql = f"SELECT {self._select_fields} FROM {self._table} {where} {order_clause} {limit_clause}"
            result = conn.execute(text(sql), params)
            return ExecuteResult(data=[self._deserialize_row(dict(r._mapping)) for r in result])

    def _exec_insert(self) -> ExecuteResult:
        from sqlalchemy import text

        rows = self._insert_data if isinstance(self._insert_data, list) else [self._insert_data]
        inserted = []
        with self._engine.begin() as conn:
            for row in rows:
                serialized = {k: self._serialize(v) for k, v in row.items()}
                cols = list(serialized.keys())
                placeholders = [f":{c}" for c in cols]
                sql = f"INSERT INTO {self._table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING *"
                result = conn.execute(text(sql), serialized)
                inserted.extend(self._deserialize_row(dict(r._mapping)) for r in result)
        return ExecuteResult(data=inserted)

    def _exec_update(self) -> ExecuteResult:
        from sqlalchemy import text

        serialized = {k: self._serialize(v) for k, v in self._update_data.items()}
        set_clause = ", ".join(f"{k} = :u_{k}" for k in serialized)
        params = {f"u_{k}": v for k, v in serialized.items()}
        where, where_params = self._build_where()
        params.update(where_params)
        sql = f"UPDATE {self._table} SET {set_clause} {where} RETURNING *"
        with self._engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return ExecuteResult(data=[self._deserialize_row(dict(r._mapping)) for r in result])

    def _exec_delete(self) -> ExecuteResult:
        from sqlalchemy import text

        where, params = self._build_where()
        sql = f"DELETE FROM {self._table} {where} RETURNING *"
        with self._engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return ExecuteResult(data=[self._deserialize_row(dict(r._mapping)) for r in result])


class CloudSQLClient:
    def __init__(self, engine):
        self._engine = engine

    def table(self, name: str) -> QueryBuilder:
        return QueryBuilder(self._engine, name)


def _create_engine():
    import sqlalchemy

    local_url = os.getenv("LOCAL_DATABASE_URL")
    if local_url:
        logger.info("[CloudSQL] Engine LOCAL via LOCAL_DATABASE_URL")
        return sqlalchemy.create_engine(local_url, pool_pre_ping=True)

    connection_name = os.getenv("CLOUDSQL_CONNECTION_NAME")
    if not connection_name:
        return None

    from google.cloud.sql.connector import Connector, IPTypes

    connector = Connector()
    db_user = os.getenv("CLOUDSQL_DB_USER", "postgres")
    db_pass = os.getenv("CLOUDSQL_DB_PASSWORD", "")
    db_name = os.getenv("CLOUDSQL_DB_NAME", "postgres")

    def getconn():
        return connector.connect(
            connection_name, "pg8000", user=db_user, password=db_pass, db=db_name, ip_type=IPTypes.PUBLIC,
        )

    logger.info("[CloudSQL] Engine criado para %s", connection_name)
    return sqlalchemy.create_engine(
        "postgresql+pg8000://", creator=getconn, pool_size=5, max_overflow=2, pool_timeout=30, pool_recycle=1800,
    )


_client: Optional[CloudSQLClient] = None


def get_db() -> Optional[CloudSQLClient]:
    global _client
    if _client is not None:
        return _client
    engine = _create_engine()
    if engine is None:
        return None
    _client = CloudSQLClient(engine)
    return _client
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest shared/tests/test_cloudsql_client.py -v` (requires `docker compose up -d postgres` from Plan 02 running)
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shared/__init__.py shared/cloudsql_client.py shared/tests/__init__.py shared/tests/test_cloudsql_client.py
git commit -m "feat: CloudSqlClient data access wrapper (no ORM)"
```

---

## Self-Review Notes

- **Spec coverage:** design §2/§3 (`CloudSqlClient`, no-ORM data access) — fully covered.
- **Placeholder scan:** none.
- **Type consistency:** `get_db()` returns `CloudSQLClient | None`; `.table(name)` returns `QueryBuilder`; `.execute()` returns `ExecuteResult(data: list[dict], count: int | None)` — these exact names/shapes are what every later plan imports and relies on.

**Next:** `2026-08-19-optin-plan-04-validation.md` (local validation rules).
