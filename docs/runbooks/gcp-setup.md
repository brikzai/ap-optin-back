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
