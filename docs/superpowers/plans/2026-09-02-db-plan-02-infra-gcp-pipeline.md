# Banco do zero — Plan 02: Infra GCP (`brikz-ap`) + pipeline de deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deixar o projeto `brikz-ap` com toda a infra base (APIs, Artifact Registry, Cloud SQL Postgres 17, service accounts, IAM, segredos estáticos) e o repo com `cloudbuild.yaml` + runbook versionado, de modo que o Plan 03 consiga fazer o primeiro deploy só criando os segredos do primeiro tenant e rodando `gcloud builds submit`.

**Architecture:** Molde dos irmãos (`etl-back-elegibility/cloudbuild.yaml`): infra criada uma vez com `gcloud`, deploy por Cloud Build. Diferenças deliberadas (spec §10): comandos num runbook versionado (`docs/runbooks/gcp-setup.md`) em vez de comentários no yaml; Artifact Registry com tag por commit em vez de `gcr.io:latest`; o build roda como SA própria (`optin-build@`) e executa o Cloud Run Job `migrate-tenants` **antes** de `gcloud run deploy`. Cada passo que cria recurso é executado via CLI **com aprovação explícita do usuário antes** e verificado com `gcloud ... describe`.

**Tech Stack:** gcloud SDK 581 (autenticado como `ricardo@brikz.ai`), Cloud SQL (Postgres 17, `db-g1-small`), Cloud Run (service + jobs), Secret Manager, Artifact Registry, Cloud Build, Docker (build remoto — não há Docker local), Python `cryptography` (geração de chaves JWT).

**Spec:** `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md` (§10; §2.3/§2.4 para role e `ADMIN_DB_CONFIG`). Série: plano 2 de 3.

**Depends on:** Plan 01 (`apps.tenants` e os comandos `migrate_tenants`/`provisionar_tenant`/`seed_dominio_arranjo` precisam existir para os jobs fazerem sentido; o `cloudbuild.yaml` referencia esses comandos).

## Global Constraints

- Projeto `brikz-ap`, região `southamerica-east1`, conta `ricardo@brikz.ai` (spec §10.1). Antes de qualquer comando: `gcloud config get-value project` deve devolver `brikz-ap`.
- **Nenhum comando que cria/altera recurso no GCP roda sem o usuário aprovar aquele comando.** Comandos `describe`/`list` são livres.
- Segredos nunca aparecem em arquivo do repo nem em log: valores gerados vão para variável de shell e entram no Secret Manager via `--data-file=-`. Nunca `echo` de segredo.
- Nomes fixos (spec §10): instância `optin-pg`, role `optin_app`, repositório AR `optin`, imagem `southamerica-east1-docker.pkg.dev/brikz-ap/optin/optin-service`, SAs `optin-run@brikz-ap.iam.gserviceaccount.com` e `optin-build@brikz-ap.iam.gserviceaccount.com`, service `optin-service`, jobs `migrate-tenants` e `optin-manage`.
- Cloud SQL: Postgres 17, `db-g1-small`, IP público **sem** redes autorizadas, backups + PITR, `deletion-protection` (spec §10.2).
- Papéis mínimos (spec §10.4): `optin-run@` = `roles/cloudsql.client` + `roles/secretmanager.secretAccessor`; `optin-build@` = `roles/run.admin` + `roles/artifactregistry.writer` + `roles/logging.logWriter` + `roles/cloudbuild.builds.builder` + `roles/iam.serviceAccountUser` sobre `optin-run@`.
- Sem Terraform, sem trigger de build por push (spec §8).

---

### Task 1: Código pronto para container — `DEBUG` só em development, `.dockerignore`, gunicorn dimensionado

**Files:**
- Modify: `config/settings.py:9-11, 26-31`
- Create: `.dockerignore`
- Modify: `Dockerfile:7`
- Create: `config/tests/test_settings_ambiente.py` (`config/tests/__init__.py` já existe após o merge do Plan 01)

**Interfaces:**
- Produces: `config.settings.DEBUG` verdadeiro **só** com `ENVIRONMENT=development`; `CORS_ALLOWED_ORIGINS` (lista explícita) para qualquer outro ambiente; imagem sem `.env`/`.git`/`.claude`; gunicorn com `WEB_CONCURRENCY` (default 2). O `cloudbuild.yaml` da Task 6 passa `ENVIRONMENT=homolog`.

- [ ] **Step 1: Teste que falha**

`config/tests/test_settings_ambiente.py`:
```python
import importlib

import config.settings as settings_module


def _recarrega(monkeypatch, ambiente: str, cors: str = ""):
    monkeypatch.setenv("ENVIRONMENT", ambiente)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", cors)
    return importlib.reload(settings_module)


def test_development_e_debug_com_cors_de_localhost(monkeypatch):
    s = _recarrega(monkeypatch, "development")
    assert s.DEBUG is True
    assert s.CORS_ALLOWED_ORIGIN_REGEXES  # localhost liberado
    assert not getattr(s, "CORS_ALLOWED_ORIGINS", None)


def test_homolog_nao_e_debug_e_usa_lista_explicita(monkeypatch):
    s = _recarrega(monkeypatch, "homolog", "https://a.example,https://b.example")
    assert s.DEBUG is False
    assert s.CORS_ALLOWED_ORIGINS == ["https://a.example", "https://b.example"]
    assert not getattr(s, "CORS_ALLOWED_ORIGIN_REGEXES", None)


def test_production_nao_e_debug(monkeypatch):
    assert _recarrega(monkeypatch, "production").DEBUG is False


def teardown_module(module):
    importlib.reload(settings_module)  # devolve o módulo ao estado do .env para os outros testes
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest config/tests/test_settings_ambiente.py -v`
Expected: `test_homolog_nao_e_debug_e_usa_lista_explicita` FAIL (`assert True is False`).

- [ ] **Step 3: Implementar**

Em `config/settings.py`, substitua a linha `DEBUG = ...` por:
```python
# DEBUG só em desenvolvimento local. homolog/production rodam com DEBUG=False
# (sem stack trace na resposta, CORS por lista explícita) — spec 2026-09-02 §10.5.
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
DEBUG = ENVIRONMENT == "development"
```
E o bloco de CORS por:
```python
# CORS — dev: qualquer localhost (vite muda de porta). Demais ambientes: só as
# origens listadas em CORS_ALLOWED_ORIGINS (separadas por vírgula).
if DEBUG:
    CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://localhost:\d+$", r"^http://127\.0\.0\.1:\d+$"]
    CORS_ALLOWED_ORIGINS = []
else:
    CORS_ALLOWED_ORIGIN_REGEXES = []
    CORS_ALLOWED_ORIGINS = [o for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o]
```

`.dockerignore` (raiz do repo `optin/`):
```
.env
.env.*
!.env.example
.git
.gitignore
.claude
.superpowers
.venv
venv
__pycache__
*.pyc
.pytest_cache
docs
docker
docker-compose.yml
*.pem
logs
```

`Dockerfile`, última linha:
```dockerfile
CMD exec gunicorn config.wsgi:application --bind :$PORT --workers ${WEB_CONCURRENCY:-2} --timeout 60
```

- [ ] **Step 4: Rodar e ver passar (e a suíte inteira, porque `settings` é global)**

Run: `python -m pytest config/tests/test_settings_ambiente.py -v && python -m pytest -q 2>&1 | tail -2`
Expected: 3 PASS; suíte inteira verde.

- [ ] **Step 5: Commit**

```bash
git add config/settings.py .dockerignore Dockerfile config/tests/test_settings_ambiente.py
git commit -m "chore: DEBUG só em development, .dockerignore, gunicorn por WEB_CONCURRENCY"
```

---

### Task 2: APIs + Artifact Registry

**Files:**
- Create: `docs/runbooks/gcp-setup.md` (seção 1 — o arquivo cresce a cada task; cada task acrescenta sua seção e commita)

**Interfaces:**
- Produces: APIs habilitadas; repositório Docker `optin` em `southamerica-east1`. A Task 6 usa o caminho `southamerica-east1-docker.pkg.dev/brikz-ap/optin/optin-service`.

- [ ] **Step 1: Confirmar projeto ativo**

Run: `gcloud config get-value project && gcloud config get-value account`
Expected: `brikz-ap` e `ricardo@brikz.ai`. Se não, `gcloud config set project brikz-ap` / `gcloud config set account ricardo@brikz.ai` e repita.

- [ ] **Step 2: Habilitar APIs (pedir aprovação; leva 1–3 min)**

```bash
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  cloudresourcemanager.googleapis.com compute.googleapis.com
```
Verify: `gcloud services list --enabled --format="value(config.name)" | grep -cE "run|sqladmin|secretmanager|cloudbuild|artifactregistry|iam\.googleapis|cloudresourcemanager|compute"`
Expected: `8`.

- [ ] **Step 3: Artifact Registry (pedir aprovação)**

```bash
gcloud artifacts repositories create optin --repository-format=docker --location=southamerica-east1 \
  --description="Imagens do optin-service"
```
Verify: `gcloud artifacts repositories describe optin --location=southamerica-east1 --format="value(name,format)"`
Expected: `projects/brikz-ap/locations/southamerica-east1/repositories/optin  DOCKER`.

- [ ] **Step 4: Iniciar o runbook**

`docs/runbooks/gcp-setup.md`:
```markdown
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
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs(runbook): APIs e Artifact Registry do brikz-ap"
```

---

### Task 3: Cloud SQL `optin-pg` + role `optin_app` + segredo `ADMIN_DB_CONFIG`

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (seção 2)

**Interfaces:**
- Produces: instância `optin-pg` (connection name `brikz-ap:southamerica-east1:optin-pg`); usuário `optin_app` (membro de `cloudsqlsuperuser`, que inclui `CREATEDB`); segredo `ADMIN_DB_CONFIG` = `{"cloudsql_connection_name":"brikz-ap:southamerica-east1:optin-pg","cloudsql_db_user":"optin_app","cloudsql_db_password":"<gerada>","cloudsql_db_name":"postgres"}`. O Plan 03 monta `TENANT_<cnpj>_CONFIG` com as mesmas chaves e `cloudsql_db_name=ap_<cnpj>`.

- [ ] **Step 1: Criar a instância (pedir aprovação; 5–10 min; **custa ~US$ 35–45/mês a partir daqui**)**

```bash
gcloud sql instances create optin-pg \
  --database-version=POSTGRES_17 --edition=ENTERPRISE --tier=db-g1-small \
  --region=southamerica-east1 --availability-type=ZONAL \
  --storage-type=SSD --storage-size=10GB --storage-auto-increase \
  --backup-start-time=03:00 --enable-point-in-time-recovery --retained-backups-count=7 \
  --assign-ip --deletion-protection --ssl-mode=ENCRYPTED_ONLY
```
Verify: `gcloud sql instances describe optin-pg --format="value(state,databaseVersion,settings.tier,connectionName,settings.ipConfiguration.authorizedNetworks)"`
Expected: `RUNNABLE  POSTGRES_17  db-g1-small  brikz-ap:southamerica-east1:optin-pg` e redes autorizadas **vazias**.

- [ ] **Step 2: Gerar senha e criar o usuário `optin_app` (pedir aprovação)**

A senha vive só na variável de shell desta sessão e no Secret Manager:
```bash
PW_OPTIN_APP="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
gcloud sql users create optin_app --instance=optin-pg --password="$PW_OPTIN_APP"
```
Verify: `gcloud sql users list --instance=optin-pg --format="value(name)"`
Expected: inclui `optin_app` (e `postgres`). Usuários criados pela API são membros de `cloudsqlsuperuser` → têm `CREATEDB`, que é o que `provisionar_tenant` precisa (spec §2.4).

- [ ] **Step 3: Segredo `ADMIN_DB_CONFIG` (pedir aprovação)**

```bash
printf '{"cloudsql_connection_name":"brikz-ap:southamerica-east1:optin-pg","cloudsql_db_user":"optin_app","cloudsql_db_password":"%s","cloudsql_db_name":"postgres"}' "$PW_OPTIN_APP" \
  | gcloud secrets create ADMIN_DB_CONFIG --data-file=- --replication-policy=user-managed --locations=southamerica-east1
unset PW_OPTIN_APP
```
Verify: `gcloud secrets versions list ADMIN_DB_CONFIG --format="value(name,state)"`
Expected: `1  enabled`. **Não** faça `versions access` no terminal (imprimiria a senha).

- [ ] **Step 4: Runbook — seção 2**

Acrescente ao `docs/runbooks/gcp-setup.md`:
```markdown
## 2. Cloud SQL

    gcloud sql instances create optin-pg \
      --database-version=POSTGRES_17 --edition=ENTERPRISE --tier=db-g1-small \
      --region=southamerica-east1 --availability-type=ZONAL \
      --storage-type=SSD --storage-size=10GB --storage-auto-increase \
      --backup-start-time=03:00 --enable-point-in-time-recovery --retained-backups-count=7 \
      --assign-ip --deletion-protection --ssl-mode=ENCRYPTED_ONLY

Sem `--authorized-networks`: acesso só via Cloud SQL Connector (IAM + TLS).

    PW_OPTIN_APP="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    gcloud sql users create optin_app --instance=optin-pg --password="$PW_OPTIN_APP"
    printf '{"cloudsql_connection_name":"brikz-ap:southamerica-east1:optin-pg","cloudsql_db_user":"optin_app","cloudsql_db_password":"%s","cloudsql_db_name":"postgres"}' "$PW_OPTIN_APP" \
      | gcloud secrets create ADMIN_DB_CONFIG --data-file=- --replication-policy=user-managed --locations=southamerica-east1
    unset PW_OPTIN_APP

Os bancos `ap_<cnpj>` NÃO são criados aqui — `provisionar_tenant` cria (Plan 03 / seção 6).
Rotação da senha: `gcloud sql users set-password optin_app --instance=optin-pg --password=...` e nova versão
de `ADMIN_DB_CONFIG` **e** de cada `TENANT_<cnpj>_CONFIG`; reiniciar o service (cache por processo).
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs(runbook): Cloud SQL optin-pg, usuário optin_app, ADMIN_DB_CONFIG"
```

---

### Task 4: Service accounts e IAM

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (seção 3)

**Interfaces:**
- Produces: `optin-run@brikz-ap.iam.gserviceaccount.com` (runtime) e `optin-build@brikz-ap.iam.gserviceaccount.com` (Cloud Build) com os papéis da spec §10.4. A Task 6 referencia as duas no `cloudbuild.yaml`.

- [ ] **Step 1: Criar as SAs (pedir aprovação)**

```bash
gcloud iam service-accounts create optin-run --display-name="optin-service runtime (Cloud Run service + jobs)"
gcloud iam service-accounts create optin-build --display-name="optin-service Cloud Build"
```
Verify: `gcloud iam service-accounts list --format="value(email)"`
Expected: as duas `@brikz-ap.iam.gserviceaccount.com`.

- [ ] **Step 2: Papéis da runtime (pedir aprovação)**

```bash
for r in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding brikz-ap \
    --member=serviceAccount:optin-run@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
done
```

- [ ] **Step 3: Papéis do build (pedir aprovação)**

```bash
for r in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter roles/cloudbuild.builds.builder; do
  gcloud projects add-iam-policy-binding brikz-ap \
    --member=serviceAccount:optin-build@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
done
gcloud iam service-accounts add-iam-policy-binding optin-run@brikz-ap.iam.gserviceaccount.com \
  --member=serviceAccount:optin-build@brikz-ap.iam.gserviceaccount.com --role=roles/iam.serviceAccountUser
```
Verify:
```bash
gcloud projects get-iam-policy brikz-ap --flatten="bindings[].members" \
  --filter="bindings.members:optin-" --format="table(bindings.members,bindings.role)"
```
Expected: 2 linhas para `optin-run`, 4 para `optin-build`. E `gcloud iam service-accounts get-iam-policy optin-run@brikz-ap.iam.gserviceaccount.com` mostra `optin-build` como `serviceAccountUser`.

- [ ] **Step 4: Runbook — seção 3**

Acrescente:
```markdown
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
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs(runbook): service accounts optin-run/optin-build e IAM mínimo"
```

---

### Task 5: Chaves JWT + segredos estáticos (`DJANGO_SECRET_KEY`, `IAM_JWT_PUBLIC_KEY`) + scripts

**Files:**
- Create: `scripts/__init__.py` (vazio)
- Create: `scripts/gerar_chaves_jwt.py`
- Create: `scripts/gerar_jwt.py`
- Create: `scripts/tests/__init__.py` (vazio)
- Create: `scripts/tests/test_gerar_jwt.py`
- Modify: `.gitignore`
- Modify: `docs/runbooks/gcp-setup.md` (seção 4)

**Interfaces:**
- Produces: par RSA em `keys/homolog/jwt_private.pem` / `jwt_public.pem` (**gitignorado**; a privada fica só nesta máquina — é o "IdP" de homolog até existir um de verdade); segredos `IAM_JWT_PUBLIC_KEY` e `DJANGO_SECRET_KEY`; `python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador <cnpj> [--horas 24]` imprime um JWT RS256 com `iss=brikz-iam` e claim `financiador_id`. O Plan 03 usa esse script no smoke test e para o `VITE_OPTIN_DEV_JWT` do front.

- [ ] **Step 1: Teste que falha — `scripts/tests/test_gerar_jwt.py`**

```python
import jwt as pyjwt

from scripts.gerar_chaves_jwt import gerar_par
from scripts.gerar_jwt import gerar_token


def test_token_gerado_valida_com_a_publica_e_carrega_financiador(tmp_path):
    priv, pub = gerar_par(tmp_path)
    token = gerar_token(priv, financiador_id="12345678000199", horas=1)
    claims = pyjwt.decode(token, pub.read_text(), algorithms=["RS256"], issuer="brikz-iam")
    assert claims["financiador_id"] == "12345678000199"
    assert claims["sub"] == "dev-user"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest scripts/tests/test_gerar_jwt.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.gerar_chaves_jwt'`.

- [ ] **Step 3: Implementar os dois scripts**

`scripts/gerar_chaves_jwt.py`:
```python
"""Gera o par RSA usado para assinar JWTs de acesso à API (IdP de homolog/dev).

    python scripts/gerar_chaves_jwt.py keys/homolog

Escreve jwt_private.pem (fica SÓ na máquina de quem emite tokens) e jwt_public.pem
(vai para o segredo IAM_JWT_PUBLIC_KEY). Ambos gitignorados (*.pem).
"""
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def gerar_par(diretorio: Path):
    diretorio = Path(diretorio)
    diretorio.mkdir(parents=True, exist_ok=True)
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = diretorio / "jwt_private.pem"
    pub = diretorio / "jwt_public.pem"
    priv.write_bytes(chave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    pub.write_bytes(chave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return priv, pub


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: python scripts/gerar_chaves_jwt.py <diretorio>")
    priv, pub = gerar_par(Path(sys.argv[1]))
    print(f"privada: {priv}\npublica: {pub}")
```

`scripts/gerar_jwt.py`:
```python
"""Emite um JWT RS256 aceito por shared/jwt_auth.py (iss=brikz-iam, claim financiador_id).

    python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 12345678000199 --horas 24
"""
import argparse
import time
from pathlib import Path

import jwt as pyjwt


def gerar_token(chave_privada: Path, financiador_id: str, horas: int = 24, sub: str = "dev-user") -> str:
    agora = int(time.time())
    return pyjwt.encode(
        {"iss": "brikz-iam", "sub": sub, "iat": agora, "exp": agora + horas * 3600, "financiador_id": financiador_id},
        Path(chave_privada).read_text(),
        algorithm="RS256",
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--chave", required=True)
    p.add_argument("--financiador", required=True)
    p.add_argument("--horas", type=int, default=24)
    p.add_argument("--sub", default="dev-user")
    a = p.parse_args()
    print(gerar_token(Path(a.chave), a.financiador, a.horas, a.sub))
```

`.gitignore` — adicione:
```
keys/
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest scripts/tests/test_gerar_jwt.py -v`
Expected: PASS.

- [ ] **Step 5: Gerar o par de homolog e criar os segredos (pedir aprovação para os `secrets create`)**

```bash
python scripts/gerar_chaves_jwt.py keys/homolog
gcloud secrets create IAM_JWT_PUBLIC_KEY --data-file=keys/homolog/jwt_public.pem \
  --replication-policy=user-managed --locations=southamerica-east1
python -c 'import secrets; print(secrets.token_urlsafe(50))' \
  | gcloud secrets create DJANGO_SECRET_KEY --data-file=- --replication-policy=user-managed --locations=southamerica-east1
```
Verify: `gcloud secrets list --format="value(name)"` → `ADMIN_DB_CONFIG`, `DJANGO_SECRET_KEY`, `IAM_JWT_PUBLIC_KEY`. E `git status` **não** mostra nada em `keys/`.

Avise o usuário explicitamente: `keys/homolog/jwt_private.pem` é quem emite tokens de homolog — fazer backup fora do repo (gerenciador de senhas). Perder = gerar par novo e trocar o segredo.

- [ ] **Step 6: Runbook — seção 4**

```markdown
## 4. Segredos estáticos do serviço

    python scripts/gerar_chaves_jwt.py keys/homolog        # privada fica local (backup fora do repo!)
    gcloud secrets create IAM_JWT_PUBLIC_KEY --data-file=keys/homolog/jwt_public.pem \
      --replication-policy=user-managed --locations=southamerica-east1
    python -c 'import secrets; print(secrets.token_urlsafe(50))' \
      | gcloud secrets create DJANGO_SECRET_KEY --data-file=- --replication-policy=user-managed --locations=southamerica-east1

Emitir token: `python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador <cnpj> --horas 24`.
Segredos por tenant (`TENANT_IDS`, `TENANT_<cnpj>_CONFIG`) ficam na seção 6 (Plan 03) — são lidos em runtime,
não montados no deploy, então onboardar tenant não exige redeploy.
```

- [ ] **Step 7: Commit**

```bash
git add scripts .gitignore docs/runbooks/gcp-setup.md
git commit -m "feat: scripts de chaves/JWT de homolog; segredos IAM_JWT_PUBLIC_KEY e DJANGO_SECRET_KEY"
```

---

### Task 6: `cloudbuild.yaml` (build → jobs → migrate → deploy)

**Files:**
- Create: `cloudbuild.yaml`
- Modify: `docs/runbooks/gcp-setup.md` (seção 5)

**Interfaces:**
- Consumes: AR `optin` (T2), SAs (T4), segredos `DJANGO_SECRET_KEY`/`IAM_JWT_PUBLIC_KEY` (T5), comandos `migrate_tenants` e `check` (Plan 01).
- Produces: `gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)` que cria/atualiza os jobs `migrate-tenants` e `optin-manage`, executa `migrate-tenants` e, só se passar, faz deploy do service `optin-service`. O Plan 03 executa isso pela primeira vez.

- [ ] **Step 1: Escrever `cloudbuild.yaml`**

```yaml
# Deploy do optin-service em Cloud Run (spec 2026-09-02 §10.6).
# Ordem obrigatória: build → push → jobs apontando para a imagem nova →
# migrate-tenants (falhou = para aqui, revisão antiga segue no ar) → deploy do service.
#
# Uso (manual, sem trigger):
#   gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)
#
# Pré-requisitos: docs/runbooks/gcp-setup.md seções 1–4 e os segredos do(s) tenant(s) (seção 6).
steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args: ['build', '-t', '${_IMAGE}:${_TAG}', '.']

  - id: push
    name: gcr.io/cloud-builders/docker
    args: ['push', '${_IMAGE}:${_TAG}']

  # Jobs administrativos com a imagem nova (create-or-update). Sem retry: migration
  # não é idempotente por acidente. Env mínima — tudo por tenant vem do Secret Manager.
  - id: deploy-jobs
    name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: bash
    args:
      - -c
      - |
        set -euo pipefail
        # $$ escapa o $ da substituição do Cloud Build (que roda ANTES do shell):
        # sem isso, ${job%%:*}/${job##*:} são lidos como chaves de substituição
        # inexistentes e o build é rejeitado no submit.
        for job in migrate-tenants:migrate_tenants optin-manage:check; do
          nome="$${job%%:*}"; comando="$${job##*:}"
          gcloud run jobs deploy "$$nome" \
            --image "${_IMAGE}:${_TAG}" --region "${_REGION}" \
            --service-account "${_RUNTIME_SA}" \
            --command python --args "manage.py,$$comando" \
            --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ENVIRONMENT=${_ENVIRONMENT},ALLOWED_HOSTS=*" \
            --max-retries 0 --task-timeout 600 --tasks 1 --quiet
        done

  - id: migrate
    name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args: ['run', 'jobs', 'execute', 'migrate-tenants', '--region', '${_REGION}', '--wait']

  - id: deploy-service
    name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args:
      - run
      - deploy
      - ${_SERVICE}
      - --image=${_IMAGE}:${_TAG}
      - --region=${_REGION}
      - --platform=managed
      # Público: o front chama do navegador; a autenticação é o JWT (shared/jwt_auth.py).
      - --allow-unauthenticated
      - --port=8080
      - --cpu=1
      - --memory=512Mi
      - --concurrency=20
      - --min-instances=0
      - --max-instances=3
      - --timeout=60
      - --service-account=${_RUNTIME_SA}
      # ^@^ troca o separador para '@' porque CORS_ALLOWED_ORIGINS tem vírgulas.
      - --set-env-vars=^@^ENVIRONMENT=${_ENVIRONMENT}@GOOGLE_CLOUD_PROJECT=$PROJECT_ID@ALLOWED_HOSTS=*@CORS_ALLOWED_ORIGINS=${_CORS_ALLOWED_ORIGINS}@IAM_JWT_ISSUER=brikz-iam@CERC_AUTH_URL=${_CERC_AUTH_URL}@CERC_API_BASE_URL=${_CERC_API_BASE_URL}@WEB_CONCURRENCY=2
      - --set-secrets=DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest,IAM_JWT_PUBLIC_KEY=IAM_JWT_PUBLIC_KEY:latest

substitutions:
  _TAG: manual                     # passe _TAG=$(git rev-parse --short HEAD) no submit
  _REGION: southamerica-east1
  _SERVICE: optin-service
  _IMAGE: southamerica-east1-docker.pkg.dev/brikz-ap/optin/optin-service
  _RUNTIME_SA: optin-run@brikz-ap.iam.gserviceaccount.com
  _ENVIRONMENT: homolog
  # Origens do front autorizadas (vírgula). Atualize quando o ap-front tiver URL de homolog.
  _CORS_ALLOWED_ORIGINS: http://localhost:5173
  _CERC_AUTH_URL: https://api.int.cerc.com/oauth/token
  _CERC_API_BASE_URL: https://ap-homolog.cerc.inf.br

serviceAccount: projects/brikz-ap/serviceAccounts/optin-build@brikz-ap.iam.gserviceaccount.com
# Sem isto o build usa o default de 600s (10min), apertado para build+push+2 jobs+
# migrate-tenants+deploy — um migrate lento sozinho já consome o orçamento inteiro.
timeout: 1800s
options:
  logging: CLOUD_LOGGING_ONLY
  machineType: E2_HIGHCPU_8
```

Além disso: crie/atualize `.dockerignore` e `.gcloudignore` para excluir explicitamente `keys/` e
`**/*.pem` (um `*.pem` sozinho não casa em subdiretório — `keys/homolog/jwt_private.pem` escaparia).

- [ ] **Step 2: Validar sintaxe e substituições sem executar**

Run: `python -c "import yaml,sys; d=yaml.safe_load(open('cloudbuild.yaml')); print([s['id'] for s in d['steps']], sorted(d['substitutions']))"`
Expected: `['build', 'push', 'deploy-jobs', 'migrate', 'deploy-service']` e as 9 substituições. (`pyyaml` vem com o SDK do Python usado pelo Django? Se faltar: `pip install pyyaml` — não entra no `requirements.txt`.)

**Não** rode `gcloud builds submit` neste plano: o passo `migrate` falharia com `TENANT_IDS` inexistente — isso é o Plan 03, depois de criar os segredos do primeiro tenant.

- [ ] **Step 3: Runbook — seção 5**

```markdown
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
```

- [ ] **Step 4: Commit**

```bash
git add cloudbuild.yaml docs/runbooks/gcp-setup.md
git commit -m "feat: cloudbuild.yaml — build, jobs, migrate-tenants antes do deploy do service"
```

---

## Self-Review Notes

- **Spec coverage:** §10.1 (T2 projeto/APIs), §10.2 (T3), §10.3 estáticos (T5; por-tenant fica no Plan 03), §10.4 (T4), §10.5 service/jobs + pré-requisitos de código (T1, T6), §10.6 (T6), §10.7 primeiro deploy → Plan 03, §10.8 custo — alertado no T3 Step 1.
- **Placeholder scan:** `<cnpj>` no runbook é parâmetro do operador; `_CORS_ALLOWED_ORIGINS` tem valor real (localhost) com instrução de atualização — não é lacuna.
- **Type consistency:** nomes de recursos idênticos em T2–T6 e na spec (`optin-pg`, `optin_app`, `optin-run@`, `optin-build@`, `optin-service`, `migrate-tenants`, `optin-manage`, imagem `.../optin/optin-service`). `gerar_par(diretorio) -> (priv, pub)` e `gerar_token(chave_privada, financiador_id, horas, sub)` usados no teste com a mesma assinatura.
- **Risco:** `gcloud run jobs deploy` com `--args` separados por vírgula — `manage.py,migrate_tenants` está correto; se um dia um argumento contiver vírgula, usar `--args=^|^a|b`.
