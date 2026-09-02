# Banco do zero — Plan 03: Primeiro tenant + primeiro deploy em homolog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenant `38138785000136` (CERC homologação) provisionado no Cloud SQL `optin-pg`, `optin-service` no ar em `brikz-ap` via `cloudbuild.yaml`, e um smoke test autenticado passando contra a URL pública.

**Architecture:** Os segredos do tenant entram no Secret Manager (lidos em runtime — spec §10.3). O **primeiro** provisionamento roda da máquina local contra o Cloud SQL (ADC + Cloud SQL Connector), porque o job `optin-manage` só existe depois do primeiro build e o build executa `migrate-tenants`, que exige o banco do tenant já existente. Tenants seguintes usam o job. Depois: `gcloud builds submit` (build → jobs → migrate → deploy), seed via job, smoke test com JWT emitido por `scripts/gerar_jwt.py`.

**Tech Stack:** gcloud, Secret Manager, Cloud Build, Cloud Run, Cloud SQL Python Connector (local, com ADC), `curl`.

**Spec:** `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md` (§2.2, §3, §10.3, §10.7). Série: plano 3 de 3.

**Depends on:** Plan 01 (comandos `provisionar_tenant`/`migrate_tenants`/`seed_dominio_arranjo`) e Plan 02 (infra `brikz-ap`, segredos estáticos, `cloudbuild.yaml`, `scripts/gerar_jwt.py`, `keys/homolog/jwt_private.pem`).

## Global Constraints

- Projeto `brikz-ap`, região `southamerica-east1`, conta `ricardo@brikz.ai`. Todo comando que cria/altera recurso ou segredo pede aprovação do usuário antes.
- Segredos nunca em arquivo do repo nem em log. Credenciais da CERC vêm de `C:\DEV\ap\.env API CERC.txt` (HANDOFF da worktree) — lidas por script, nunca `cat`/`echo`.
- Banco do tenant é `ap_38138785000136` (spec §3.2). `TENANT_IDS` em homolog = `38138785000136` **apenas** — o `12345678000199` é local.
- A suíte local **não** roda com `GOOGLE_CLOUD_PROJECT` setado (o `conftest.py` aborta). Passe a variável inline no comando de provisionamento, nunca no `.env`.

---

### Task 1: Segredos do tenant `38138785000136`

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (seção 6)

**Interfaces:**
- Consumes: segredo `ADMIN_DB_CONFIG` (Plan 02 T3) — mesma senha de `optin_app`.
- Produces: segredos `TENANT_IDS` e `TENANT_38138785000136_CONFIG` com `{"cloudsql_connection_name","cloudsql_db_user","cloudsql_db_password","cloudsql_db_name":"ap_38138785000136","cerc_client_id","cerc_client_secret","cerc_cnpj_solicitante":"38138785000136"}`.

- [ ] **Step 1: Confirmar que o arquivo de credenciais existe e tem as chaves esperadas (sem imprimir valores)**

Run:
```bash
python - <<'EOF'
from pathlib import Path
p = Path(r"C:\DEV\ap\.env API CERC.txt")
chaves = {l.split("=",1)[0].strip() for l in p.read_text(encoding="utf-8").splitlines() if "=" in l and not l.strip().startswith("#")}
print(sorted(chaves))
EOF
```
Expected: a lista inclui `CERC_CLIENT_ID` e `CERC_CLIENT_SECRET`. Se os nomes forem outros, ajuste o script do Step 3 — **pergunte ao usuário** em vez de adivinhar.

- [ ] **Step 2: `TENANT_IDS` (pedir aprovação)**

```bash
printf '38138785000136' | gcloud secrets create TENANT_IDS --data-file=- \
  --replication-policy=user-managed --locations=southamerica-east1
```
Verify: `gcloud secrets versions access latest --secret=TENANT_IDS` → `38138785000136` (este pode ser impresso; não é sensível).

- [ ] **Step 3: `TENANT_38138785000136_CONFIG` (pedir aprovação)**

**Nunca encadeie `gcloud | python | gcloud` num pipe triplo** — no Git Bash/Windows isso pode quebrar
silenciosamente no meio (o `gcloud` de saída cria o segredo mesmo com stdin vazio, resultando numa
secret com **zero versões**, sem erro visível no exit code do comando de fora). Monte o JSON num arquivo
temporário local, suba com `--data-file=<arquivo>`, apague o arquivo:
```bash
TMPCFG="$(mktemp)"
gcloud secrets versions access latest --secret=ADMIN_DB_CONFIG > "${TMPCFG}.admin"
python3 - "${TMPCFG}.admin" "$TMPCFG" <<'EOF'
import json, sys
from pathlib import Path
admin_path, out_path = sys.argv[1], sys.argv[2]
admin = json.loads(Path(admin_path).read_text(encoding="utf-8"))
cerc = {}
for l in Path(r"C:\DEV\ap\.env API CERC.txt").read_text(encoding="utf-8").splitlines():
    if "=" in l and not l.strip().startswith("#"):
        k, v = l.split("=", 1); cerc[k.strip()] = v.strip().strip('"').strip("'")
cfg = {
    "cloudsql_connection_name": admin["cloudsql_connection_name"],
    "cloudsql_db_user": admin["cloudsql_db_user"],
    "cloudsql_db_password": admin["cloudsql_db_password"],
    "cloudsql_db_name": "ap_38138785000136",
    "cerc_client_id": cerc["CERC_CLIENT_ID"],
    "cerc_client_secret": cerc["CERC_CLIENT_SECRET"],
    "cerc_cnpj_solicitante": "38138785000136",
}
assert all(cfg.values()), "campo vazio na config do tenant"
Path(out_path).write_text(json.dumps(cfg), encoding="utf-8")
EOF
gcloud secrets create TENANT_38138785000136_CONFIG --data-file="$TMPCFG" --replication-policy=user-managed --locations=southamerica-east1
shred -u "$TMPCFG" "${TMPCFG}.admin" 2>/dev/null || rm -f "$TMPCFG" "${TMPCFG}.admin"
```
Verify — **sempre**, mesmo que o comando acima não tenha reportado erro (é exatamente o tipo de falha
silenciosa que este passo existe para pegar):
```bash
gcloud secrets versions list TENANT_38138785000136_CONFIG --format="value(name,state)"
```
Expected: uma linha `1  enabled`. Vazio = a criação falhou silenciosamente; apague o segredo
(`gcloud secrets delete TENANT_38138785000136_CONFIG --quiet`) e refaça o passo.

Depois, confira o conteúdo sem expor segredos:
```bash
gcloud secrets versions access latest --secret=TENANT_38138785000136_CONFIG | python -c "import json,sys; d=json.load(sys.stdin); print(sorted(d), d['cloudsql_db_name'], d['cerc_cnpj_solicitante'])"
```
Expected: 7 chaves, `ap_38138785000136`, `38138785000136`.

- [ ] **Step 4: Runbook — seção 6**

```markdown
## 6. Onboarding de tenant

Um tenant = uma entrada em `TENANT_IDS` + um segredo `TENANT_<cnpj>_CONFIG` + um banco `ap_<cnpj>`.

1. `TENANT_IDS`: nova versão com a lista completa, separada por vírgula:
       printf 'cnpj1,cnpj2' | gcloud secrets versions add TENANT_IDS --data-file=-
   (na primeira vez: `gcloud secrets create TENANT_IDS ...`)
2. `TENANT_<cnpj>_CONFIG`: JSON com `cloudsql_connection_name`, `cloudsql_db_user`, `cloudsql_db_password`
   (mesmos de `ADMIN_DB_CONFIG`), `cloudsql_db_name = ap_<cnpj>`, `cerc_client_id`, `cerc_client_secret`,
   `cerc_cnpj_solicitante`. Monte num arquivo temporário local e suba com `--data-file=<arquivo>` (nunca
   um pipe triplo `gcloud | python | gcloud` — no Git Bash/Windows pode quebrar em silêncio e criar um
   segredo com zero versões); apague o arquivo depois. Sempre confira com `gcloud secrets versions list`.
3. Provisionar o banco:
   - Primeiro tenant do projeto (jobs ainda não existem): da máquina local, com ADC —
         gcloud auth application-default login
         GOOGLE_CLOUD_PROJECT=brikz-ap python manage.py provisionar_tenant <cnpj>
   - Demais: `gcloud run jobs execute optin-manage --region southamerica-east1 --wait --args=manage.py,provisionar_tenant,<cnpj>`
4. Seed: `gcloud run jobs execute optin-manage --region southamerica-east1 --wait --args=manage.py,seed_dominio_arranjo,--tenant,<cnpj>`
5. Token para o financiador: `python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador <cnpj>`

Sem redeploy: o service lê `TENANT_<cnpj>_CONFIG` na primeira requisição daquele tenant.
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs(runbook): onboarding de tenant (TENANT_IDS, TENANT_<cnpj>_CONFIG, provisionamento)"
```

---

### Task 2: Provisionar `ap_38138785000136` a partir da máquina local

**Files:** nenhum (operação).

**Interfaces:**
- Consumes: `python manage.py provisionar_tenant` (Plan 01), segredos da Task 1, `ADMIN_DB_CONFIG`.
- Produces: banco `ap_38138785000136` em `optin-pg` com `tenant_info`, `schema_aplicado` (0001) e todas as tabelas.

- [ ] **Step 1: ADC (interativo — pedir ao usuário)**

O Connector local precisa de Application Default Credentials da conta `ricardo@brikz.ai`:
```
! gcloud auth application-default login
```
Verify: `gcloud auth application-default print-access-token | head -c 12` imprime algo.

- [ ] **Step 2: Dry-run de acesso ao Secret Manager**

Run: `GOOGLE_CLOUD_PROJECT=brikz-ap python -c "from shared.secrets import get_secret; print(get_secret('TENANT_IDS'))"`
Expected: `38138785000136`. Se der `PermissionDenied`, a conta precisa de `roles/secretmanager.secretAccessor` no projeto (owner já tem).

- [ ] **Step 3: Provisionar (pedir aprovação — cria banco no Cloud SQL)**

Run: `GOOGLE_CLOUD_PROJECT=brikz-ap python manage.py provisionar_tenant 38138785000136`
Expected:
```
[provisionar] ap_38138785000136: 0001_baseline.sql aplicada
[provisionar] ap_38138785000136: pronto (1 migration(s))
```
Verify: `gcloud sql databases list --instance=optin-pg --format="value(name)"` inclui `ap_38138785000136`.

- [ ] **Step 4: Idempotência**

Run: `GOOGLE_CLOUD_PROJECT=brikz-ap python manage.py provisionar_tenant 38138785000136 --existente`
Expected: `pronto (0 migration(s))`.

- [ ] **Step 5: Confirmar que a suíte local continua isolada**

Run: `python -m pytest -q 2>&1 | tail -2`
Expected: verde, contra o Postgres local (nenhuma env `GOOGLE_CLOUD_PROJECT` ficou setada — `echo $GOOGLE_CLOUD_PROJECT` vazio).

---

### Task 3: Primeiro `gcloud builds submit` → service no ar

**Files:** nenhum (operação).

**Interfaces:**
- Consumes: `cloudbuild.yaml` (Plan 02 T6), HEAD do `master` com Plans 01/02 commitados.
- Produces: jobs `migrate-tenants` e `optin-manage`; service `optin-service` com URL pública `https://optin-service-<hash>-rj.a.run.app` (a Task 5 e o front usam).

- [ ] **Step 1: Árvore limpa**

Run: `git status --short`
Expected: vazio (o build usa o diretório de trabalho — não deploye alteração não commitada).

- [ ] **Step 2: Build + deploy (pedir aprovação; 5–8 min)**

Run: `gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD) 2>&1 | tail -40`
Expected: os 5 steps `SUCCESS`; no step `migrate`, log `[migrate] ap_38138785000136: nada pendente`; ao final `Service [optin-service] revision [...] has been deployed and is serving 100 percent of traffic. Service URL: https://...`.

Se `migrate` falhar com `TenantMismatchError` ou `tenant_info`: o banco não foi provisionado (Task 2) — não prossiga.
Se `deploy-service` falhar por IAM (`setIamPolicy`): `optin-build@` precisa de `roles/run.admin` (Plan 02 T4) — corrija lá, não aqui.

- [ ] **Step 3: Guardar a URL e checar `/health`**

```bash
URL=$(gcloud run services describe optin-service --region southamerica-east1 --format="value(status.url)")
echo "$URL"
curl -s -o /dev/null -w "%{http_code}\n" "$URL/api/v1/health"
```
Expected: `200`.

- [ ] **Step 4: Sem JWT = 401**

Run: `curl -s -w "\n%{http_code}\n" "$URL/api/v1/optins"`
Expected: `{"erro": "NAO_AUTENTICADO", ...}` e `401`.

---

### Task 4: Seed `dominio_arranjo` via job

**Files:** nenhum (operação).

**Interfaces:**
- Consumes: job `optin-manage` (Task 3).
- Produces: linha `99T` em `dominio_arranjo` do tenant.

- [ ] **Step 1: Executar (pedir aprovação)**

Run: `gcloud run jobs execute optin-manage --region southamerica-east1 --wait --args=manage.py,seed_dominio_arranjo,--tenant,38138785000136`
Expected: execução `Succeeded`; em `gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="optin-manage"' --limit 5 --format="value(textPayload)"` aparece `[seed] ap_38138785000136: dominio_arranjo ok`.

---

### Task 5: Smoke test autenticado + entrega para o front

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (seção 7)
- Modify: `docs/dev-setup.md` (seção "Homolog")

**Interfaces:**
- Consumes: `scripts/gerar_jwt.py`, `keys/homolog/jwt_private.pem` (Plan 02 T5), URL (Task 3).
- Produces: JWT de 24h para o tenant e instruções para o `ap-front` local (`VITE_OPTIN_API_BASE_URL`, `VITE_OPTIN_DEV_JWT`, `VITE_FINANCIADOR_ID`).

- [ ] **Step 1: Token**

Run: `TOKEN=$(python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 38138785000136 --horas 24)`

- [ ] **Step 2: Leituras autenticadas**

```bash
curl -s -w "\n%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL/api/v1/optins"
curl -s -w "\n%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL/api/v1/clientes"
```
Expected: `[]` e `200` nos dois (tenant recém-criado, sem dados).

- [ ] **Step 3: Token de outro tenant não provisionado = 500 controlado, não vazamento**

```bash
T2=$(python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 11111111000191 --horas 1)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $T2" "$URL/api/v1/optins"
```
Expected: `500` (segredo `TENANT_11111111000191_CONFIG` não existe → `RuntimeError` em `get_tenant_config`). Confirma que um JWT válido de tenant desconhecido não devolve dados de ninguém. (Melhorar para `403` é escopo futuro, não deste plano.)

- [ ] **Step 4: Runbook — seção 7 e dev-setup**

Acrescente ao `docs/runbooks/gcp-setup.md`:
```markdown
## 7. Smoke test pós-deploy

    URL=$(gcloud run services describe optin-service --region southamerica-east1 --format="value(status.url)")
    TOKEN=$(python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 38138785000136)
    curl -s -w "\n%{http_code}\n" "$URL/api/v1/health"                                   # 200
    curl -s -w "\n%{http_code}\n" "$URL/api/v1/optins"                                   # 401
    curl -s -w "\n%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL/api/v1/optins" # 200
```
Acrescente ao `docs/dev-setup.md`:
```markdown
## Homolog (brikz-ap)

Back: URL em `gcloud run services describe optin-service --region southamerica-east1 --format="value(status.url)"`.
Token de 24h: `python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 38138785000136`.
Front local contra homolog (`ap-front/.env`): `VITE_OPTIN_API_BASE_URL=<URL>/api/v1`, `VITE_OPTIN_DEV_JWT=<token>`,
`VITE_FINANCIADOR_ID=38138785000136`. Quando o front tiver URL própria, atualizar `_CORS_ALLOWED_ORIGINS`
no `cloudbuild.yaml` e redeployar. Infra: `docs/runbooks/gcp-setup.md`.
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gcp-setup.md docs/dev-setup.md
git commit -m "docs: smoke test de homolog e instruções para o front"
```

- [ ] **Step 6: Reportar ao usuário**

URL do service, confirmação dos três códigos HTTP (200/401/200), e o lembrete: fazer backup de `keys/homolog/jwt_private.pem` fora do repo. Próximo plano sugerido: deploy do `ap-front` (repo separado) + `_CORS_ALLOWED_ORIGINS`.

---

## Self-Review Notes

- **Spec coverage:** §10.3 segredos por tenant lidos em runtime (T1), §3 provisionamento (T2), §10.6/§10.7 primeiro deploy na ordem correta (T3), §6.3 seed (T4), smoke (`/health` + `GET` com JWT) (T5). O ponto cego do ciclo "migrate antes do deploy exige banco existente" é resolvido pelo provisionamento local no primeiro tenant e documentado na seção 6 do runbook.
- **Placeholder scan:** `<hash>`/`<URL>`/`<token>` são valores só conhecidos em execução, capturados em variáveis nos steps.
- **Type consistency:** nomes de segredos e de jobs iguais aos do Plan 02 e da spec; `provisionar_tenant <cnpj> [--existente]` e `seed_dominio_arranjo --tenant <cnpj>` iguais ao Plan 01 T8.
- **Risco:** o Connector local usa IP público da instância + ADC; se a rede bloquear a porta 3307/5432 de saída, rodar a Task 2 de outra rede ou via Cloud Shell (`gcloud cloud-shell ssh` com o repo clonado).
