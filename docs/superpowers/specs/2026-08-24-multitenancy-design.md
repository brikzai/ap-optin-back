# Multi-tenancy — Design de implementação (um tenant por financiador)

> **Substituída em parte** por `2026-09-02-database-multitenant-migrations-design.md`: registro de tenants (`TENANT_IDS`), banco lógico por tenant (`ap_<cnpj>`), provisionamento, migrations e guarda `tenant_info`. O formato de `TENANT_{cnpj}_CONFIG` continua válido, acrescido de `database_url` opcional.

> **Status:** aprovado em brainstorming, pronto para plano de implementação.
> **Fonte normativa:** `SPEC-01-optin-e-gestao.md` (§3 autenticação, §4 contratos CERC, §5 API interna, §6 modelo de dados) + `2026-08-18-optin-service-design.md` (decisões de stack já tomadas — sem ORM, sem DRF, Cloud Run). Este documento **estende** essas decisões para múltiplos tenants (financiadores); não repete o que já está lá.

## 1. Contexto e decisão

O serviço vai atender **múltiplos financiadores**, cada um como um **tenant isolado**. Decisões tomadas em brainstorming (2026-08-24):

- **Resolução de tenant:** claim no JWT do IdP corporativo, não header nem path.
- **Identidade do tenant:** o próprio `cnpjFinanciador` (14 dígitos) — não um ID opaco separado. Resolve tenant e já fornece o `cnpjFinanciador` para os payloads CERC no mesmo campo.
- **Isolamento de dados:** um banco/instância Cloud SQL **separado por tenant** (isolamento físico, não coluna `financiador_id` em tabelas compartilhadas).
- **Registro de config por tenant:** Secret Manager, um segredo por tenant — reaproveitando `shared/secrets.py` sem alterá-lo.
- **Escala/provisionamento:** poucos tenants (dezenas), provisionamento manual/scriptado. Sem automação de onboarding self-service neste momento (YAGNI).
- **Dev/test:** um único "tenant de desenvolvimento" fixo — o Cloud SQL atual (`registradora-506000:us-east1:app-db`) vira esse tenant.

Isso invalida uma premissa registrada no Plan 08 (Task 7, "Riscos e pendências"): `CERC_CNPJ_SOLICITANTE`/`CERC_CNPJ_FINANCIADOR` como variáveis de ambiente fixas, um financiador por deploy. A partir deste plano, cada requisição resolve seu próprio tenant.

## 2. Resolução de tenant — `shared/jwt_auth.py`

O decorator `jwt_required` (já implementado, commit `37169f3`) passa a exigir o claim `financiador_id` em todo JWT válido:

- Claim ausente → `401` (mesmo formato de erro já usado para token ausente/expirado/issuer errado).
- Claim presente mas não é uma string de 14 dígitos numéricos → `401` (checagem de formato leve, sem dígito verificador — o CNPJ já vem de um IdP corporativo confiável; validação de DV completa continua sendo regra de negócio de `apps/optin/validation.py`, não desta camada de autenticação).
- Claim válido → `request.financiador_id = claims["financiador_id"]`, além do já existente `request.jwt_claims`.

Nenhuma outra rota (`health`) exige JWT, logo nenhuma outra rota tem `request.financiador_id` — código downstream só deve lê-lo dentro de views decoradas com `@jwt_required`.

## 3. Config por tenant — `shared/secrets.py` (sem alterar o arquivo)

`get_secret(name)` já tem exatamente a dualidade necessária: sem `GOOGLE_CLOUD_PROJECT`, lê a env var de mesmo nome; com o projeto setado, lê do Secret Manager (versão `latest`). Um segredo por tenant é só uma convenção de nome em cima disso — **nenhuma mudança em `shared/secrets.py`**.

Novo módulo `shared/tenant_config.py`:

```python
def get_tenant_config(financiador_id: str) -> dict:
    """Lê e parseia o segredo TENANT_{financiador_id}_CONFIG (JSON).

    Dev local: env var TENANT_{financiador_id}_CONFIG no .env (via
    shared.secrets.get_secret, sem GOOGLE_CLOUD_PROJECT). Produção/homolog:
    Secret Manager, um segredo por tenant, mesmo nome.
    """
```

Formato do JSON (chaves obrigatórias, todas usadas pelas seções 4 e 5):

```json
{
  "cloudsql_connection_name": "registradora-506000:us-east1:app-db",
  "cloudsql_db_user": "app",
  "cloudsql_db_password": "...",
  "cloudsql_db_name": "app",
  "cerc_client_id": "...",
  "cerc_client_secret": "...",
  "cerc_cnpj_solicitante": "12345678000199"
}
```

`cnpj_financiador` **não** é um campo do JSON — é sempre o próprio `financiador_id` (evita duplicação que pode divergir). Config é cacheada em memória por processo, sem TTL (mesma filosofia do cache de token do Plan 06 — restart do processo pega mudanças; rotação de segredo em produção implica reiniciar as instâncias Cloud Run, aceito como custo conhecido, não resolvido aqui).

## 4. Banco por tenant — `shared/cloudsql_client.py`

`get_db()` (sem argumento, singleton único) vira `get_db(financiador_id: str)`, com um cache `dict[str, CloudSQLClient]` por tenant. Cada entrada nova constrói seu próprio engine SQLAlchemy a partir do `get_tenant_config(financiador_id)` — a lógica de `_create_engine()` (Cloud SQL Python Connector + pg8000) muda de ler `CLOUDSQL_CONNECTION_NAME`/`CLOUDSQL_DB_USER`/etc. de `os.environ` para ler do dict de config retornado por `get_tenant_config`.

**`LOCAL_DATABASE_URL` é removido.** Não há mais um caminho "local Postgres direto via docker-compose" separado do caminho "Cloud SQL por tenant" — o tenant de dev usa o mesmo mecanismo `get_tenant_config` que qualquer tenant real, só que via env var local (§3). Isso simplifica para um único código-caminho, sem branch dev-vs-prod dentro de `cloudsql_client.py`.

Como o isolamento é por **banco inteiro**, as tabelas do §6 da SPEC-01 (`optin`, `optin_credenciadora`, `optin_arranjo`, `optout`, `cerc_requisicao`, `webhook_inbox`, `dominio_arranjo`) **não ganham coluna `financiador_id`** — o schema atual (`docker/initdb/*.sql`) roda sem alteração, replicado uma vez por banco de tenant.

## 5. CERC por tenant — `services/cerc/token_provider.py` e `services/cerc/client.py`

- `token_provider.py`: `_cache`/`_lock` (globais, um por processo) viram `dict[str, dict]`/`dict[str, threading.Lock]` chaveados por `financiador_id`. `get_cerc_token(financiador_id)`, `invalidate_token(financiador_id)`. `client_id`/`client_secret` vêm de `get_tenant_config(financiador_id)` em vez de `os.environ["CERC_CLIENT_ID"]`/`get_secret("CERC_CLIENT_SECRET")`. `CERC_AUTH_URL` **continua** env var global — é o host OAuth do ambiente (homolog/produção), não varia por tenant.
- `client.py`: `registrar_optin`/`atualizar_optin`/`encerrar_optin` ganham `financiador_id` como **primeiro parâmetro** (antes de `payload`/`correlacao_id`). Usado para: (a) buscar o token certo via `get_cerc_token(financiador_id)`; (b) gravar a auditoria em `cerc_requisicao` do banco do tenant certo, via `get_db(financiador_id)`. `CERC_API_BASE_URL` continua env var global, mesma razão do auth host.

Isso é uma mudança de assinatura em código já mergeado (Plan 07, commit `2d8f198` e anteriores) — aceita conscientemente: melhor corrigir agora do que depois de mais 5 tasks do Plan 08 construídas sobre a API de single-tenant.

## 6. Impacto no Plan 08 (já escrito, parcialmente executado)

- **Task 1** (JWT, `37169f3`) precisa de um adendo: claim `financiador_id` (§2 acima). Não refaz o arquivo do zero — soma ao que já existe.
- **Task 2** (schema de idempotência) **não muda** — a tabela `idempotency_key` roda dentro do banco do tenant como qualquer outra tabela de negócio, sem coluna nova.
- **Task 3** (`apps/optin/idempotency.py`) **muda**: `buscar_resposta_em_cache`/`guardar_resposta` chamam `get_db()` diretamente, então ganham `financiador_id` como primeiro parâmetro; o decorator `idempotente(recurso)` passa a ler `request.financiador_id` (populado por `jwt_required`, que roda antes dele em todo empilhamento de decorators do Plan 08) e repassar para as duas funções.
- **Tasks 4, 5** (validação local, mapeamento CERC) **não mudam** — são funções puras, sem acesso a banco ou à CERC.
- **Task 6** (`repository.py`): toda função ganha `financiador_id` como primeiro parâmetro, repassado para `get_db(financiador_id)`.
- **Tasks 7, 9, 10** (views `criar_optin`, `atualizar_optin_view`, `optin_optout`): usam `request.financiador_id` (de `jwt_required`) em vez de `os.environ["CERC_CNPJ_SOLICITANTE"]`/`os.environ["CERC_CNPJ_FINANCIADOR"]`; toda chamada a `repository.*` e a `services.cerc.client.*` passa `request.financiador_id` como primeiro argumento. `cnpjSolicitante` no payload CERC vem de `get_tenant_config(financiador_id)["cerc_cnpj_solicitante"]`.
- **Task 8** (GET list/detail): sem chamada à CERC, mas `repository.listar`/`buscar_por_id` também ganham `financiador_id` (Task 6), então as views passam `request.financiador_id`.
- **Task 11** (fechamento): inalterada em intenção, roda depois do Plan 09.

O texto do Plan 08 será atualizado (Tasks 6-10) depois que o Plan 09 abaixo estiver implementado — não antes, para não editar um plano em cima de uma fundação que ainda não existe.

## 7. Plano de implementação (Plan 09 — próximo passo)

Ordem: `shared/tenant_config.py` → `shared/cloudsql_client.py` (retrofit) → `services/cerc/token_provider.py` (retrofit) → `services/cerc/client.py` (retrofit) → `shared/jwt_auth.py` (adendo do claim). Cada retrofit deve rodar os testes já existentes daquele módulo (adaptados para passar `financiador_id`) antes de seguir para o próximo, para nunca ficar num estado quebrado no meio do caminho.

## 8. Testes e dev/homolog

- Tenant de dev/teste: um `financiador_id` fixo (reaproveita `12345678000199`, já usado em todos os testes existentes) apontando, via `TENANT_12345678000199_CONFIG` no `.env`, para o Cloud SQL atual (`registradora-506000:us-east1:app-db`) com as credenciais que já estão soltas no `.env` hoje (`CLOUDSQL_DB_USER`/`PASSWORD`/`NAME`) e as credenciais CERC que já estão soltas (`CERC_CLIENT_ID`/`SECRET`). Migração do `.env`: consolidar esses valores soltos num único `TENANT_12345678000199_CONFIG` JSON.
- Testes de multi-tenancy de verdade (dois tenants isolados) usam **configs fake** (`monkeypatch.setenv` com dois `TENANT_{cnpj}_CONFIG` diferentes apontando pro **mesmo** Cloud SQL de dev mas com `cloudsql_db_name`/schema diferentes, ou simplesmente dois `financiador_id` diferentes cacheados em processo com configs mockadas) — não provisiona uma segunda instância Cloud SQL real só para teste.
- Suite existente (28 testes de Plans 01-07 + 7 de Task 1) precisa ser adaptada: todo `get_db()` sem argumento e toda chamada a `client.registrar_optin(payload, correlacao_id=...)` sem `financiador_id` para de compilar — isso é esperado e faz parte do Plan 09, não um efeito colateral a evitar.

## 9. Contrato com o futuro front de gestão de tenants

O provisionamento de tenants (criar instância Cloud SQL, rodar `docker/initdb/*.sql`, gravar o segredo) será gerenciado por um front apartado, fora deste repositório. Este documento define o **contrato** que esse front precisa seguir para este serviço enxergar um tenant: nome do segredo (`TENANT_{cnpj}_CONFIG`, §3) e o formato JSON (§3). Não existe hoje nenhum formato pré-existente do lado de lá — este é o formato de referência. Se esse front vier a ser construído antes deste serviço, qualquer mudança de formato exige atualizar `shared/tenant_config.py` e este documento juntos.

## 10. Riscos e pendências

- Rotação de segredo (`client_secret`, senha do banco) de um tenant não invalida o cache em memória do processo — precisa de restart. Aceito por ora; um mecanismo de invalidação por evento é trabalho futuro se isso incomodar operacionalmente.
- Nenhuma automação de onboarding de tenant (criar instância Cloud SQL, rodar `docker/initdb/*.sql`, gravar o segredo) — processo manual/scriptado fora do código da aplicação. Se o número de tenants crescer muito além de "dezenas", isso vira um projeto próprio.
- `cerc_cnpj_solicitante` assume que cada tenant tem **um único** CNPJ solicitante fixo. Se um financiador algum dia operar com mais de um solicitante (ex.: mais de um Prestador de Serviço atuando por ele), esse campo vira insuficiente — não há evidência disso hoje, tratado como YAGNI.
