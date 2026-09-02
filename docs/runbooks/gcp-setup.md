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
