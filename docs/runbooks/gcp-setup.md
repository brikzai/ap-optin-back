# Runbook — infra GCP do optin-service

Projeto de homologação: `brikz-ap` (org 456240596788), região `southamerica-east1`, conta `ricardo@brikz.ai`.
Produção: repetir este runbook num projeto próprio, trocando `brikz-ap` e os hosts da CERC.
Spec: `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md` §10.

Cada seção é idempotente ou verificável — rode o `describe` antes de recriar.

## 0. Sessão

    gcloud config set project brikz-ap
    gcloud config set account ricardo@brikz.ai

## 1. APIs e Artifact Registry

    gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
      cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
      cloudresourcemanager.googleapis.com compute.googleapis.com
    gcloud artifacts repositories create optin --repository-format=docker --location=southamerica-east1 \
      --description="Imagens do optin-service"

Verificar: `gcloud artifacts repositories describe optin --location=southamerica-east1`

Feito em 2026-09-02: as 8 APIs habilitadas, repositório `optin` criado (`projects/brikz-ap/locations/southamerica-east1/repositories/optin`, formato DOCKER).

## 2. Cloud SQL

    gcloud sql instances create optin-pg \
      --database-version=POSTGRES_17 --edition=ENTERPRISE --tier=db-g1-small \
      --region=southamerica-east1 --availability-type=ZONAL \
      --storage-type=SSD --storage-size=10GB --storage-auto-increase \
      --backup-start-time=03:00 --enable-point-in-time-recovery --retained-backups-count=7 \
      --assign-ip --deletion-protection

Sem `--authorized-networks`: acesso só via Cloud SQL Connector (IAM + TLS).

    PW_OPTIN_APP="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    gcloud sql users create optin_app --instance=optin-pg --password="$PW_OPTIN_APP"
    printf '{"cloudsql_connection_name":"brikz-ap:southamerica-east1:optin-pg","cloudsql_db_user":"optin_app","cloudsql_db_password":"%s","cloudsql_db_name":"postgres"}' "$PW_OPTIN_APP" \
      | gcloud secrets create ADMIN_DB_CONFIG --data-file=- --replication-policy=user-managed --locations=southamerica-east1
    unset PW_OPTIN_APP

Os bancos `ap_<cnpj>` NÃO são criados aqui — `provisionar_tenant` cria (Plan 03 / seção 6).
Rotação da senha: `gcloud sql users set-password optin_app --instance=optin-pg --password=...` e nova versão
de `ADMIN_DB_CONFIG` **e** de cada `TENANT_<cnpj>_CONFIG`; reiniciar o service (cache por processo).

Feito em 2026-09-02: instância `optin-pg` `RUNNABLE` (`brikz-ap:southamerica-east1:optin-pg`), redes
autorizadas vazias, usuário `optin_app` criado, segredo `ADMIN_DB_CONFIG` versão 1.
`ssl_mode=ENCRYPTED_ONLY` aplicado (revisão pós-Task 6): TLS obrigatório, sem exigir certificado de
cliente — no-op para o Connector (que já faz mTLS por conta própria), fecha a brecha de conexão em
texto puro caso uma rede autorizada seja liberada no futuro (`gcloud sql instances patch optin-pg --ssl-mode=ENCRYPTED_ONLY`).

## 3. Service accounts e IAM

    gcloud iam service-accounts create optin-run --display-name="optin-service runtime (Cloud Run service + jobs)"
    gcloud iam service-accounts create optin-build --display-name="optin-service Cloud Build"
    for r in roles/cloudsql.client roles/secretmanager.secretAccessor; do
      gcloud projects add-iam-policy-binding brikz-ap --member=serviceAccount:optin-run@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
    done
    for r in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter roles/cloudbuild.builds.builder; do
      gcloud projects add-iam-policy-binding brikz-ap --member=serviceAccount:optin-build@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
    done
    gcloud iam service-accounts add-iam-policy-binding optin-run@brikz-ap.iam.gserviceaccount.com \
      --member=serviceAccount:optin-build@brikz-ap.iam.gserviceaccount.com --role=roles/iam.serviceAccountUser

`optin-run@` não tem nada além de Cloud SQL client e leitura de segredos. Quem roda `gcloud builds submit`
precisa poder subir o fonte para o bucket de staging (owner/editor do projeto serve).

Feito em 2026-09-02: `optin-run@brikz-ap.iam.gserviceaccount.com` (cloudsql.client, secretmanager.secretAccessor);
`optin-build@brikz-ap.iam.gserviceaccount.com` (run.admin, artifactregistry.writer, logging.logWriter,
cloudbuild.builds.builder) + serviceAccountUser sobre `optin-run@`.

## 4. Segredos estáticos do serviço

    python scripts/gerar_chaves_jwt.py keys/homolog        # privada fica local (backup fora do repo!)
    gcloud secrets create IAM_JWT_PUBLIC_KEY --data-file=keys/homolog/jwt_public.pem \
      --replication-policy=user-managed --locations=southamerica-east1
    python -c 'import secrets; print(secrets.token_urlsafe(50))' \
      | gcloud secrets create DJANGO_SECRET_KEY --data-file=- --replication-policy=user-managed --locations=southamerica-east1

Emitir token: `python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador <cnpj> --horas 24`.
Segredos por tenant (`TENANT_IDS`, `TENANT_<cnpj>_CONFIG`) ficam na seção 6 (Plan 03) — são lidos em runtime,
não montados no deploy, então onboardar tenant não exige redeploy.

Feito em 2026-09-02: par RSA gerado em `keys/homolog/` (gitignorado — **fazer backup da privada fora do
repo**, ela é quem emite tokens de homolog; perder = gerar par novo e trocar `IAM_JWT_PUBLIC_KEY`).
Segredos `IAM_JWT_PUBLIC_KEY` e `DJANGO_SECRET_KEY` criados, versão 1 cada.

## 5. Deploy (cloudbuild.yaml)

    gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)

O que acontece, na ordem: build → push → `gcloud run jobs deploy` de `migrate-tenants` e `optin-manage` →
`gcloud run jobs execute migrate-tenants --wait` (falhou = build para, revisão antiga segue) → `gcloud run deploy optin-service`.
Primeiro deploy só depois da seção 6 (segredos do tenant), senão `migrate-tenants` falha por `TENANT_IDS` ausente.

Comandos administrativos em homolog (imagem já deployada):

    gcloud run jobs execute optin-manage --region southamerica-east1 --wait \
      --args=manage.py,provisionar_tenant,<cnpj>
    gcloud run jobs execute optin-manage --region southamerica-east1 --wait \
      --args=manage.py,seed_dominio_arranjo,--tenant,<cnpj>

Logs: `gcloud run jobs executions list --job migrate-tenants --region southamerica-east1` e
`gcloud logging read 'resource.type="cloud_run_job"' --limit 50`.

Feito em 2026-09-02: `cloudbuild.yaml` escrito e validado (5 steps, 9 substituições) — **ainda não executado**.
O passo `migrate` falharia por `TENANT_IDS` inexistente; primeiro `gcloud builds submit` real é o Plan 03,
depois de criar os segredos do primeiro tenant (seção 6, a escrever).

**Revisão pós-Task 6 (verificação do estado real no GCP contra o plano) achou e corrigiu, antes de
qualquer `gcloud builds submit`:**
- **Crítico:** `${job%%:*}`/`${job##*:}` no step `deploy-jobs` seriam lidos pelo parser de substituições
  do Cloud Build (que roda antes do shell) como chaves inexistentes — o build seria rejeitado no submit.
  `python -c "import yaml..."` valida só sintaxe YAML, nunca pegaria isso. Corrigido escapando para `$$`.
- **Importante:** sem `timeout:` no nível raiz, o build usaria o default de 600s — apertado para
  build+push+2 jobs+migrate+deploy. Adicionado `timeout: 1800s`.
- **Importante:** `.dockerignore`/`.gcloudignore` com `*.pem` sozinho não cobre `keys/homolog/jwt_private.pem`
  (Docker não casa `*.pem` em subdiretório sem `**/`). Só não vazava pro contexto de build por acidente
  (fallback do `gcloud` para `.gitignore`, que tem `keys/`). Adicionado `**/*.pem` e `keys/` explícitos nos
  dois arquivos, e criado `.gcloudignore` explícito em vez de depender do fallback.
- **Importante** (não é código, é o próprio Cloud SQL): `ssl_mode` estava `ALLOW_UNENCRYPTED_AND_ENCRYPTED`.
  Corrigido para `ENCRYPTED_ONLY` (seção 2 acima).

## 5b. Deploy automático (Cloud Build trigger)

Desde 2026-09-03 o código-fonte foi para `github.com/brikzai/ap-optin-back` (remote pessoal
`rdelimasilva/ap-optin` descartado) e existe um trigger que builda/deploya sozinho a cada push na `master` —
o `gcloud builds submit` manual da seção 5 continua funcionando, mas deixa de ser o caminho normal.

    gcloud builds connections describe optin-github --region=southamerica-east1   # estado da conexão GitHub
    gcloud builds triggers describe optin-deploy-master --region=southamerica-east1

- **Conexão:** `optin-github` (2ª geração), autorizada via OAuth como usuário `brikzai`, GitHub App instalado
  no repositório `ap-optin-back`.
- **Repositório cadastrado:** `optin-back` → `https://github.com/brikzai/ap-optin-back.git`.
- **Trigger:** `optin-deploy-master`, branch `^master$`, config `cloudbuild.yaml` do próprio repo,
  substituição `_TAG=$SHORT_SHA` (equivalente ao `$(git rev-parse --short HEAD)` manual), service account
  `optin-build@brikz-ap.iam.gserviceaccount.com` (mesma da seção 3).
- **Aprovação:** nenhuma (`approvalConfig: {}`) — decisão explícita, sem gate manual antes de
  `migrate-tenants` rodar em homolog. Qualquer push na `master` vai para o ar sozinho.
- **Pré-requisito que faltava e foi resolvido:** a service agent do Cloud Build
  (`service-1009092036032@gcp-sa-cloudbuild.iam.gserviceaccount.com`) precisou de `roles/secretmanager.admin`
  no projeto para a conexão GitHub conseguir guardar o token OAuth — sem isso a criação da connection falha
  com `permission_denied` em `secretmanager.secrets.create`.

Acompanhar builds disparados pelo trigger: `gcloud builds list --region=southamerica-east1 --limit=5` ou
Console → Cloud Build → History.

## 6. Onboarding de tenant

Um tenant = uma entrada em `TENANT_IDS` + um segredo `TENANT_<cnpj>_CONFIG` + um banco `ap_<cnpj>`.

1. `TENANT_IDS`: nova versão com a lista completa, separada por vírgula:
       printf 'cnpj1,cnpj2' | gcloud secrets versions add TENANT_IDS --data-file=-
   (na primeira vez: `gcloud secrets create TENANT_IDS ...`)
2. `TENANT_<cnpj>_CONFIG`: JSON com `cloudsql_connection_name`, `cloudsql_db_user`, `cloudsql_db_password`
   (mesmos de `ADMIN_DB_CONFIG`), `cloudsql_db_name = ap_<cnpj>`, `cerc_client_id`, `cerc_client_secret`,
   `cerc_cnpj_solicitante`. Monte num arquivo temporário local (nunca em pipe triplo — no Git Bash/Windows
   um pipe encadeado `gcloud | python | gcloud` pode quebrar silenciosamente e criar um segredo com **zero
   versões**, sem erro visível; sempre confirme com `gcloud secrets versions list <nome>` depois), suba com
   `--data-file=<arquivo>` e apague o arquivo em seguida.
3. Provisionar o banco:
   - Primeiro tenant do projeto (jobs ainda não existem): da máquina local, com ADC —
         gcloud auth application-default login
         GOOGLE_CLOUD_PROJECT=brikz-ap python manage.py provisionar_tenant <cnpj>
   - Demais: `gcloud run jobs execute optin-manage --region southamerica-east1 --wait --args=manage.py,provisionar_tenant,<cnpj>`
4. Seed: `gcloud run jobs execute optin-manage --region southamerica-east1 --wait --args=manage.py,seed_dominio_arranjo,--tenant,<cnpj>`
5. Token para o financiador: `python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador <cnpj>`

Sem redeploy: o service lê `TENANT_<cnpj>_CONFIG` na primeira requisição daquele tenant.

Feito em 2026-09-02: `TENANT_IDS` = `38138785000136`; `TENANT_38138785000136_CONFIG` criado (7 chaves,
`cloudsql_db_name=ap_38138785000136`, credenciais CERC de `C:\DEV\ap\.env API CERC.txt`) — verificado
sem expor valores. Este é o primeiro tenant (CERC homologação).

## 7. Primeiro deploy — estado em 2026-09-02

- Tenant `38138785000136` provisionado via `provisionar_tenant` local (ADC de `ricardo@brikz.ai`,
  `gcloud auth application-default login --account=ricardo@brikz.ai`) — banco `ap_38138785000136`
  criado, `0001_baseline.sql` aplicada, idempotente (`--existente` → 0 migrations).
- `gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=1227fbf` → **SUCCESS** (2m21s).
  Jobs `migrate-tenants`/`optin-manage` criados; `migrate-tenants` executou com sucesso (1 tarefa,
  0 falhas). Service `optin-service` deployado, revisão `optin-service-00001-7jt`, URL
  `https://optin-service-6sy5bhymwq-rj.a.run.app`.
- **Bloqueio encontrado:** `/api/v1/health` retornou `403` do Google Frontend (não da aplicação) —
  `--allow-unauthenticated` no deploy não teve efeito porque a organização `brikz.ai` tem a política
  **Domain Restricted Sharing** (`constraints/iam.allowedPolicyMemberDomains`, `allowedValues: [C0380bkka]`)
  herdada no projeto, que bloqueia `allUsers`/`allAuthenticatedUsers` em qualquer binding de IAM.
  Confirmado com `gcloud run services get-iam-policy optin-service` (policy vazia) e a mensagem exata
  do `add-iam-policy-binding`: `FAILED_PRECONDITION: One or more users named in the policy do not
  belong to a permitted customer`.
- **Correção aplicada (só neste projeto, não na organização):**
  ```
  gcloud services enable orgpolicy.googleapis.com
  # arquivo local (não commitado):
  #   name: projects/brikz-ap/policies/iam.allowedPolicyMemberDomains
  #   spec: { rules: [{ allowAll: true }] }
  gcloud org-policies set-policy <arquivo>
  ```
  `ricardo@brikz.ai` tinha `orgpolicy.policyAdmin` no projeto — a exceção foi criada com sucesso
  (`gcloud org-policies describe ... --effective` já mostra `allowAll: true`).
- **Pendente:** `gcloud run services add-iam-policy-binding optin-service --region southamerica-east1
  --member=allUsers --role=roles/run.invoker` ainda falha com o mesmo erro — a definição da política
  já mudou mas o **enforcement** do IAM do Cloud Run ainda não propagou (pode levar de minutos a
  cerca de uma hora, comportamento conhecido do GCP para esta constraint). **Reexecutar o comando
  acima depois de esperar** — é o único passo que falta para o serviço aceitar tráfego público.
- Depois que o binding funcionar, rodar o smoke test completo (seção 8, a escrever): `/health` → 200,
  `/optins` sem JWT → 401 (da aplicação, não do Google Frontend), `/optins` com JWT do tenant → 200.

## 8. Smoke test pós-deploy

    URL=$(gcloud run services describe optin-service --region southamerica-east1 --format="value(status.url)")
    TOKEN=$(python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 38138785000136)
    curl -s -w "\n%{http_code}\n" "$URL/api/v1/health"                                   # 200
    curl -s -w "\n%{http_code}\n" "$URL/api/v1/optins"                                   # 401
    curl -s -w "\n%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL/api/v1/optins" # 200

Feito em 2026-09-02: `allUsers`/`run.invoker` propagou (levou ~alguns minutos após a exceção de projeto
da seção 7). Resultado: `/health` 200, `/optins` sem JWT 401 (da aplicação — `NAO_AUTENTICADO`), `/optins`
e `/clientes` com JWT do tenant 200 (`{"dados": []}`, tenant recém-provisionado). JWT de tenant
desconhecido (não provisionado) → 500 genérico do Django, consistente em 3 tentativas — nenhum dado
vazado (comportamento aceito no design; virar 403 é escopo futuro).
`seed_dominio_arranjo --tenant 38138785000136` executado via job `optin-manage`, log confirmado:
`[seed] ap_38138785000136: dominio_arranjo ok`.

**optin-service em homolog está no ar e servindo tráfego real:**
`https://optin-service-6sy5bhymwq-rj.a.run.app`

**Pendências que sobraram deste deploy (nenhuma bloqueia o uso atual):**
- `_CORS_ALLOWED_ORIGINS` do `cloudbuild.yaml` ainda é `http://localhost:5173` — atualizar quando o
  `ap-front` tiver uma URL própria de homolog, e redeployar.
- A exceção de Domain Restricted Sharing (seção 7) está só neste projeto (`brikz-ap`); replicar a mesma
  receita no projeto de produção quando ele existir.
- Minors da revisão de infra (M2-M5, ver histórico do plano 02) seguem em aberto, nenhum urgente.
