# Setup de desenvolvimento

## Banco local (PostgreSQL 17)

### Cluster user-owned (Postgres 17.7, recomendado nesta máquina)

Este é o padrão atual: um cluster PostgreSQL rodando em modo user-owned, sem precisar de privilégios de administrador do Windows.

`<PGDATA>` = diretório de dados do cluster local (ex.: `%USERPROFILE%\pgdata-optin17`).

**Localização do cluster:** `<PGDATA>`  
**Superuser:** `postgres` (senha definida por você ao rodar `initdb`)  
**Autenticação:** SCRAM-SHA-256  
**Porta:** 5432

**Iniciar o cluster** (sem administrador, executar após qualquer reboot):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "<PGDATA>" -l "<PGDATA>\server.log" start
```

**Parar o cluster:**

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "<PGDATA>" stop
```

**Verificar status:**

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "<PGDATA>" status
```

**Role de aplicação** (já criada, uma vez via `postgres`):

```sql
CREATE ROLE optin_app LOGIN PASSWORD 'optin' CREATEDB;
GRANT pg_signal_backend TO optin_app;
```

A role `optin_app` precisa de `CREATEDB` porque o comando `provisionar_tenant` cria novos bancos.

`pg_signal_backend` é para os testes: `apps/tenants/tests/*` derrubam bancos descartáveis com
`DROP DATABASE ... WITH (FORCE)`, que precisa terminar qualquer backend ainda conectado ao banco
(ex.: um worker de autovacuum, que roda como role interna do cluster). Sem essa concessão, o drop
falha intermitentemente com `42501 permissão negada para terminar o processo` — não é um teste
flaky, é falta de privilégio (medido: ~1 em 100 execuções, porque depende do autovacuum ainda
estar conectado no instante do drop). Não afeta código de produção — `provisionar_tenant` nunca
dá `DROP`, só cria.

### Alternativa: serviço Windows (requer administrador)

Quem tiver privilégios de administrador pode usar a instalação Windows do PostgreSQL como um serviço:

```powershell
Start-Service postgresql-x64-17
```

**Cuidado:** o serviço e o cluster user-owned usam o mesmo porto (5432). Não deixe ambos rodando simultaneamente.

## Provisionamento de tenant

Após copiar `.env` de `.env.example` (atualizando `TENANT_IDS` e `TENANT_12345678000199_CONFIG` com a URL do banco local):

```bash
python manage.py provisionar_tenant 12345678000199
```

Isso:
1. Cria o banco lógico `ap_12345678000199` (convenção `ap_<cnpj>`).
2. Cria a tabela `tenant_info` (identificação e auditoria).
3. Aplica as migrations de `db/migrations/`.

## Seed de dados mínimo

Após provisionar, popular o domínio de arranjos (código `99T` = todos os arranjos):

```bash
python manage.py seed_dominio_arranjo --tenant 12345678000199
```

## Migrations

As migrations habitam em `db/migrations/NNNN_descricao.sql`, são **forward-only** e **nunca devem ser editadas** após aplicação.

**Aplicar em todos os tenants:**

```bash
python manage.py migrate_tenants
```

**Aplicar apenas num tenant:**

```bash
python manage.py migrate_tenants --tenant 12345678000199
```

**Simulação (sem escrever no ledger):**

```bash
python manage.py migrate_tenants --dry-run
```

O ledger de aplicação vive em `schema_aplicado (arquivo, checksum, aplicado_em)` dentro de cada banco de tenant. Editar um arquivo já aplicado é erro por design.

**Cuidado com pg8000:** o runner aplica cada statement via `conn.exec_driver_sql`, que trata `%` como marcador de parâmetro. Uma migration com `LIKE '%x'` precisa escrever `LIKE '%%x'`.

## Testes

```bash
python -m pytest
```

O `conftest.py` da raiz provisiona e migra o tenant de teste uma única vez por sessão (idempotente). **Proteção:** a suíte aborta se `GOOGLE_CLOUD_PROJECT` estiver setado ou se `TENANT_IDS` não tiver `12345678000199` — nunca aponta para um Cloud SQL real por acidente.

**Marcadores (pytest.ini):**

- `@pytest.mark.sem_banco`: testes que não precisam de Postgres rodando.
- `@pytest.mark.homolog`: testes que chamam a CERC de homologação de verdade — rodam sob demanda com `-m homolog`.

Ver spec: `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md` (§7 testes, §8.1 bootstrap).

## Sem Docker nesta máquina

Quem tiver Docker pode usar a alternativa:

```bash
docker compose up -d
```

Isso sobe um Postgres 17 em `localhost:5433`. Ajuste as URLs do `.env` conforme necessário (`ADMIN_DB_CONFIG`, `TENANT_{cnpj}_CONFIG[database_url]`).
