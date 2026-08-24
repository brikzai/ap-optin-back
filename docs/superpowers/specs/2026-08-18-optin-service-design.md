# optin-service — Design de implementação (Django)

> Status: aprovado em brainstorming, pronto para plano de implementação.
> Fonte normativa: `SPEC-01-optin-e-gestao.md` (Financiador CERC — Arranjos de Pagamento), anexada como referência completa de contrato/regras de negócio. Este documento cobre **decisões de arquitetura e stack**; não repete o conteúdo normativo já coberto lá (catálogo de erros §7, regras R1-R8, máquina de estados §9.1 etc. — ver a spec original).

## 1. Contexto e decisão de stack

Novo microserviço Python/Django, em GCP **próprio e isolado** (projeto, Cloud SQL, Pub/Sub e Scheduler dedicados — não reaproveita a infra do GCP "brikz" antigo).

Segue as **convenções de código** já validadas em outros back-ends Django da Brikz (`etl-back-ingestion-main`, `etl-back-elegibility`), sem reaproveitar a infra deles:

- **Sem Django ORM.** `DATABASES = {}` no settings; acesso a dados via `CloudSqlClient`, um wrapper próprio (SQLAlchemy + Cloud SQL Python Connector) com API estilo Supabase/PostgREST (`.table("optin").insert(...).execute()`).
- **Deploy em Cloud Run**, sem Celery. Assíncrono via **Pub/Sub** (eventos quase real-time) e **Cloud Scheduler** (jobs periódicos), no mesmo molde do `events/gcs` + `jobs/sweep-orphans` já existentes nos repos irmãos.
- **Auth interna** via middleware JWT próprio (RS256, chave pública do IdP corporativo) — não `django.contrib.auth`/DRF `TokenAuthentication`.
- **Schema versionado em SQL puro**, numerado (`docker/initdb/NN-descricao.sql`), sem framework de migration.

Essas decisões foram tomadas explicitamente pelo usuário durante o brainstorming, descartando as alternativas "Django idiomático (ORM + Celery)" e "híbrido".

## 2. Estrutura de pastas

```
optin/
├── manage.py
├── requirements.txt
├── Dockerfile / cloudbuild.yaml
├── docker/initdb/                # DDL versionado (01-optin-schema.sql = §6 da SPEC-01)
├── config/                       # settings.py (DATABASES={}), urls.py, wsgi.py, asgi.py
├── apps/
│   └── optin/
│       ├── views.py              # API interna (§5) + webhook receptor CERC (§4.4) + push endpoint Pub/Sub
│       ├── urls.py
│       ├── validation.py         # R1-R8, VAL001-010, anti-duplicidade §5.6
│       └── management/commands/  # retry_envio, expirar_optins,
│                                  # sincronizar_dominio_arranjo, reconciliacao_diaria
├── services/
│   └── cerc/
│       ├── token_provider.py     # OAuth2 client-credentials, cache 80% de expires_in, single-flight
│       └── client.py             # registrar_optin / atualizar_optin / encerrar_optin
│                                  # (consultar_agenda fica para o plano da SPEC-03)
└── shared/
    ├── cloudsql_client.py        # QueryBuilder (adaptado do padrão da casa; sem dependência cruzada de repo)
    ├── jwt_auth.py                # middleware JWT do IdP corporativo (API interna)
    ├── pubsub_client.py           # publish helper (webhook inbox) + verificação OIDC do push
    └── secrets.py                 # leitura de segredos via Secret Manager
```

Decisões YAGNI explícitas (descartadas em favor de simplicidade, revisitar só se a necessidade aparecer de fato):

- Sem interface/porta formal `CercOptInGateway` — só existe o adapter REST hoje; o adapter de arquivo é "futuro, não agora" (SPEC-01 §1.2). `services/cerc/client.py` expõe funções diretas.
- Sem camada de domínio separada (`optin_domain/`) — regras locais cabem em `apps/optin/validation.py`.
- Sem pasta `jobs/` própria na raiz — jobs são management commands do Django (`apps/optin/management/commands/`), padrão já usado nos repos irmãos.
- Sem DRF ViewSets/serializers — function-based views + validação manual, espelhando o padrão observado nos outros serviços (nenhum deles usa DRF além de `rest_framework` instalado para renderer JSON).

## 3. Camada de dados

- Tabelas do §6 da SPEC-01 usadas por **este** plano (`optin`, `optin_credenciadora`, `optin_arranjo`, `optout`, `cerc_requisicao`, `webhook_inbox`, `dominio_arranjo`) viram `docker/initdb/01-optin-schema.sql`, copiadas da spec quase literalmente (ela já é DDL Postgres válida). `consulta_agenda`/`consulta_agenda_ur` **ficam de fora** — a SPEC-01 §4.3/§5.5 diz explicitamente que a consulta de agenda foi movida para a SPEC 03; criar essas tabelas agora seria schema sem nenhum código que as use (YAGNI).
- Instância Cloud SQL dedicada a este serviço; schema único (`public`), sem necessidade de multi-schema já que não há outro serviço gravando na mesma instância.
- Tipos monetários: `NUMERIC(18,2)` no Postgres, `decimal.Decimal` em Python. **Proibido `float`/`double`** em qualquer campo de valor (requisito explícito da SPEC-01, verificado estaticamente conforme §11.4).
- Auditoria (§8 da SPEC-01): sem tabela nova — cabe em `cerc_requisicao` (trilha de chamadas) + evento de auditoria gravado em Python (`validation.py`/`views.py`, não trigger de banco) a cada registro/alteração/encerramento de opt-in, com diff de campos.

## 4. Autenticação, cliente CERC e API interna

- **Token CERC** (OAuth2 client-credentials): `services/cerc/token_provider.py`. Cache em memória por processo, renovação proativa a 80% de `expires_in`, single-flight via lock por processo. Em `401`, invalida cache e repete a chamada uma única vez (SPEC-01 §3).
- **`client_secret`**: Google Secret Manager via `shared/secrets.py`. Nunca em texto plano no repo. `.env` local (gitignorado) guarda os valores reais de homologação para desenvolvimento; `.env.example` commitado só com as chaves.
- **Cliente REST CERC** (`services/cerc/client.py`): `registrar_optin`, `atualizar_optin`, `encerrar_optin`, `consultar_agenda`. Cada chamada grava uma linha em `cerc_requisicao` **antes** de interpretar a resposta.
- **API interna** (`apps/optin/views.py`, base `/api/v1`, conforme SPEC-01 §5): function-based views, autenticadas pelo middleware JWT corporativo (`shared/jwt_auth.py`). Rotas isentas: `health` e o push endpoint do Pub/Sub (que usa verificação OIDC própria, não o JWT de usuário).
- `Idempotency-Key` obrigatório nos `POST` mutantes, com dedupe por coluna/índice.

## 5. Ambientes e credenciais

- **Homologação:** credenciais OAuth2 já fornecidas pelo usuário (`CERC_CLIENT_ID`, `CERC_CLIENT_SECRET`, `CERC_AUTH_URL=https://api.int.cerc.com/oauth/token`, `CERC_API_BASE_URL=https://ap-homolog.cerc.inf.br`) — batem com os hosts descritos na SPEC-01 §3. Vivem só no `.env` local do desenvolvedor e no Secret Manager do ambiente de homolog; nunca commitadas.
- **Produção:** hosts (`CERC_AUTH_URL`/`CERC_API_BASE_URL`) e credenciais ainda **a confirmar com a CERC** — mesmo risco já registrado na SPEC-01 §12.1-§12.2 (rate limits e grade horária não publicados).

## 6. Assíncrono, jobs e deploy

- **Webhook CERC → Pub/Sub:** handler grava em `webhook_inbox` **antes** de publicar no tópico `optin-webhook-inbox` (se o publish falhar, um job de varredura recupera pelo `processado_em IS NULL`). Responde `202` em <200ms — zero lógica de negócio na rota. Push subscription bate em `POST /api/v1/events/webhook-inbox`, verificado por OIDC (adaptação de `shared/eventarc_auth.py` para push do Pub/Sub).
- **Jobs periódicos** (Cloud Scheduler → endpoint HTTP interno protegido por OIDC):
  - `retry_envio` — opt-ins `PENDENTE` > 1h e `FALHA_ENVIO` elegíveis a reenvio (backoff já resolvido pela política de retentativa da SPEC-01 §9.2).
  - `expirar_optins` — diário, 00:15 America/Sao_Paulo (SPEC-01 §9.1).
  - `sincronizar_dominio_arranjo` — diário.

  (`fechar_consultas_agenda`, quiet period de 90s e hard timeout de 15min — SPEC-01 §9.3 — ficam para o plano da SPEC-03, junto com a consulta de agenda.)
- **Reconciliação diária completa** (SPEC-01 §9.4): único job que roda como **Cloud Run Job** dedicado (não endpoint HTTP), disparado pelo Cloud Scheduler via Admin API (`jobs.run`) — evita competir por worker com o tráfego web durante uma comparação potencialmente pesada.
- **Deploy:** Cloud Run + `cloudbuild.yaml`, mesmo molde dos repos irmãos.

## 7. Testes e observabilidade

- `pytest` + `pytest-django` para settings/urls, mesmo sem ORM.
- Unitários: normalização de documento, `VAL001`-`VAL010`, parser do `207` multi-status, `TokenProvider` (renovação a 80%, single-flight sob concorrência).
- Integração: cenários IT-01 a IT-13 (SPEC-01 §11.2) contra homologação real (credenciais já disponíveis) ou mock local.
- Carga: webhook receptor sustentando 500 req/s por 5min, p99 < 200ms, zero respostas fora de 2xx (SPEC-01 §11.3).
- Observabilidade: métricas Prometheus (SPEC-01 §10) via `django-prometheus` ou exportação manual em `/metrics`. Alertas ficam na configuração de monitoring do GCP, fora do código.

## 8. Riscos e pendências (herdados da SPEC-01 §12)

1. Rate limits e tamanho máximo de lote de `/opt_in`, `/opt_out`, `/v15/agenda/consultar` não publicados — confirmar com a CERC antes de dimensionar o batcher.
2. Grade horária operacional do canal API não confirmada.
3. Sem endpoint de consulta de opt-in por protocolo conhecido — reconciliação depende de AP022/AP023 (canal arquivo, fora do escopo desta primeira implementação).
4. Semântica exata de `104804` (duplicado no catálogo CERC com duas descrições).
5. Obrigatoriedade de `documentoTitular` quando `documentoUsuarioFinalRecebedor` é CNPJ raiz — a confirmar.
6. Hosts e credenciais de produção da CERC ainda não confirmados (só homologação validada nesta sessão).
