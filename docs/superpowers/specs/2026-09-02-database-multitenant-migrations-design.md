# optin-service — Banco de dados: multi-tenancy, provisionamento e migrations (recomeço do zero)

> Status: aprovado em brainstorming (2026-09-02), pronto para plano de implementação.
> Substitui a §3 "Camada de dados" de `2026-08-18-optin-service-design.md` e a §3 de `2026-08-24-multitenancy-design.md` no que conflitar. Motivação: todos os bancos foram derrubados para recomeçar; este documento fixa como o banco é desenhado daqui em diante.

## 1. Contexto e decisões

### 1.1 Modelo de negócio (confirmado)

- Somos **prestadores de serviço a financiadores**. O `optin-service` é a plataforma.
- **Tenant = financiador.** Cada financiador é uma **ilha**: não existe visão cross-tenant (sem painel consolidado, sem cadastro global de EC, sem faturamento agregado no banco). Vocês só olham "por dentro" de um tenant.
- Cada financiador tem seus próprios **clientes = ECs** (Estabelecimentos Comerciais). O mesmo EC (mesmo CNPJ/CPF) **pode** ser cliente de vários financiadores ao mesmo tempo — cada tenant tem seu próprio registro, sem vínculo entre eles.
- Escala prevista: **meia dúzia** de financiadores em 12–24 meses.
- Onboarding de financiador é feito pelo time (não é self-service).

### 1.2 Decisões de arquitetura

| Decisão | Escolha | Alternativas descartadas e por quê |
|---|---|---|
| Isolamento | **Um banco lógico Postgres por tenant** (`CREATE DATABASE`), numa **única instância Cloud SQL** | *RLS em banco compartilhado:* complexidade e risco de vazamento sem ganho com 6 tenants. *Instância por tenant:* custo ×6 sem exigência contratual hoje. Se um financiador exigir infra dedicada, o tenant é promovido a instância própria mudando só a config (`cloudsql_connection_name`) — sem mudar código. |
| Acesso a dados | **Sem Django ORM** — mantém `shared/cloudsql_client.py` (`QueryBuilder` + SQL parametrizado via SQLAlchemy Core/pg8000) | *Django ORM com router por contextvar:* tecnicamente viável e seguro, mas este seria o **único de cinco repos** do mesmo time com ORM. Verificado em disco em 2026-09-02: `etl-back-ingestion-main`, `etl-back-elegibility`, `ap-back-consulta-agenda`, `ap-back-contratos` — todos `DATABASES = {}`, sem `models.py`, sem `migrations/`. *SQLAlchemy ORM + Alembic:* mesma divergência; revisitar só se a SPEC-03 (agenda/URs) trouxer modelo relacional bem mais denso. |
| Migrations | **SQL puro versionado + runner próprio por tenant** (`migrate_tenants`), alinhado ao `scripts/apply_schema.py` que `ap-back-consulta-agenda` e `ap-back-contratos` já usam (ledger `schema_aplicado` com checksum) | *Nenhuma ferramenta (estado anterior):* `ALTER TABLE` na mão, repetido por tenant, sem controle de versão — foi a dor concreta nº 1. *Alembic:* framework a mais para ~60 linhas de runner. |
| Infra GCP | **Projeto novo por ambiente** (`ap-optin-homolog` agora, `ap-optin-prod` depois), região `southamerica-east1`, provisionado por runbook `gcloud` executado via CLI (§10) | *Reaproveitar `registradora-506000`:* descartado — o recomeço inclui a infra, separada do projeto antigo. *Um projeto para homolog+prod:* mistura credenciais reais da CERC com as de homolog. *Terraform:* nenhum irmão usa; YAGNI para 1 projeto e meia dúzia de recursos. |
| Garantia de isolamento | **Estrutural, em runtime** (`tenant_info` validado por `get_db`) | *Disciplina no `.env`/Secret Manager (estado anterior):* causou incidente real — dois tenants no mesmo banco, suíte de testes apagou opt-ins reais. |
| Testes | **Postgres local** (PostgreSQL 17 instalado; Docker opcional), mesmo runner de migrations | *Cloud SQL real do tenant de dev (estado anterior):* lento, custa, e foi o vetor do incidente acima. |

O diagnóstico que levou a isto: "sem ORM" era defensável; o erro foi empacotar junto "sem ferramenta de migration" e "sem garantia estrutural de isolamento". As duas dores reais que apareceram não vêm de escrever SQL — vêm da ausência dessas duas peças. Este documento adiciona as duas sem mexer no estilo de acesso a dados.

## 2. Registro de tenants e configuração

### 2.1 `TENANT_IDS`

Variável de ambiente / segredo **`TENANT_IDS`**: lista explícita de CNPJs separados por vírgula.

```
TENANT_IDS=12345678000199,38138785000136
```

É a única fonte de enumeração de tenants (runner de migrations, provisionamento, health check). Adicionar tenant é ato deliberado — editar `TENANT_IDS` **e** criar `TENANT_{cnpj}_CONFIG`. Um segredo `TENANT_*_CONFIG` sem entrada em `TENANT_IDS` é ignorado; uma entrada em `TENANT_IDS` sem segredo faz `provisionar_tenant`/`migrate_tenants` falharem com erro claro.

### 2.2 `TENANT_{cnpj}_CONFIG` (JSON) — chaves

Mantém as chaves atuais e ganha uma opcional:

| Chave | Obrigatória | Uso |
|---|---|---|
| `cloudsql_connection_name` | prod/homolog | Cloud SQL Connector |
| `cloudsql_db_user` / `cloudsql_db_password` | prod/homolog | idem |
| `cloudsql_db_name` | prod/homolog | **Deve ser `ap_<cnpj>`** (ver §3.2) |
| `database_url` | **opcional** | Se presente, engine via URL SQLAlchemy direta (`postgresql+pg8000://...`) e as chaves `cloudsql_*` são ignoradas. Uso: dev local e testes. |
| `cerc_client_id` / `cerc_client_secret` / `cerc_cnpj_solicitante` | sim | inalterado |

`shared/cloudsql_client._create_engine(config)` passa a ramificar: `database_url` → `sqlalchemy.create_engine(url, pool_pre_ping=True)`; senão → Connector como hoje.

### 2.3 `ADMIN_DB_CONFIG` (JSON)

Conexão administrativa à instância, usada **só** por `provisionar_tenant` para `CREATE DATABASE`. Mesmas chaves de conexão de um tenant (`database_url` **ou** `cloudsql_*`), apontando para o banco `postgres` da instância, com role que tenha `CREATEDB`. Nunca usada pela aplicação em runtime.

### 2.4 Roles

Uma role de aplicação por instância (`optin_app`), compartilhada por todos os bancos lógicos. Isolamento é pelo banco (uma conexão Postgres não enxerga outro banco), não pela role. Role por tenant só se um contrato exigir — YAGNI.

## 3. Provisionamento: `manage.py provisionar_tenant <cnpj>`

### 3.1 Passos

1. Valida que `<cnpj>` está em `TENANT_IDS` e que `TENANT_<cnpj>_CONFIG` existe e é parseável.
2. Valida `cloudsql_db_name == f"ap_{cnpj}"` (quando não há `database_url`; com `database_url`, valida que o path do URL é `ap_<cnpj>`).
3. Valida que nenhum **outro** tenant em `TENANT_IDS` usa o mesmo banco (mesmo `cloudsql_connection_name` + `cloudsql_db_name`, ou mesmo `database_url`). Colisão → aborta.
4. Conecta via `ADMIN_DB_CONFIG` (autocommit) e executa `CREATE DATABASE ap_<cnpj>`. Se já existir: aborta com erro, **a menos que** `--existente` seja passado (caso de re-provisionar após restore).
5. Conecta ao banco novo com a config do tenant (engine interno, sem a guarda da §5 — ela ainda não tem o que validar) e cria:
   ```sql
   CREATE TABLE tenant_info (
     financiador_id TEXT PRIMARY KEY,
     criado_em      TIMESTAMPTZ NOT NULL DEFAULT now()
   );
   INSERT INTO tenant_info (financiador_id) VALUES (:cnpj);
   ```
   `tenant_info` é identidade de infraestrutura, não schema de aplicação — por isso vive no provisionamento, não numa migration.
6. Executa `migrate_tenants --tenant <cnpj>` (§4).

### 3.2 Convenção de nome

Nome do banco é **sempre** `ap_<cnpj>` (ex.: `ap_12345678000199`). Não é livre. Junto com a validação da §5, isto elimina a classe de erro do incidente (dois tenants no mesmo banco): o nome codifica o dono, e o banco confirma o dono em runtime.

## 4. Migrations: `manage.py migrate_tenants`

### 4.1 Layout

```
db/
└── migrations/
    ├── 0001_baseline.sql
    ├── 0002_<descricao>.sql
    └── ...
```

- Numeração sequencial de 4 dígitos, sem lacunas. Nome = `NNNN_descricao_snake_case.sql`.
- **Forward-only.** Não há "down". Rollback é restore de backup (Cloud SQL point-in-time) ou uma migration corretiva nova.
- Um arquivo pode conter vários statements. O runner divide com `sqlparse.split()` (respeita `$$` e literais; dependência já transitiva do Django).
- `docker/initdb/` e o volume `initdb` do `docker-compose.yml` **saem** — o runner é o único mecanismo de schema, local e na nuvem.

### 4.2 Tabela de controle (por banco)

Mesmo nome e semântica do ledger que `ap-back-consulta-agenda`/`ap-back-contratos` já usam em `scripts/apply_schema.py`:

```sql
CREATE TABLE IF NOT EXISTS schema_aplicado (
  arquivo     TEXT PRIMARY KEY,          -- ex.: '0001_baseline.sql'
  checksum    TEXT NOT NULL,             -- sha256 do conteúdo
  aplicado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Criada pelo próprio runner na primeira execução (bootstrap). Arquivo já aplicado com checksum diferente → **erro** ("arquivo aplicado foi editado; crie um novo numerado"). A fonte da verdade é o disco, não o banco.

### 4.3 Comportamento

`migrate_tenants [--tenant <cnpj>] [--dry-run]`

Para cada tenant em `TENANT_IDS` (ou só o `--tenant`):

1. Carrega config, cria engine interno (sem cache do `get_db`).
2. Verifica `tenant_info.financiador_id == cnpj` — divergência aborta **esse tenant** e continua os demais, saindo com código ≠ 0 no fim.
3. Garante `schema_aplicado`.
4. Lista arquivos em `db/migrations/`, ordena; para cada um já registrado, confere o checksum (divergência aborta esse tenant); subtrai os já aplicados.
5. Para cada pendente, **em uma transação**: executa todos os statements, insere em `schema_aplicado`, commit. Falha → rollback daquele arquivo, aborta esse tenant, continua os demais.
6. `--dry-run` só imprime o que seria aplicado por tenant.

Log por tenant: `[migrate] ap_<cnpj>: 0002_x aplicada` / `nada pendente`.

### 4.4 Quando roda

- **Deploy:** passo do `cloudbuild.yaml` que executa `migrate_tenants` (Cloud Run Job com a mesma imagem) **antes** de rotear tráfego para a revisão nova. Migration falhou → deploy não avança.
- **Local:** na mão, `python manage.py migrate_tenants`.
- **Provisionamento:** chamado por `provisionar_tenant` (§3.1 passo 6).

Migrations devem ser compatíveis com a revisão anterior ainda em execução durante o rollout (aditivas: `ADD COLUMN` nullable/default, `CREATE TABLE`; nunca `DROP`/`RENAME` no mesmo deploy que remove o uso).

## 5. Guarda estrutural de isolamento em `get_db`

`shared/cloudsql_client.get_db(financiador_id)`, ao criar o engine (primeira chamada por tenant no processo):

```python
with engine.connect() as conn:
    dono = conn.execute(text("SELECT financiador_id FROM tenant_info")).scalar()
if dono != financiador_id:
    raise TenantMismatchError(financiador_id, dono)
```

- Divergência (ou `tenant_info` ausente/vazia): engine **não** é cacheado, exceção sobe, a requisição falha com 500. Middleware/handlers não tratam — é erro de configuração, deve ser ruidoso.
- Custo: uma query por tenant por processo (o engine é cacheado). Desprezível.
- Resultado: dois tenants apontando para o mesmo banco é **impossível em runtime**, não só no provisionamento.

## 6. Schema baseline: `0001_baseline.sql`

Como os bancos foram derrubados, **não há histórico a preservar**. Um único arquivo consolida o estado final, sem sequência fictícia de `ALTER`s.

### 6.1 Pré-requisito

A branch `worktree-optin-plan-10-11` (plans 10, 11, 13 + correções — `cliente`, `optin.cliente_id`, `erro_codigo`/`erro_mensagem`, cancelamento, fix de auth CERC, fix de N+1) **entra no `master`** como parte deste recomeço. O baseline reflete o schema dessa branch; o código que usa essas colunas vem junto.

### 6.2 Conteúdo (ordem de criação)

Copiado dos arquivos atuais da worktree (`00-cliente.sql`, `01-optin-schema.sql`, `02-idempotency-e-referencia.sql`), sem alteração de tipos ou nomes:

1. `cliente` — `id` (`cli_<ULID>`), `documento` (UNIQUE), `documento_tipo`, `nome`, `email`, `telefone`, `status` (default `'pending'`), `criado_em`, `atualizado_em`.
2. `optin` — como hoje, **com** `cliente_id TEXT NOT NULL REFERENCES cliente(id)`, `erro_codigo`, `erro_mensagem`; checks de vigência; índices `(documento_ufr, status)` e `(vigencia_inicio, vigencia_fim)`.
3. `optin_credenciadora`, `optin_arranjo` — PK composta, FK para `optin`.
4. `optout` — FK para `optin`, `referencia_externa` UNIQUE.
5. `cerc_requisicao`, `webhook_inbox` (`hash_dedupe` UNIQUE), `dominio_arranjo`.
6. `idempotency_key` — PK `(recurso, chave)`.
7. Sequences `optin_referencia_seq`, `optout_referencia_seq`.

Fora, de propósito: `consulta_agenda`/`consulta_agenda_ur` (SPEC-03), `tenant_info` (§3), `schema_aplicado` (§4). Tipos monetários, quando aparecerem, são `NUMERIC(18,2)` — nunca `float`.

### 6.3 Seed

`dominio_arranjo` precisa de dados para a validação de arranjo funcionar. Seed **não** é migration. O comando `sincronizar_dominio_arranjo` previsto no design original ainda não existe; até lá, um `seed_dominio_arranjo --tenant <cnpj>` mínimo com o código `99T` ("todos"). Fica para o plano decidir o mínimo viável.

## 7. Testes e dev local

- Postgres local = **PostgreSQL 17 instalado como serviço Windows** (`postgresql-x64-17`, porta 5432) — não há Docker nesta máquina. `docker-compose.yml` fica como alternativa para quem tiver Docker (container `postgres:17`, porta 5433, **sem** volume `initdb`). Cloud SQL também em Postgres 17 (§10.2), para local = nuvem.
- Role local `optin_app` (senha `optin`, `CREATEDB`) criada uma vez pelo superusuário `postgres`.
- `.env` local:
  ```
  TENANT_IDS=12345678000199
  TENANT_12345678000199_CONFIG={"database_url":"postgresql+pg8000://optin_app:optin@localhost:5432/ap_12345678000199", "cerc_client_id":"...", ...}
  ADMIN_DB_CONFIG={"database_url":"postgresql+pg8000://optin_app:optin@localhost:5432/postgres"}
  ```
- Setup de teste (fixture de sessão do pytest, ou script `make test-db`): `provisionar_tenant 12345678000199` (idempotente com `--existente`) + `migrate_tenants`. Mesmo runner, mesmo schema que produção.
- **A suíte automatizada nunca mais aponta para Cloud SQL real.** Testes de integração contra a CERC de homologação continuam existindo, mas são marcados (`@pytest.mark.homolog`) e rodam só sob demanda, contra um tenant local — a CERC é externa, o banco não precisa ser.
- Os testes existentes que hoje leem `FINANCIADOR_TESTE = "12345678000199"` continuam válidos sem alteração de lógica; só a config do tenant muda de Cloud SQL para `database_url`.

## 8. Fora de escopo (YAGNI explícito)

- Down migrations.
- Role Postgres por tenant.
- Instância Cloud SQL por tenant (config já permite; nada a construir agora).
- Painel/registro de tenants em banco (a lista é `TENANT_IDS`).
- Migração de dados dos bancos antigos — foram derrubados de propósito. Os 2 opt-ins reais perdidos na CERC (`OPTIN-2026-000000438/439`) são recriados manualmente se necessário, fora deste escopo.
- Trocar pg8000 por psycopg (Cloud SQL Connector só suporta pg8000/asyncpg para Postgres).
- Terraform / IaC declarativo; Cloud Build trigger automático por push (deploy é `gcloud builds submit` manual até haver mais de uma pessoa fazendo deploy); projeto `ap-optin-prod` (mesma receita do §10, quando a CERC liberar produção).
- Pub/Sub, Cloud Scheduler e o Cloud Run Job de reconciliação (design original §6) — sem código ainda; a infra deles entra junto com o código.

## 9. Riscos

1. **`sqlparse.split()` em SQL exótico** — cobre `$$`, literais e comentários; para o DDL deste projeto é suficiente. Se um dia falhar, o fallback é um arquivo por statement.
2. **Migration não-aditiva durante rollout** — mitigada por convenção (§4.4), não por ferramenta. Vale um item no checklist de PR.
3. **`TENANT_IDS` e `TENANT_*_CONFIG` dessincronizados** — `migrate_tenants` e `provisionar_tenant` falham cedo e alto; o health check (`/health`) pode passar a listar tenants com config ausente (decidir no plano).
4. **Merge da worktree** — há trabalho no `ap-front` (8 commits locais, nunca subidos) acoplado ao contrato da branch. O merge do back não obriga subir o front, mas o front local só funciona contra o back mergeado.
5. **Runbook executado à mão** (§10) — o estado da infra vive na cabeça de quem rodou e no runbook, não em código. Mitigação: cada passo do runbook é idempotente ou verificável (`gcloud ... describe`), e prod é obrigado a seguir o mesmo documento.

## 10. Infra GCP e deploy (homologação)

Segue o molde dos irmãos (`etl-back-elegibility/cloudbuild.yaml`): infra criada **uma vez com `gcloud`**, deploy por `cloudbuild.yaml`. Diferença: os comandos ficam num runbook versionado (`docs/runbooks/gcp-setup.md`), não em comentários do yaml, porque precisam ser repetidos para prod. O runbook é executado via CLI nesta máquina, passo a passo, com aprovação antes de cada criação de recurso.

### 10.1 Projeto e região

- Projeto **`ap-optin-homolog`** (prod: `ap-optin-prod`, mesma receita), separado do `registradora-506000` antigo.
- Região **`southamerica-east1`** (São Paulo): CERC e financiadores no Brasil; dado de EC no país evita discussão de residência de dados com financiador exigente. ~15–20% mais caro que `us-east1` — irrelevante nesta escala.
- APIs: `run`, `sqladmin`, `secretmanager`, `cloudbuild`, `artifactregistry`, `iam`.

### 10.2 Cloud SQL (uma instância)

- `optin-pg`, Postgres 17 (mesma versão do Postgres local, §7), `db-g1-small` (homolog; prod dimensiona à parte), IP público **sem redes autorizadas** — acesso só via Cloud SQL Connector (IAM + TLS), como os irmãos. Backups automáticos + point-in-time recovery ligados; `deletion-protection` ligado.
- Role de aplicação **`optin_app`** com `CREATEDB` (senha gerada, guardada só no Secret Manager). Serve para a app (§2.4) e para `ADMIN_DB_CONFIG` (§2.3, banco `postgres`). Sem role por tenant.
- Nenhum banco lógico criado pelo runbook — bancos são criados por `provisionar_tenant` (§3).

### 10.3 Secret Manager

| Segredo | Conteúdo | Lido por |
|---|---|---|
| `TENANT_IDS` | `cnpj1,cnpj2` | `migrate_tenants`, `provisionar_tenant` (runtime, via `shared/secrets.py`) |
| `TENANT_<cnpj>_CONFIG` | JSON §2.2 (`cloudsql_*` + `cerc_*`) | `get_tenant_config` (runtime) |
| `ADMIN_DB_CONFIG` | JSON §2.3 | `provisionar_tenant` (runtime) |
| `DJANGO_SECRET_KEY`, `IAM_JWT_PUBLIC_KEY` | valores | montados como env via `--set-secrets` no deploy |

Tudo que é por tenant é lido **em runtime** pela API do Secret Manager (`GOOGLE_CLOUD_PROJECT` setado) — adicionar tenant = criar segredos + executar o job de provisionamento, **sem redeploy**. Só o que é estático do serviço vai via `--set-secrets`.

### 10.4 Service accounts e IAM

- **`optin-run@`** (runtime do service e dos jobs): `roles/cloudsql.client`, `roles/secretmanager.secretAccessor`. Nada mais.
- **`optin-build@`** (Cloud Build): `roles/run.admin`, `roles/artifactregistry.writer`, `roles/logging.logWriter`, e `roles/iam.serviceAccountUser` **sobre** `optin-run@` (para deployar como ela).
- Artifact Registry `optin` (Docker) em `southamerica-east1`. Imagem `southamerica-east1-docker.pkg.dev/ap-optin-homolog/optin/optin-service:<SHORT_SHA>`. Diferença dos irmãos (`gcr.io/...:latest`): Container Registry está descontinuado e tag por SHA permite rollback por revisão.

### 10.5 Cloud Run

- **Service `optin-service`**: ingress público (o front chama do navegador; auth é o JWT), `--service-account optin-run@`, 1 CPU / 512 Mi, concurrency 20, min 0 / max 3 (homolog), timeout 60 s. Env não sensível: `ENVIRONMENT=homolog`, `GOOGLE_CLOUD_PROJECT`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `IAM_JWT_ISSUER`, `CERC_AUTH_URL`, `CERC_API_BASE_URL`. Segredos via `--set-secrets` (§10.3). Sem `--add-cloudsql-instances` — o Connector não usa o socket Unix.
- **Job `migrate-tenants`**: mesma imagem e SA, comando `python manage.py migrate_tenants`, 1 tarefa, sem retry (migration não é idempotente por acidente).
- **Job `provisionar-tenant`**: idem, comando `python manage.py provisionar_tenant`, `<cnpj>` passado em `--args` na execução (`gcloud run jobs execute provisionar-tenant --args=<cnpj> --wait`).

### 10.6 `cloudbuild.yaml` (ordem obrigatória)

1. `docker build` + `push` com tag `$SHORT_SHA`.
2. `gcloud run jobs update migrate-tenants --image <nova>` (o job aponta para a imagem nova, que contém as migrations novas).
3. `gcloud run jobs execute migrate-tenants --wait` — **falhou, o build para aqui**; a revisão antiga continua servindo.
4. `gcloud run deploy optin-service --image <nova>`.

Substituições: `_REGION`, `_SERVICE`, `_RUNTIME_SA`, `_CORS_ALLOWED_ORIGINS`, `_CERC_AUTH_URL`, `_CERC_API_BASE_URL`. Deploy é `gcloud builds submit --config cloudbuild.yaml` manual (trigger por push fica de fora, §8).

### 10.7 Primeiro deploy (ordem no runbook)

Projeto → APIs → Artifact Registry → Cloud SQL + role → SAs + IAM → segredos (`TENANT_IDS`, `ADMIN_DB_CONFIG`, `DJANGO_SECRET_KEY`, `IAM_JWT_PUBLIC_KEY`, `TENANT_<cnpj>_CONFIG` do 1º tenant) → criar os dois jobs e o service com uma imagem inicial (`gcloud builds submit`) → `provisionar-tenant <cnpj>` → smoke test (`/health` + um `GET /api/v1/optins` com JWT do tenant).

### 10.8 Custo estimado (homolog)

Cloud SQL `db-g1-small` em `southamerica-east1` ≈ US$ 35–45/mês (item dominante); Cloud Run com min 0 ≈ zero em idle; Secret Manager e Artifact Registry desprezíveis. Ordem de grandeza: **US$ 40–50/mês**.
