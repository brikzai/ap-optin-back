# Banco do zero — Plan 01: Código (baseline, runner de migrations, provisionamento, guarda de tenant, testes locais) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trazer a worktree `worktree-optin-plan-10-11` para o `master`, substituir `docker/initdb/*.sql` por `db/migrations/0001_baseline.sql` + runner por tenant (`migrate_tenants`), criar `provisionar_tenant`, colocar a guarda estrutural `tenant_info` em `get_db`, e fazer a suíte inteira rodar contra o PostgreSQL 17 local.

**Architecture:** Novo app Django `apps/tenants/` (sem models) com três módulos puros — `registry.py` (lista/validação de tenants), `runner.py` (aplica `db/migrations/*.sql` com ledger `schema_aplicado`), `provisioning.py` (cria banco `ap_<cnpj>` + `tenant_info` + roda o runner) — e três management commands finos por cima. `shared/cloudsql_client.py` ganha `database_url` na config e a guarda `_verificar_tenant`. Um `conftest.py` na raiz provisiona/migra o tenant de teste uma vez por sessão do pytest.

**Tech Stack:** Django 4.2 (sem ORM), SQLAlchemy 2 Core + pg8000, `sqlparse` (split de statements), PostgreSQL 17 local (serviço Windows `postgresql-x64-17`), pytest + pytest-django.

**Spec:** `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md` (§2–§7). Série: plano 1 de 3 (2 = infra GCP, 3 = primeiro deploy).

## Global Constraints

- Sem Django ORM — `DATABASES = {}`; todo acesso via `shared.cloudsql_client.get_db(financiador_id)`; toda função de repository recebe `financiador_id` como primeiro parâmetro.
- Nome de banco por tenant é **sempre** `ap_<cnpj>` (spec §3.2). Validado por regex `^ap_\d{14}$` antes de qualquer interpolação em SQL.
- Ledger de migrations: tabela `schema_aplicado (arquivo TEXT PK, checksum TEXT NOT NULL, aplicado_em TIMESTAMPTZ)` — mesmo nome/semântica dos irmãos (spec §4.2). Arquivo já aplicado com checksum diferente = erro.
- Migrations forward-only, nome `NNNN_descricao_snake.sql` (4 dígitos), diretório `db/migrations/` na raiz do repo. **Nunca** criar pasta chamada `migrations` dentro de um app Django (o Django a trataria como migrations do ORM).
- Segredos (`TENANT_IDS`, `TENANT_<cnpj>_CONFIG`, `ADMIN_DB_CONFIG`) lidos via `shared.secrets.get_secret` (env var local sem `GOOGLE_CLOUD_PROJECT`; Secret Manager com). Nunca commitar `.env`.
- A suíte automatizada roda **só** contra o Postgres local. Tenant de teste: `FINANCIADOR_TESTE = "12345678000199"`. Todo teste que grava limpa em `try/finally` (padrão já existente).
- Tipos monetários: `NUMERIC(18,2)`, nunca `float`.
- Erros HTTP: `{"erro": "<codigo>", "mensagem": "<texto>"}` (não há endpoint novo neste plano; constraint herdada).
- Commits: mensagem em português, prefixo `feat:`/`fix:`/`test:`/`docs:`/`chore:`, rodapé de atribuição conforme instruções da sessão.

---

### Task 1: Merge da worktree no `master`

**Files:**
- Modify (via merge): `apps/optin/*`, `apps/cliente/*` (novo), `config/settings.py`, `config/urls.py`, `requirements.txt`, `.env.example`, `docker/initdb/*`, `services/cerc/token_provider.py`, docs.
- Modify: `.gitignore`

**Interfaces:**
- Consumes: branch local `worktree-optin-plan-10-11` (17 commits à frente de `5160904`, base comum com `master`).
- Produces: `master` com `apps.cliente`, `optin.cliente_id`, `optin.erro_codigo/erro_mensagem`, endpoint `cancelar`, fix de auth CERC, fix de N+1 — tudo que a Task 4 (baseline) e as tasks seguintes pressupõem.

- [ ] **Step 1: Descartar as alterações não commitadas em `master` (são idênticas ao que vem da branch)**

`git diff` mostra que `config/settings.py` e `requirements.txt` têm exatamente o CORS que a branch já commitou (`corsheaders` + `CORS_ALLOWED_ORIGIN_REGEXES`/`CORS_ALLOWED_ORIGINS`). Manter causaria conflito no merge.

Run (de `C:\DEV\ap\ap-back-optin\optin`):
```bash
git status --short
git checkout -- config/settings.py requirements.txt
git status --short
```
Expected: só `?? .claude/` sobra.

- [ ] **Step 2: Ignorar `.claude/` (worktrees e estado de sessão não são código)**

Adicione ao final de `.gitignore`:
```
.claude/
```

- [ ] **Step 3: Merge**

```bash
git add .gitignore
git commit -m "chore: ignora .claude/ (worktrees e estado de sessão)"
git merge --no-ff worktree-optin-plan-10-11 -m "merge: worktree-optin-plan-10-11 (cliente, optin.cliente_id, cancelar, fix auth CERC, fix N+1)"
git log --oneline -3
```
Expected: merge sem conflito (os dois commits de `master` desde a base são só docs com nomes de arquivo diferentes). Se houver conflito, pare e reporte — não resolva "no chute".

- [ ] **Step 4: Verificar que tudo importa**

Run: `python -m pytest --collect-only -q 2>&1 | tail -3`
Expected: `~110 tests collected` (96 antes + os de `apps/cliente` e `test_views_cancelar_optin`), **zero erros de coleta**. Não rode a suíte ainda — não há banco.

---

### Task 2: Postgres local + `.env` + dependência `sqlparse`

**Files:**
- Modify: `.env` (local, gitignorado)
- Modify: `.env.example`
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`
- Delete: `docker/initdb/00-cliente.sql`, `docker/initdb/01-optin-schema.sql`, `docker/initdb/02-idempotency-e-referencia.sql`

**Interfaces:**
- Produces: serviço `postgresql-x64-17` rodando em `localhost:5432`; role `optin_app` (senha `optin`, `CREATEDB`); env vars `TENANT_IDS`, `TENANT_12345678000199_CONFIG` (com `database_url`), `ADMIN_DB_CONFIG`. Todas as tasks seguintes conectam por aqui.

- [ ] **Step 1: Subir o serviço do Postgres 17**

Iniciar serviço exige elevação no Windows. Peça ao usuário para rodar no terminal dele (prefixo `!`):
```
! powershell -Command "Start-Service postgresql-x64-17; Set-Service postgresql-x64-17 -StartupType Automatic; Get-Service postgresql-x64-17"
```
Expected: `Status: Running`. Se der "Acesso negado", o usuário precisa de um PowerShell como administrador.

- [ ] **Step 2: Criar a role `optin_app`**

Precisa da senha do superusuário `postgres` local — **pergunte ao usuário**; não a grave em lugar nenhum do repo. Com ela em mãos (substitua `<SENHA_POSTGRES>` só no comando, nunca em arquivo):
```powershell
$env:PGPASSWORD = "<SENHA_POSTGRES>"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -c "CREATE ROLE optin_app LOGIN PASSWORD 'optin' CREATEDB;"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -c "\du optin_app"
Remove-Item Env:PGPASSWORD
```
Expected: `CREATE ROLE` e a linha `optin_app | Create DB`. Se já existir (`already exists`), siga.

- [ ] **Step 3: Editar o `.env` local**

Mantenha as chaves que já existem (`ENVIRONMENT`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `IAM_JWT_PUBLIC_KEY`, `IAM_JWT_ISSUER`, `CERC_AUTH_URL`, `CERC_API_BASE_URL`, `CORS_ALLOWED_ORIGINS`). **Substitua** a linha `TENANT_12345678000199_CONFIG=...` e **adicione** `TENANT_IDS` e `ADMIN_DB_CONFIG`. Preserve `cerc_client_id`/`cerc_client_secret`/`cerc_cnpj_solicitante` que já estavam no JSON antigo (copie os valores atuais):

```
TENANT_IDS=12345678000199
TENANT_12345678000199_CONFIG={"database_url":"postgresql+pg8000://optin_app:optin@localhost:5432/ap_12345678000199","cerc_client_id":"<valor atual>","cerc_client_secret":"<valor atual>","cerc_cnpj_solicitante":"12345678000199"}
ADMIN_DB_CONFIG={"database_url":"postgresql+pg8000://optin_app:optin@localhost:5432/postgres"}
```

Se existir `TENANT_38138785000136_CONFIG` (tenant real da CERC apontando para o Cloud SQL antigo, já derrubado), **remova a linha** — sem entrada em `TENANT_IDS` ela seria ignorada, mas é lixo.

- [ ] **Step 4: Atualizar `.env.example`**

Substitua o bloco de multi-tenancy inteiro por:
```
# Multi-tenancy — ver docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md §2.
# TENANT_IDS: lista explícita de financiadores (CNPJ), separados por vírgula. É a única
# fonte de enumeração (migrate_tenants, provisionar_tenant).
TENANT_IDS=12345678000199
# Um JSON por tenant. Dev local: "database_url" (Postgres local, banco SEMPRE ap_<cnpj>).
# Homolog/prod: chaves cloudsql_* (Cloud SQL Connector) no lugar de database_url.
TENANT_12345678000199_CONFIG={"database_url":"postgresql+pg8000://optin_app:optin@localhost:5432/ap_12345678000199","cerc_client_id":"","cerc_client_secret":"","cerc_cnpj_solicitante":"12345678000199"}
# Conexão administrativa (só provisionar_tenant, para CREATE DATABASE). Aponta para o banco "postgres".
ADMIN_DB_CONFIG={"database_url":"postgresql+pg8000://optin_app:optin@localhost:5432/postgres"}
```

- [ ] **Step 5: `requirements.txt` — adicionar `sqlparse` explicitamente**

O Django já o traz transitivamente, mas o runner depende dele diretamente. Adicione a linha após `sqlalchemy>=2.0`:
```
sqlparse>=0.4
```
Run: `pip install -r requirements.txt` — Expected: sem erro.

- [ ] **Step 6: Remover o mecanismo `initdb` e enxugar o `docker-compose.yml`**

```bash
git rm -r docker/initdb
```
Reescreva `docker-compose.yml` (alternativa para quem tiver Docker; esta máquina usa o serviço local):
```yaml
# Alternativa a um PostgreSQL instalado localmente. O schema NÃO é carregado por
# initdb: rode `python manage.py provisionar_tenant <cnpj>` com ADMIN_DB_CONFIG e
# TENANT_<cnpj>_CONFIG apontando para localhost:5433.
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: optin_app
      POSTGRES_PASSWORD: optin
      POSTGRES_DB: postgres
    ports:
      - "5433:5432"
```

- [ ] **Step 7: Verificar conexão administrativa**

Run:
```bash
python -c "import json,os,sqlalchemy; from dotenv import load_dotenv; load_dotenv(); e=sqlalchemy.create_engine(json.loads(os.environ['ADMIN_DB_CONFIG'])['database_url']); print(e.connect().exec_driver_sql('select version()').scalar())"
```
Expected: `PostgreSQL 17.x ...`.

- [ ] **Step 8: Commit**

```bash
git add .env.example requirements.txt docker-compose.yml
git commit -m "chore: Postgres local por serviço, TENANT_IDS/ADMIN_DB_CONFIG, remove docker/initdb"
```

---

### Task 3: `database_url` em `_create_engine`

**Files:**
- Modify: `shared/cloudsql_client.py:187-206`
- Test: `shared/tests/test_cloudsql_client.py`

**Interfaces:**
- Consumes: dict de config de tenant (`shared.tenant_config.get_tenant_config`).
- Produces: `shared.cloudsql_client._create_engine(config: dict) -> sqlalchemy.Engine` — se `config["database_url"]` existir, engine por URL direta; senão, Cloud SQL Connector como hoje. Usado por `get_db`, pelo runner (Task 5) e pelo provisionamento (Task 6).

- [ ] **Step 1: Teste que falha**

Adicione ao final de `shared/tests/test_cloudsql_client.py`:
```python
def test_create_engine_usa_database_url_quando_presente():
    # create_engine é lazy: não conecta, só monta a URL — dá pra testar sem banco.
    engine = cloudsql_client_module._create_engine({
        "database_url": "postgresql+pg8000://u:p@localhost:5432/ap_12345678000199",
    })
    assert engine.url.database == "ap_12345678000199"
    assert engine.url.host == "localhost"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest shared/tests/test_cloudsql_client.py::test_create_engine_usa_database_url_quando_presente -v`
Expected: FAIL com `KeyError: 'cloudsql_connection_name'`.

- [ ] **Step 3: Implementar**

Em `shared/cloudsql_client.py`, substitua a função `_create_engine` inteira por:
```python
def _create_engine(config: dict):
    """Engine por tenant. `database_url` (dev/teste) tem precedência sobre as
    chaves cloudsql_* (Cloud SQL Connector, homolog/prod). Spec 2026-09-02 §2.2."""
    import sqlalchemy

    database_url = config.get("database_url")
    if database_url:
        logger.info("[CloudSQL] Engine por database_url (banco %s)", sqlalchemy.engine.make_url(database_url).database)
        return sqlalchemy.create_engine(database_url, pool_pre_ping=True)

    from google.cloud.sql.connector import Connector, IPTypes

    connector = Connector()

    def getconn():
        return connector.connect(
            config["cloudsql_connection_name"],
            "pg8000",
            user=config["cloudsql_db_user"],
            password=config["cloudsql_db_password"],
            db=config["cloudsql_db_name"],
            ip_type=IPTypes.PUBLIC,
        )

    logger.info("[CloudSQL] Engine criado para tenant (connection=%s)", config["cloudsql_connection_name"])
    return sqlalchemy.create_engine(
        "postgresql+pg8000://", creator=getconn, pool_size=5, max_overflow=2, pool_timeout=30, pool_recycle=1800,
    )
```
Atualize também o docstring do módulo (linhas 6–9) para: `Um banco lógico Postgres por tenant/financiador (ap_<cnpj>) — a config vem de shared.tenant_config.get_tenant_config; o CloudSQLClient é cacheado por financiador_id e validado contra tenant_info (spec 2026-09-02 §5).`

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest shared/tests/test_cloudsql_client.py::test_create_engine_usa_database_url_quando_presente -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/cloudsql_client.py shared/tests/test_cloudsql_client.py
git commit -m "feat: config de tenant aceita database_url (Postgres local/testes)"
```

---

### Task 4: `apps/tenants/registry.py` — lista e validação de tenants

**Files:**
- Create: `apps/tenants/__init__.py` (vazio)
- Create: `apps/tenants/registry.py`
- Create: `apps/tenants/tests/__init__.py` (vazio)
- Create: `apps/tenants/tests/test_registry.py`
- Modify: `config/settings.py:13-19` (INSTALLED_APPS)

**Interfaces:**
- Consumes: `shared.secrets.get_secret`, `shared.tenant_config.get_tenant_config`.
- Produces (usados pelas Tasks 6–8):
  - `RegistroTenantsInvalido(RuntimeError)`
  - `tenant_ids() -> list[str]` — CNPJs de `TENANT_IDS`, sem espaços, sem duplicatas, ordem preservada; vazio → erro.
  - `nome_banco(financiador_id: str) -> str` — `"ap_<cnpj>"`; CNPJ que não seja 14 dígitos → erro.
  - `nome_banco_da_config(config: dict) -> str` — extrai o nome do banco de `database_url` ou de `cloudsql_db_name`.
  - `chave_banco(config: dict) -> tuple` — identidade física do banco para detectar colisão.
  - `validar_config(financiador_id: str, config: dict) -> None` — nome do banco tem que ser `ap_<cnpj>`.
  - `detectar_colisao(financiador_id: str, config: dict) -> str | None` — CNPJ de outro tenant de `TENANT_IDS` que use o mesmo banco, ou `None`.

- [ ] **Step 1: Registrar o app**

Em `config/settings.py`, `INSTALLED_APPS`, adicione `"apps.tenants",` logo após `"apps.optin",` (e `"apps.cliente",` se o merge o tiver colocado ali — mantenha a ordem: optin, cliente, tenants).

- [ ] **Step 2: Testes que falham**

`apps/tenants/tests/test_registry.py`:
```python
import json

import pytest

import shared.tenant_config as tenant_config_module
from apps.tenants import registry
from apps.tenants.registry import RegistroTenantsInvalido


@pytest.fixture(autouse=True)
def _sem_gcp_e_sem_cache(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    tenant_config_module._cache.clear()
    yield
    tenant_config_module._cache.clear()


def _config_local(banco: str) -> str:
    return json.dumps({"database_url": f"postgresql+pg8000://u:p@localhost:5432/{banco}"})


def test_tenant_ids_le_lista_sem_espacos_nem_duplicatas(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", " 12345678000199, 99999999000191 ,12345678000199")
    assert registry.tenant_ids() == ["12345678000199", "99999999000191"]


def test_tenant_ids_vazio_e_erro(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", " , ")
    with pytest.raises(RegistroTenantsInvalido):
        registry.tenant_ids()


def test_nome_banco_segue_convencao():
    assert registry.nome_banco("12345678000199") == "ap_12345678000199"


@pytest.mark.parametrize("ruim", ["123", "1234567800019A", "ap_12345678000199", ""])
def test_nome_banco_rejeita_cnpj_invalido(ruim):
    with pytest.raises(RegistroTenantsInvalido):
        registry.nome_banco(ruim)


def test_nome_banco_da_config_database_url_e_cloudsql():
    assert registry.nome_banco_da_config({"database_url": "postgresql+pg8000://u:p@h/ap_1"}) == "ap_1"
    assert registry.nome_banco_da_config({"cloudsql_connection_name": "p:r:i", "cloudsql_db_name": "ap_2"}) == "ap_2"


def test_validar_config_aceita_banco_correto():
    registry.validar_config("12345678000199", {"database_url": "postgresql+pg8000://u:p@h/ap_12345678000199"})


def test_validar_config_rejeita_banco_de_outro_tenant():
    with pytest.raises(RegistroTenantsInvalido):
        registry.validar_config("12345678000199", {"database_url": "postgresql+pg8000://u:p@h/ap_99999999000191"})


def test_detectar_colisao_encontra_outro_tenant_no_mesmo_banco(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", "12345678000199,99999999000191")
    monkeypatch.setenv("TENANT_12345678000199_CONFIG", _config_local("ap_12345678000199"))
    monkeypatch.setenv("TENANT_99999999000191_CONFIG", _config_local("ap_12345678000199"))  # errado de propósito
    config = json.loads(_config_local("ap_12345678000199"))
    assert registry.detectar_colisao("12345678000199", config) == "99999999000191"


def test_detectar_colisao_none_quando_bancos_distintos(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", "12345678000199,99999999000191")
    monkeypatch.setenv("TENANT_12345678000199_CONFIG", _config_local("ap_12345678000199"))
    monkeypatch.setenv("TENANT_99999999000191_CONFIG", _config_local("ap_99999999000191"))
    config = json.loads(_config_local("ap_12345678000199"))
    assert registry.detectar_colisao("12345678000199", config) is None
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `python -m pytest apps/tenants/tests/test_registry.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'apps.tenants'`.

- [ ] **Step 4: Implementar `apps/tenants/registry.py`**

```python
"""Registro de tenants — quem existe (TENANT_IDS) e em que banco cada um vive.

Spec: docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md §2, §3.2.
Nome de banco é SEMPRE ap_<cnpj>; a validação aqui e a guarda em get_db (§5)
tornam impossível dois tenants no mesmo banco.
"""

import re
from typing import Optional

from sqlalchemy.engine import make_url

from shared.secrets import get_secret
from shared.tenant_config import get_tenant_config

_CNPJ = re.compile(r"^\d{14}$")


class RegistroTenantsInvalido(RuntimeError):
    pass


def tenant_ids() -> list:
    bruto = get_secret("TENANT_IDS")
    ids = []
    for parte in bruto.split(","):
        cnpj = parte.strip()
        if cnpj and cnpj not in ids:
            ids.append(cnpj)
    if not ids:
        raise RegistroTenantsInvalido("TENANT_IDS está vazio")
    return ids


def nome_banco(financiador_id: str) -> str:
    if not _CNPJ.match(financiador_id or ""):
        raise RegistroTenantsInvalido(f"financiador_id inválido: {financiador_id!r} (esperado CNPJ com 14 dígitos)")
    return f"ap_{financiador_id}"


def nome_banco_da_config(config: dict) -> str:
    if config.get("database_url"):
        return make_url(config["database_url"]).database or ""
    return config.get("cloudsql_db_name") or ""


def chave_banco(config: dict) -> tuple:
    if config.get("database_url"):
        url = make_url(config["database_url"])
        return ("url", url.host, url.port, url.database)
    return ("cloudsql", config.get("cloudsql_connection_name"), config.get("cloudsql_db_name"))


def validar_config(financiador_id: str, config: dict) -> None:
    esperado = nome_banco(financiador_id)
    real = nome_banco_da_config(config)
    if real != esperado:
        raise RegistroTenantsInvalido(
            f"tenant {financiador_id}: banco configurado é {real!r}, esperado {esperado!r} (spec §3.2)"
        )


def detectar_colisao(financiador_id: str, config: dict) -> Optional[str]:
    minha = chave_banco(config)
    for outro in tenant_ids():
        if outro == financiador_id:
            continue
        if chave_banco(get_tenant_config(outro)) == minha:
            return outro
    return None
```

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m pytest apps/tenants/tests/test_registry.py -v`
Expected: 10 PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/tenants config/settings.py
git commit -m "feat: apps.tenants.registry — TENANT_IDS, convenção ap_<cnpj>, detecção de colisão"
```

---

### Task 5: `db/migrations/0001_baseline.sql` + `apps/tenants/runner.py`

**Files:**
- Create: `db/migrations/0001_baseline.sql`
- Create: `apps/tenants/runner.py`
- Create: `apps/tenants/tests/conftest.py`
- Create: `apps/tenants/tests/test_runner.py`

**Interfaces:**
- Consumes: `shared.cloudsql_client._create_engine(config)` (Task 3); `ADMIN_DB_CONFIG` (Task 2).
- Produces (usados pelas Tasks 6, 7):
  - `MIGRATIONS_DIR: Path` — `<raiz>/db/migrations`.
  - `MigrationEditada(RuntimeError)`, `NomeMigrationInvalido(RuntimeError)`.
  - `listar_migrations(diretorio: Path) -> list[Path]` — ordenados; nome fora de `^\d{4}_[a-z0-9_]+\.sql$` → erro.
  - `checksum(conteudo: str) -> str` — sha256 hex.
  - `garantir_ledger(conn) -> None` — `CREATE TABLE IF NOT EXISTS schema_aplicado`.
  - `aplicadas(conn) -> dict[str, str]` — `{arquivo: checksum}`.
  - `pendentes(engine, diretorio: Path) -> list[Path]` — confere checksum das aplicadas (divergência → `MigrationEditada`), devolve as não aplicadas.
  - `aplicar(engine, diretorio: Path, dry_run: bool = False) -> list[str]` — nomes aplicados (ou que seriam), cada arquivo em uma transação.
  - Fixture `banco_descartavel` (conftest) → `(engine, nome)` de um banco temporário criado via `ADMIN_DB_CONFIG`, dropado ao fim.

- [ ] **Step 1: Escrever `db/migrations/0001_baseline.sql`**

Consolidação literal de `docker/initdb/00-cliente.sql` + `01-optin-schema.sql` (versão da worktree, com `cliente_id`, `erro_codigo`, `erro_mensagem`) + `02-idempotency-e-referencia.sql`. Nada de `tenant_info`/`schema_aplicado` aqui (provisionamento e runner cuidam deles).

```sql
-- 0001_baseline.sql — estado consolidado do schema no recomeço do zero (2026-09-02).
-- Fonte: SPEC-01 §6 + design 2026-08-25 (cliente) + design 2026-09-02 §6.
-- Forward-only. Não edite este arquivo depois de aplicado: crie 0002_*.sql.

CREATE TABLE cliente (
  id             TEXT PRIMARY KEY,
  documento      TEXT NOT NULL,
  documento_tipo TEXT NOT NULL,
  nome           TEXT NOT NULL,
  email          TEXT,
  telefone       TEXT,
  status         TEXT NOT NULL DEFAULT 'pending',
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (documento)
);

CREATE TABLE optin (
  id                    TEXT PRIMARY KEY,
  referencia_externa    TEXT UNIQUE NOT NULL,
  protocolo_cerc        TEXT UNIQUE,
  origem                TEXT NOT NULL,
  status                TEXT NOT NULL,
  cnpj_solicitante      TEXT NOT NULL,
  cnpj_financiador      TEXT NOT NULL,
  cliente_id            TEXT NOT NULL REFERENCES cliente(id),
  documento_ufr         TEXT NOT NULL,
  documento_ufr_tipo    TEXT NOT NULL,
  documento_titular     TEXT,
  data_assinatura       DATE NOT NULL,
  vigencia_inicio       DATE NOT NULL,
  vigencia_fim          DATE NOT NULL,
  carteira              TEXT,
  evidencia_id          TEXT NOT NULL,
  contrato_id           TEXT,
  erro_codigo           TEXT,
  erro_mensagem         TEXT,
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

CREATE TABLE idempotency_key (
  recurso        TEXT NOT NULL,
  chave          TEXT NOT NULL,
  http_status    INT NOT NULL,
  response_body  JSONB NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (recurso, chave)
);

CREATE SEQUENCE optin_referencia_seq START 1;
CREATE SEQUENCE optout_referencia_seq START 1;
```

- [ ] **Step 2: Fixture de banco descartável — `apps/tenants/tests/conftest.py`**

```python
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
    # render_as_string(hide_password=False) é obrigatório: str(URL) mascara a
    # senha como "***" e o pg8000 mandaria "***" para o servidor (erro 28P01).
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
```

- [ ] **Step 3: Testes que falham — `apps/tenants/tests/test_runner.py`**

```python
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
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `python -m pytest apps/tenants/tests/test_runner.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'apps.tenants.runner'`.

- [ ] **Step 5: Implementar `apps/tenants/runner.py`**

```python
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
    # criar_ledger=False (dry-run) NÃO escreve nada: spec §4.3 diz que --dry-run
    # só imprime, então nem a tabela de ledger pode ser criada como efeito colateral.
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
```

- [ ] **Step 6: Rodar e ver passar**

Run: `python -m pytest apps/tenants/tests/test_runner.py -v`
Expected: 12 PASS. Se `test_split_respeita_dollar_quoting` falhar por pg8000 rejeitar `$$`, o `sqlparse.split` está correto e o problema é o `exec_driver_sql` recebendo `%` — não há `%` no baseline; anote e siga.

- [ ] **Step 7: Commit**

```bash
git add db/migrations/0001_baseline.sql apps/tenants/runner.py apps/tenants/tests/conftest.py apps/tenants/tests/test_runner.py
git commit -m "feat: baseline 0001 + runner de migrations com ledger schema_aplicado"
```

---

### Task 6: `apps/tenants/provisioning.py` — criar banco `ap_<cnpj>` + `tenant_info` + migrar

**Files:**
- Create: `apps/tenants/provisioning.py`
- Create: `apps/tenants/tests/test_provisioning.py`

**Interfaces:**
- Consumes: `registry.nome_banco/validar_config/detectar_colisao` (Task 4); `runner.aplicar/MIGRATIONS_DIR` (Task 5); `shared.cloudsql_client._create_engine` (Task 3); `shared.secrets.get_secret("ADMIN_DB_CONFIG")`; `shared.tenant_config.get_tenant_config`.
- Produces (usados pela Task 7 e pelo `conftest.py` raiz da Task 9):
  - `BancoJaExiste(RuntimeError)`, `TenantInfoDivergente(RuntimeError)`.
  - `config_admin() -> dict`.
  - `banco_existe(engine_admin, nome: str) -> bool`.
  - `criar_banco(engine_admin, nome: str) -> None`.
  - `garantir_tenant_info(engine, financiador_id: str) -> None` — cria a tabela se faltar, insere se vazia, erro se já tiver outro dono.
  - `provisionar(financiador_id: str, existente: bool = False) -> list[str]` — passos da spec §3.1; retorna migrations aplicadas.

- [ ] **Step 1: Testes que falham — `apps/tenants/tests/test_provisioning.py`**

```python
import json
import os

import pytest
import sqlalchemy

import shared.cloudsql_client as cloudsql_client_module
import shared.tenant_config as tenant_config_module
from apps.tenants import provisioning
from apps.tenants.provisioning import BancoJaExiste, TenantInfoDivergente
from apps.tenants.registry import RegistroTenantsInvalido
from apps.tenants.tests.conftest import _engine_admin, _url_para

CNPJ_A = "11111111000191"
CNPJ_B = "22222222000191"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()
    yield
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()


def _configura(monkeypatch, cnpj: str, banco: str):
    monkeypatch.setenv(f"TENANT_{cnpj}_CONFIG", json.dumps({"database_url": _url_para(banco)}))


def _dropa(nome: str):
    admin = _engine_admin()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
    admin.dispose()


def test_provisionar_cria_banco_tenant_info_e_aplica_baseline(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    _dropa(f"ap_{CNPJ_A}")
    try:
        aplicadas = provisioning.provisionar(CNPJ_A)
        assert aplicadas == ["0001_baseline.sql"]
        engine = sqlalchemy.create_engine(_url_para(f"ap_{CNPJ_A}"))
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT financiador_id FROM tenant_info").scalar() == CNPJ_A
            assert conn.exec_driver_sql("SELECT to_regclass('public.optin')").scalar() is not None
        engine.dispose()
    finally:
        _dropa(f"ap_{CNPJ_A}")


def test_provisionar_recusa_banco_existente_sem_flag(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    _dropa(f"ap_{CNPJ_A}")
    try:
        provisioning.provisionar(CNPJ_A)
        with pytest.raises(BancoJaExiste):
            provisioning.provisionar(CNPJ_A)
        assert provisioning.provisionar(CNPJ_A, existente=True) == []  # idempotente
    finally:
        _dropa(f"ap_{CNPJ_A}")


def test_provisionar_recusa_config_com_nome_de_banco_errado(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_A)
    _configura(monkeypatch, CNPJ_A, "banco_livre")
    with pytest.raises(RegistroTenantsInvalido):
        provisioning.provisionar(CNPJ_A)


def test_provisionar_recusa_cnpj_fora_de_tenant_ids(monkeypatch):
    monkeypatch.setenv("TENANT_IDS", CNPJ_B)
    _configura(monkeypatch, CNPJ_A, f"ap_{CNPJ_A}")
    with pytest.raises(RegistroTenantsInvalido):
        provisioning.provisionar(CNPJ_A)


def test_garantir_tenant_info_recusa_outro_dono(banco_descartavel):
    engine, _ = banco_descartavel
    provisioning.garantir_tenant_info(engine, CNPJ_A)
    provisioning.garantir_tenant_info(engine, CNPJ_A)  # idempotente
    with pytest.raises(TenantInfoDivergente):
        provisioning.garantir_tenant_info(engine, CNPJ_B)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest apps/tenants/tests/test_provisioning.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'apps.tenants.provisioning'`.

- [ ] **Step 3: Implementar `apps/tenants/provisioning.py`**

```python
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
            conn.execute(text("INSERT INTO tenant_info (financiador_id) VALUES (:f)"), {"f": financiador_id})
        elif dono != financiador_id:
            raise TenantInfoDivergente(f"banco já pertence ao tenant {dono}, não a {financiador_id}")


def provisionar(financiador_id: str, existente: bool = False) -> list:
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest apps/tenants/tests/test_provisioning.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/tenants/provisioning.py apps/tenants/tests/test_provisioning.py
git commit -m "feat: provisionamento de tenant (banco ap_<cnpj>, tenant_info, baseline)"
```

---

### Task 7: Guarda `tenant_info` em `get_db`

**Files:**
- Modify: `shared/cloudsql_client.py:209-235`
- Modify: `shared/tests/test_cloudsql_client.py:53-116`

**Interfaces:**
- Consumes: `tenant_info` criada por `provisioning.garantir_tenant_info` (Task 6).
- Produces: `shared.cloudsql_client.TenantMismatchError(RuntimeError)`; `_verificar_tenant(engine, financiador_id) -> None`; `get_db` passa a chamá-la antes de cachear.

**Atenção:** dois testes existentes quebram com a guarda e precisam ser reescritos, não apagados: `test_get_db_cacheia_por_financiador_id` (apontava tenant 2 para o banco do tenant 1 — agora isso é exatamente o erro que a guarda detecta) e `test_get_db_single_flight_on_concurrent_first_access` (monkeypatcha `_create_engine` para devolver `object()`, que não tem `.connect()`).

- [ ] **Step 1: Reescrever os dois testes e adicionar o da guarda**

Em `shared/tests/test_cloudsql_client.py`, substitua `test_get_db_cacheia_por_financiador_id` por:
```python
def test_get_db_cacheia_por_financiador_id(monkeypatch):
    cloudsql_client_module._clients.clear()
    monkeypatch.setenv(f"TENANT_{FINANCIADOR_TESTE_2}_CONFIG", os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"])
    # Engine fake + guarda desligada: o que se prova aqui é só o cache por id.
    monkeypatch.setattr(cloudsql_client_module, "_create_engine", lambda config: object())
    monkeypatch.setattr(cloudsql_client_module, "_verificar_tenant", lambda engine, fid: None)

    db1a = get_db(FINANCIADOR_TESTE)
    db1b = get_db(FINANCIADOR_TESTE)
    db2 = get_db(FINANCIADOR_TESTE_2)

    assert db1a is db1b
    assert db1a is not db2

    cloudsql_client_module._clients.clear()


def test_get_db_recusa_tenant_apontando_para_banco_de_outro(monkeypatch):
    # Reproduz o incidente do HANDOFF (dois tenants no mesmo banco): o banco do
    # tenant de teste tem tenant_info = FINANCIADOR_TESTE; pedir get_db de outro
    # id com a MESMA config tem que explodir, não servir dados alheios.
    cloudsql_client_module._clients.pop(FINANCIADOR_TESTE_2, None)
    monkeypatch.setenv(f"TENANT_{FINANCIADOR_TESTE_2}_CONFIG", os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"])

    with pytest.raises(cloudsql_client_module.TenantMismatchError):
        get_db(FINANCIADOR_TESTE_2)

    assert FINANCIADOR_TESTE_2 not in cloudsql_client_module._clients  # nada cacheado
```
Em `test_get_db_single_flight_on_concurrent_first_access`, logo após a linha `monkeypatch.setattr(cloudsql_client_module, "_create_engine", _slow_fake_engine)`, adicione:
```python
    monkeypatch.setattr(cloudsql_client_module, "_verificar_tenant", lambda engine, fid: None)
```
E adicione `import shared.tenant_config as tenant_config_module` no topo e, dentro dos dois testes reescritos, `tenant_config_module._cache.pop(FINANCIADOR_TESTE_2, None)` antes do `monkeypatch.setenv` (o cache de config é por processo e pode ter ficado de outro teste).

- [ ] **Step 2: Provisionar o tenant de teste (o teste da guarda precisa de `tenant_info` real)**

Run: `python -c "from dotenv import load_dotenv; load_dotenv(); from apps.tenants.provisioning import provisionar; print(provisionar('12345678000199', existente=True))"`
Expected: `['0001_baseline.sql']` na primeira vez (`[]` se já provisionado). A partir daqui `ap_12345678000199` existe com `tenant_info = 12345678000199`.

- [ ] **Step 3: Rodar e ver falhar**

Run: `python -m pytest shared/tests/test_cloudsql_client.py -v -k "recusa_tenant or cacheia or single_flight"`
Expected: `recusa_tenant` FAIL com `AttributeError: ... has no attribute 'TenantMismatchError'`; os outros dois FAIL com `AttributeError: ... has no attribute '_verificar_tenant'`.

- [ ] **Step 4: Implementar**

Em `shared/cloudsql_client.py`, adicione logo após a classe `CloudSQLClient`:
```python
class TenantMismatchError(RuntimeError):
    """O banco configurado para este financiador_id pertence a outro tenant (ou a nenhum)."""


def _verificar_tenant(engine, financiador_id: str) -> None:
    from sqlalchemy import text

    with engine.connect() as conn:
        dono = conn.execute(text("SELECT financiador_id FROM tenant_info")).scalar()
    if dono != financiador_id:
        raise TenantMismatchError(
            f"banco configurado para {financiador_id} pertence a {dono!r} — "
            "confira TENANT_{cnpj}_CONFIG; nunca dois tenants no mesmo banco (spec 2026-09-02 §5)"
        )
```
E em `get_db`, troque o bloco dentro do `with _lock_for(financiador_id):` por:
```python
        if financiador_id in _clients:
            return _clients[financiador_id]

        config = get_tenant_config(financiador_id)
        engine = _create_engine(config)
        try:
            _verificar_tenant(engine, financiador_id)
        except Exception:
            engine.dispose()
            raise
        client = CloudSQLClient(engine)
        _clients[financiador_id] = client
        return client
```
Nota: se `tenant_info` não existir, o `SELECT` levanta erro do driver — também correto (banco não provisionado ≠ banco deste tenant); o `dispose()` no `except Exception` cobre os dois casos.

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m pytest shared/tests/test_cloudsql_client.py -v`
Expected: todos PASS (o tenant de teste já está provisionado desde o Step 2, então `round_trip` e `gte_lte` também passam).

- [ ] **Step 6: Commit**

```bash
git add shared/cloudsql_client.py shared/tests/test_cloudsql_client.py
git commit -m "feat: get_db valida tenant_info — dois tenants no mesmo banco falham em runtime"
```

---

### Task 8: Management commands `migrate_tenants`, `provisionar_tenant`, `seed_dominio_arranjo`

**Files:**
- Create: `apps/tenants/management/__init__.py` (vazio)
- Create: `apps/tenants/management/commands/__init__.py` (vazio)
- Create: `apps/tenants/management/commands/migrate_tenants.py`
- Create: `apps/tenants/management/commands/provisionar_tenant.py`
- Create: `apps/tenants/management/commands/seed_dominio_arranjo.py`
- Create: `apps/tenants/tests/test_commands.py`

**Interfaces:**
- Consumes: `registry`, `runner`, `provisioning` (Tasks 4–6); `_create_engine`, `_verificar_tenant` (Tasks 3, 7).
- Produces: `python manage.py migrate_tenants [--tenant CNPJ] [--dry-run]`, `python manage.py provisionar_tenant CNPJ [--existente]`, `python manage.py seed_dominio_arranjo --tenant CNPJ`. Usados pelo `cloudbuild.yaml` e pelos Cloud Run Jobs (Plan 02) e pelo runbook (Plan 03).

- [ ] **Step 1: Testes que falham — `apps/tenants/tests/test_commands.py`**

```python
import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

import shared.cloudsql_client as cloudsql_client_module
import shared.tenant_config as tenant_config_module
from apps.tenants.tests.conftest import _engine_admin, _url_para

CNPJ = "33333333000191"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("TENANT_IDS", CNPJ)
    monkeypatch.setenv(f"TENANT_{CNPJ}_CONFIG", json.dumps({"database_url": _url_para(f"ap_{CNPJ}")}))
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()
    yield
    tenant_config_module._cache.clear()
    cloudsql_client_module._clients.clear()
    admin = _engine_admin()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "ap_{CNPJ}" WITH (FORCE)')
    admin.dispose()


def test_provisionar_depois_migrate_e_seed(monkeypatch):
    out = StringIO()
    call_command("provisionar_tenant", CNPJ, stdout=out)
    assert "0001_baseline.sql" in out.getvalue()

    out = StringIO()
    call_command("migrate_tenants", stdout=out)
    assert f"ap_{CNPJ}: nada pendente" in out.getvalue()

    out = StringIO()
    call_command("migrate_tenants", "--dry-run", stdout=out)
    assert f"ap_{CNPJ}: nada pendente" in out.getvalue()

    call_command("seed_dominio_arranjo", "--tenant", CNPJ, stdout=StringIO())
    call_command("seed_dominio_arranjo", "--tenant", CNPJ, stdout=StringIO())  # idempotente
    db = cloudsql_client_module.get_db(CNPJ)
    assert db.table("dominio_arranjo").select("codigo").eq("codigo", "99T").execute().data == [{"codigo": "99T"}]


def test_provisionar_duas_vezes_falha_sem_existente():
    call_command("provisionar_tenant", CNPJ, stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("provisionar_tenant", CNPJ, stdout=StringIO())
    call_command("provisionar_tenant", CNPJ, "--existente", stdout=StringIO())


def test_migrate_tenants_falha_se_tenant_nao_provisionado():
    with pytest.raises(CommandError):
        call_command("migrate_tenants", stdout=StringIO())
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest apps/tenants/tests/test_commands.py -v`
Expected: FAIL com `CommandError: Unknown command: 'provisionar_tenant'`.

- [ ] **Step 3: Implementar os três comandos**

`apps/tenants/management/commands/provisionar_tenant.py`:
```python
from django.core.management.base import BaseCommand, CommandError

from apps.tenants import provisioning, registry


class Command(BaseCommand):
    help = "Cria o banco ap_<cnpj>, grava tenant_info e aplica as migrations (spec 2026-09-02 §3)."

    def add_arguments(self, parser):
        parser.add_argument("cnpj")
        parser.add_argument("--existente", action="store_true", help="reaproveita banco já criado (ex.: após restore)")

    def handle(self, *args, **opts):
        try:
            aplicadas = provisioning.provisionar(opts["cnpj"], existente=opts["existente"])
        except (registry.RegistroTenantsInvalido, provisioning.BancoJaExiste, provisioning.TenantInfoDivergente) as e:
            raise CommandError(str(e))
        nome = registry.nome_banco(opts["cnpj"])
        for a in aplicadas:
            self.stdout.write(f"[provisionar] {nome}: {a} aplicada")
        self.stdout.write(self.style.SUCCESS(f"[provisionar] {nome}: pronto ({len(aplicadas)} migration(s))"))
```

`apps/tenants/management/commands/migrate_tenants.py`:
```python
from django.core.management.base import BaseCommand, CommandError

from apps.tenants import registry, runner
from shared.cloudsql_client import _create_engine, _verificar_tenant
from shared.tenant_config import get_tenant_config


class Command(BaseCommand):
    help = "Aplica db/migrations/*.sql pendentes em cada tenant de TENANT_IDS (spec 2026-09-02 §4)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="só este CNPJ")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        ids = [opts["tenant"]] if opts["tenant"] else registry.tenant_ids()
        falhas = []
        for cnpj in ids:
            nome = registry.nome_banco(cnpj)
            try:
                config = get_tenant_config(cnpj)
                registry.validar_config(cnpj, config)
                engine = _create_engine(config)
                try:
                    _verificar_tenant(engine, cnpj)
                    aplicadas = runner.aplicar(engine, runner.MIGRATIONS_DIR, dry_run=opts["dry_run"])
                finally:
                    engine.dispose()
            except Exception as e:  # um tenant não pode impedir os outros
                falhas.append(cnpj)
                self.stderr.write(f"[migrate] {nome}: ERRO {e}")
                continue
            verbo = "seria aplicada" if opts["dry_run"] else "aplicada"
            for a in aplicadas:
                self.stdout.write(f"[migrate] {nome}: {a} {verbo}")
            if not aplicadas:
                self.stdout.write(f"[migrate] {nome}: nada pendente")
        if falhas:
            raise CommandError(f"falhou em: {', '.join(falhas)}")
```

`apps/tenants/management/commands/seed_dominio_arranjo.py`:
```python
from django.core.management.base import BaseCommand
from sqlalchemy import text

from apps.tenants import registry
from shared.cloudsql_client import get_db


class Command(BaseCommand):
    help = "Seed mínimo de dominio_arranjo (código 99T = todos). Idempotente. Spec 2026-09-02 §6.3."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)

    def handle(self, *args, **opts):
        cnpj = opts["tenant"]
        with get_db(cnpj)._engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dominio_arranjo (codigo, descricao, ativo, atualizado_em)
                VALUES ('99T', 'Todos os arranjos', true, now())
                ON CONFLICT (codigo) DO NOTHING
            """))
        self.stdout.write(self.style.SUCCESS(f"[seed] {registry.nome_banco(cnpj)}: dominio_arranjo ok"))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest apps/tenants/tests/test_commands.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/tenants/management apps/tenants/tests/test_commands.py
git commit -m "feat: comandos migrate_tenants, provisionar_tenant e seed_dominio_arranjo"
```

---

### Task 9: Bootstrap do banco de teste + suíte completa verde

**Files:**
- Create: `conftest.py` (raiz do repo `optin/`)
- Modify: `pytest.ini`

**Interfaces:**
- Consumes: `provisioning.provisionar` (Task 6).
- Produces: toda sessão do pytest garante `ap_12345678000199` provisionado e migrado antes do primeiro teste; marker `homolog` registrado.

- [ ] **Step 1: `conftest.py` na raiz**

```python
"""Bootstrap da suíte: tenant de teste provisionado/migrado no Postgres local uma vez por sessão.

Idempotente (provisionar(..., existente=True) + runner com ledger). Se TENANT_IDS não
estiver no .env, a suíte para com instrução — nunca cai num Cloud SQL real (spec §7).
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

FINANCIADOR_TESTE = "12345678000199"


@pytest.fixture(scope="session", autouse=True)
def _tenant_de_teste_provisionado():
    if os.getenv("GOOGLE_CLOUD_PROJECT"):
        pytest.exit("GOOGLE_CLOUD_PROJECT setado — a suíte só roda contra Postgres local (spec 2026-09-02 §7)")
    if FINANCIADOR_TESTE not in (os.getenv("TENANT_IDS") or ""):
        pytest.exit(f"TENANT_IDS sem {FINANCIADOR_TESTE} — configure o .env (ver .env.example)")

    from apps.tenants.provisioning import provisionar

    provisionar(FINANCIADOR_TESTE, existente=True)
```

- [ ] **Step 2: `pytest.ini` — marker**

```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings
python_files = tests.py test_*.py *_tests.py
markers =
    homolog: chama a CERC de homologação de verdade — rodar sob demanda com -m homolog
```

- [ ] **Step 3: Rodar a suíte inteira**

Run: `python -m pytest -q 2>&1 | tail -15`
Expected: **tudo verde**. Falhas prováveis e o que fazer:
- Testes de `apps/optin`/`apps/cliente` que assumem linhas pré-existentes em `dominio_arranjo` (ex.: `99T`): rode `python manage.py seed_dominio_arranjo --tenant 12345678000199` uma vez e reexecute. Se depender disso, adicione ao `conftest.py` raiz, após `provisionar(...)`: `from django.core.management import call_command; call_command("seed_dominio_arranjo", "--tenant", FINANCIADOR_TESTE, verbosity=0)`.
- Testes que ainda referenciem `TENANT_38138785000136_CONFIG` ou Cloud SQL: são bugs deste plano — corrija o teste para o tenant local, não o `.env`.
- Qualquer outra falha: **não** marque como `xfail`; investigue e reporte.

- [ ] **Step 4: Rodar duas vezes seguidas (idempotência do bootstrap)**

Run: `python -m pytest -q 2>&1 | tail -2 && python -m pytest -q 2>&1 | tail -2`
Expected: mesmo resultado verde nas duas.

- [ ] **Step 5: Commit**

```bash
git add conftest.py pytest.ini
git commit -m "test: suíte roda contra Postgres local com tenant de teste provisionado por sessão"
```

---

### Task 10: Documentação de dev e limpeza

**Files:**
- Create: `docs/dev-setup.md`
- Modify: `docs/superpowers/specs/2026-08-24-multitenancy-design.md` (nota de substituição no topo)

**Interfaces:** nenhuma (documentação).

- [ ] **Step 1: `docs/dev-setup.md`**

```markdown
# Setup de desenvolvimento

## Banco local (PostgreSQL 17 instalado)

1. Serviço: `Start-Service postgresql-x64-17` (PowerShell como administrador, uma vez; `Set-Service -StartupType Automatic`).
2. Role (uma vez, como `postgres`): `CREATE ROLE optin_app LOGIN PASSWORD 'optin' CREATEDB;`
3. `.env` (copie de `.env.example`): `TENANT_IDS`, `TENANT_12345678000199_CONFIG` com `database_url`, `ADMIN_DB_CONFIG`.
4. `python manage.py provisionar_tenant 12345678000199` — cria `ap_12345678000199`, `tenant_info`, aplica `db/migrations/`.
5. `python manage.py seed_dominio_arranjo --tenant 12345678000199` — código `99T`.

Sem Docker nesta máquina. Quem tiver Docker: `docker compose up -d` sobe um Postgres 17 em `localhost:5433` (ajuste as URLs do `.env`).

## Migrations

- Arquivos em `db/migrations/NNNN_descricao.sql`, forward-only, nunca editar um já aplicado.
- Aplicar em todos os tenants: `python manage.py migrate_tenants` (`--dry-run` para ver; `--tenant CNPJ` para um só).
- Ledger por banco: `schema_aplicado (arquivo, checksum, aplicado_em)`.

## Testes

`python -m pytest` — o `conftest.py` da raiz provisiona/migra o tenant de teste. A suíte **nunca** aponta para Cloud SQL (aborta se `GOOGLE_CLOUD_PROJECT` estiver setado).

Spec: `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md`.
```

- [ ] **Step 2: Nota no topo da spec de multi-tenancy antiga**

Em `docs/superpowers/specs/2026-08-24-multitenancy-design.md`, logo após o título, adicione:
```markdown
> **Substituída em parte** por `2026-09-02-database-multitenant-migrations-design.md`: registro de tenants (`TENANT_IDS`), banco lógico por tenant (`ap_<cnpj>`), provisionamento, migrations e guarda `tenant_info`. O formato de `TENANT_{cnpj}_CONFIG` continua válido, acrescido de `database_url` opcional.
```

- [ ] **Step 3: Commit**

```bash
git add docs/dev-setup.md docs/superpowers/specs/2026-08-24-multitenancy-design.md
git commit -m "docs: setup de dev com Postgres local e migrations por tenant"
```

---

## Self-Review Notes

- **Spec coverage:** §2.1 `TENANT_IDS` (T4), §2.2 `database_url` (T3), §2.3 `ADMIN_DB_CONFIG` (T2/T6), §2.4 role única (T2), §3 provisionamento e convenção `ap_<cnpj>` (T4/T6/T8), §4 runner/ledger/dry-run/nome (T5/T8), §4.4 "quando roda" — o passo de deploy fica no Plan 02, §5 guarda (T7), §6 baseline e pré-requisito de merge (T1/T5), §6.3 seed (T8), §7 testes locais e marker (T2/T9). §8/§9 não geram tasks. §10 = Plans 02/03.
- **Placeholder scan:** `<SENHA_POSTGRES>` e `<valor atual>` são entradas do usuário/ambiente, não lacunas do plano; instruído explicitamente a não gravá-las.
- **Type consistency:** `_create_engine(config: dict)` (T3) é o mesmo símbolo importado em T6/T8; `_verificar_tenant(engine, financiador_id)` (T7) idem em T8; `runner.aplicar(engine, diretorio, dry_run=False) -> list[str]` (T5) usado em T6/T8 com a mesma assinatura; `provisionar(financiador_id, existente=False) -> list[str]` (T6) usado em T8/T9; helpers `_engine_admin`/`_url_para` do conftest de `apps/tenants/tests` importados por T6/T8.
- **Risco conhecido:** pg8000 + `exec_driver_sql` interpreta `%` como placeholder em alguns caminhos; o baseline não contém `%`. Se uma migration futura precisar (`LIKE '%x'`), usar `%%`.
