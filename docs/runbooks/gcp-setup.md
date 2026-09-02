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
