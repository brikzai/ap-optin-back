# optin-service — Plan 02: Local Postgres + SPEC-01 Schema — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Postgres dev environment pre-loaded with the SPEC-01 opt-in schema, so every later plan has real tables to read/write against.

**Architecture:** Plain versioned SQL in `docker/initdb/`, loaded automatically by the official `postgres` Docker image's init-script mechanism. No migration framework (matches house convention — see design doc §2/§3).

**Tech Stack:** Docker Compose, Postgres 16.

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§3). Normative source: `SPEC-01-optin-e-gestao.md` §6 (Modelo de dados). Series: plan 2 of 7.

**Depends on:** `2026-08-19-optin-plan-01-scaffold.md` (repo layout).

## Global Constraints

- Money columns are `NUMERIC(18,2)`; **never** `float`/`double` (SPEC-01 §6, §11.4).
- Tables excluded from this schema on purpose: `consulta_agenda`, `consulta_agenda_ur` (SPEC-01 §4.3/§5.5 — moved to SPEC 03; building them now would be schema with no consumer, per the design doc's YAGNI note).

---

### Task 1: Local Postgres + SPEC-01 schema (DDL)

**Files:**
- Create: `optin/docker-compose.yml`
- Create: `optin/docker/initdb/01-optin-schema.sql`

**Interfaces:**
- Produces: a running local Postgres on `localhost:5433`, database `optin`, with all tables from SPEC-01 §6 (minus `consulta_agenda`/`consulta_agenda_ur`) pre-created: `optin`, `optin_credenciadora`, `optin_arranjo`, `optout`, `cerc_requisicao`, `webhook_inbox`, `dominio_arranjo`. Plan 03's tests connect to this via `LOCAL_DATABASE_URL`.

- [ ] **Step 1: Write `docker/initdb/01-optin-schema.sql`**

```sql
CREATE TABLE optin (
  id                    TEXT PRIMARY KEY,
  referencia_externa    TEXT UNIQUE NOT NULL,
  protocolo_cerc        TEXT UNIQUE,
  origem                TEXT NOT NULL,
  status                TEXT NOT NULL,
  cnpj_solicitante      TEXT NOT NULL,
  cnpj_financiador      TEXT NOT NULL,
  documento_ufr         TEXT NOT NULL,
  documento_ufr_tipo    TEXT NOT NULL,
  documento_titular     TEXT,
  data_assinatura       DATE NOT NULL,
  vigencia_inicio       DATE NOT NULL,
  vigencia_fim          DATE NOT NULL,
  carteira              TEXT,
  evidencia_id          TEXT NOT NULL,
  contrato_id           TEXT,
  criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (vigencia_fim >= vigencia_inicio),
  CHECK (vigencia_inicio >= data_assinatura)
);
CREATE INDEX ON optin (documento_ufr, status);
CREATE INDEX ON optin (vigencia_inicio, vigencia_fim);

CREATE TABLE optin_credenciadora (
  optin_id TEXT REFERENCES optin(id),
  cnpj TEXT,
  PRIMARY KEY (optin_id, cnpj)
);

CREATE TABLE optin_arranjo (
  optin_id TEXT REFERENCES optin(id),
  codigo TEXT,
  PRIMARY KEY (optin_id, codigo)
);

CREATE TABLE optout (
  id                 TEXT PRIMARY KEY,
  optin_id           TEXT NOT NULL REFERENCES optin(id),
  referencia_externa TEXT UNIQUE NOT NULL,
  protocolo_cerc     TEXT,
  status             TEXT NOT NULL,
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cerc_requisicao (
  id                 TEXT PRIMARY KEY,
  recurso            TEXT NOT NULL,
  correlacao_id      TEXT NOT NULL,
  http_status        INT,
  request_body       JSONB NOT NULL,
  response_body      JSONB,
  tentativa          INT NOT NULL DEFAULT 1,
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE webhook_inbox (
  id               TEXT PRIMARY KEY,
  tipo_evento      TEXT NOT NULL,
  data_hora_evento TIMESTAMPTZ NOT NULL,
  payload          JSONB NOT NULL,
  hash_dedupe      TEXT NOT NULL UNIQUE,
  processado_em    TIMESTAMPTZ,
  erro             TEXT,
  recebido_em      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dominio_arranjo (
  codigo        TEXT PRIMARY KEY,
  descricao     TEXT,
  ativo         BOOLEAN NOT NULL DEFAULT true,
  atualizado_em TIMESTAMPTZ NOT NULL
);
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: optin
      POSTGRES_PASSWORD: optin
      POSTGRES_DB: optin
    ports:
      - "5433:5432"
    volumes:
      - ./docker/initdb:/docker-entrypoint-initdb.d
```

- [ ] **Step 3: Start it and verify the schema loaded**

Run: `docker compose up -d postgres` then `docker compose exec postgres psql -U optin -d optin -c "\dt"`
Expected: lists `optin`, `optin_credenciadora`, `optin_arranjo`, `optout`, `cerc_requisicao`, `webhook_inbox`, `dominio_arranjo`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml docker/initdb/01-optin-schema.sql
git commit -m "feat: local Postgres + SPEC-01 schema (opt-in tables)"
```

---

## Self-Review Notes

- **Spec coverage:** SPEC-01 §6, scoped to opt-in tables only (agenda tables deferred to SPEC-03, per design doc) — fully covered.
- **Placeholder scan:** none.
- **Type consistency:** table/column names copied verbatim from SPEC-01 §6 and from the design doc — every later plan's `cloudsql_client.table("...")` calls must match these exact names.

**Next:** `2026-08-19-optin-plan-03-cloudsql-client.md` (data access wrapper).
