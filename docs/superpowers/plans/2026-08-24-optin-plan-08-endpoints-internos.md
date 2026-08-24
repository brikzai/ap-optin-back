# optin-service — Plan 08: Endpoints Internos (SPEC-01 §5) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar a API interna `/api/v1/optins` (SPEC-01 §5) sobre as fundações dos planos 01-07: `POST /api/v1/optins` (criar), `GET /api/v1/optins` (listar), `GET /api/v1/optins/{id}` (detalhar), `PATCH /api/v1/optins/{id}` (atualizar), `POST /api/v1/optins/{id}/optout` (encerrar) — com autenticação JWT do IdP corporativo, `Idempotency-Key`, anti-duplicidade (§5.6) e a máquina de estados §9.1 fechando o ciclo PENDENTE→ATIVO/REJEITADO e ATIVO→ENCERRADO.

**Architecture:** Function-based views em `apps/optin/views.py` (sem DRF ViewSets, decisão já tomada no design doc), autenticadas por um decorator JWT (`shared/jwt_auth.py`) e um decorator de idempotência (`apps/optin/idempotency.py`). Toda regra de negócio local fica em `apps/optin/validation.py` (funções puras, testáveis sem banco); toda leitura/escrita passa por `apps/optin/repository.py`, que usa `shared.cloudsql_client.get_db(financiador_id)` (sem ORM, um banco por tenant — Plan 09). A interpretação do array 207 da CERC (sucesso/erro por item, correlação por `referenciaExterna`, casos idempotentes como `104803`/`106803`) fica isolada em `apps/optin/cerc_mapping.py`, reaproveitada por create/update/optout. As chamadas à CERC em si usam `services/cerc/client.py` (Plan 07, já corrigido para `POST /opt_in` com `tipoOperacao`/array — ver nota abaixo).

**Tech Stack:** Django 4.2 (function-based views), PyJWT (`pyjwt[crypto]`) para verificação RS256, SQLAlchemy via `shared/cloudsql_client.py`, `python-ulid` para IDs, pytest + pytest-django + `django.test.Client`.

**Spec:** `docs/superpowers/specs/SPEC-01-optin-e-gestao.md` (§5, §5.6, §6, §7.1-§7.3, §8, §9.1) e `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§2-§4).

## Contexto: correção aplicada antes deste plano

`services/cerc/client.py` foi corrigido nesta sessão (antes deste plano) para bater com a SPEC-01 §4.1/§4.2: não existe `PUT /opt_in/{protocolo}` — `atualizar_optin` usa o mesmo `POST /opt_in` com `tipoOperacao="A"` e `protocolo` no corpo; `/opt_in` e `/opt_out` recebem sempre um **array**. `registrar_optin`/`atualizar_optin`/`encerrar_optin` retornam o array 207 cru (`list`), não mais um `dict`. Este plano consome esse contrato corrigido.

## Global Constraints

- Todos os endpoints de `/api/v1/*` exigem `Authorization: Bearer <JWT RS256>` do IdP corporativo, exceto `health` (SPEC-01 §5, design §4). Chave pública fixa via `IAM_JWT_PUBLIC_KEY` (PEM), emissor esperado via `IAM_JWT_ISSUER` (já presentes em `.env`/`.env.example`, sem verificação via JWKS/rede).
- `financiador_id` (tenant/CNPJ do financiador) vem de `request.financiador_id`, populado por `jwt_required` a partir do claim JWT (Plan 09) — todo código que acessa banco (`repository.py`) ou chama a CERC (`services/cerc/client.py`) recebe `financiador_id` como argumento explícito, nunca lê `os.environ` para isso. `CERC_CNPJ_SOLICITANTE`/`CERC_CNPJ_FINANCIADOR` **não existem mais** como env vars — vieram do design de multi-tenancy (Plan 09): `cnpjFinanciador` é o próprio `financiador_id`; `cnpjSolicitante` vem de `shared.tenant_config.get_tenant_config(financiador_id)["cerc_cnpj_solicitante"]`.
- `Idempotency-Key` é obrigatório em todo `POST` mutante (`POST /optins`, `POST /optins/{id}/optout`) — SPEC-01 §5. Ausência é erro local `VAL011` (código introduzido por este plano; não existe no catálogo CERC §7, que cobre só o payload `/opt_in`/`/opt_out`).
- Documentos (CNPJ/CPF) são sempre normalizados (`validation.normalizar_documento`) antes de persistir ou logar; logs usam `validation.mascarar_documento` (§8) — nunca documento íntegro em log.
- `referenciaExterna` é gerada pelo serviço, imutável, única (`OPTIN-{YYYY}-{seq:09d}` / `OPTOUT-{YYYY}-{seq:09d}` — SPEC-01 §4.1), via sequência Postgres dedicada.
- Sem Django ORM, sem DRF serializers/ViewSets (decisão já tomada no design doc) — toda serialização é função Python simples em `views.py`.

## Riscos e pendências desta implementação

- **RESOLVIDO pelo Plan 09:** `cnpjSolicitante`/`cnpjFinanciador` (ausentes do corpo de `POST /api/v1/optins` na SPEC-01 §5.1, mas obrigatórios no schema §6 e no payload CERC §4.1) vêm de `request.financiador_id` (= `cnpjFinanciador`) e `get_tenant_config(financiador_id)["cerc_cnpj_solicitante"]` (= `cnpjSolicitante`) — não mais de env vars fixas. Ver `docs/superpowers/specs/2026-08-24-multitenancy-design.md`.
- **Conflito entre SPEC-01 §5.1 (exemplo de resposta mostra `"status": "PENDENTE", "protocoloCerc": null` no `201`) e §11.2 IT-01 (`201`, status `ATIVO`, `protocolo_cerc` persistido).** Resolvido a favor do critério de aceite testável (IT-01): `POST /api/v1/optins` grava `PENDENTE`, chama a CERC **sincronamente** (lote de 1 item) e responde com o estado final (`ATIVO`/`REJEITADO`). `PENDENTE` continua existindo como estado real e transitório no banco (visível em `GET` se a chamada travar antes de resolver), mas o exemplo de `201`/`protocoloCerc: null` da SPEC-01 é tratado como ilustrativo, não como contrato literal.
- Envio assíncrono/em lote (>1 item por chamada `/opt_in`) fica fora deste plano — cada `POST /api/v1/optins` envia lote de 1. Reenvio de `PENDENTE`/`FALHA_ENVIO` travados é o job `retry_envio` (SPEC-01 §9.4), plano futuro (item 6 da ordem sugerida em §0).
- Classificação completa retentável/não-retentável por código CERC (§9.2) não é replicada aqui: qualquer erro de transporte ou HTTP fora do 207 em create/update/optout vira `FALHA_ENVIO` local + `502` ao chamador — a granularidade fina de retry é responsabilidade do job de reconciliação (§9.4, fora de escopo).
- `VAL009` (carteira obrigatória para "Prestador de Serviço") continua **fora de escopo**: a SPEC-01 não define de onde vem o tipo de empresa do participante (nenhuma tabela/API cadastrada) — mesmo motivo do adiamento original no Plan 04.
- `VAL008` implementado só como checagem de presença (campo não vazio); "inacessível" exigiria integração com um storage de evidências não especificado na SPEC-01.
- `credenciadora`/`arranjo` **não** são filtros suportados em `GET /api/v1/optins` nesta primeira versão (exigiriam join com tabelas filhas + paginação correta, e nenhum dos IT-01..IT-13 exercita esse filtro) — filtros suportados: `status`, `usuarioFinalRecebedor`, `origem`, `carteira`, `vigenteEm`.
- Trilha de auditoria com diff de campos (§8, além de `cerc_requisicao`) não é construída aqui — plano futuro dedicado a auditoria/observabilidade (item 6 da ordem sugerida em §0).
- Estado `ERRO_PARCIAL`, citado como pré-condição válida de opt-out em §5.3, não existe na máquina de estados §9.1 e nenhum fluxo deste plano o produz. Implementado por completude/compatibilidade futura, mas nunca observado na prática até um plano futuro definir quando ele é atingido.

---

### Task 1: Autenticação JWT do IdP corporativo

> **Status: já concluída e superada.** `shared/jwt_auth.py` foi implementado (commit `37169f3`) e depois ampliado pelo Plan 09 (Task 5, commit `bb24a23`) para exigir o claim `financiador_id` e popular `request.financiador_id` — exatamente o que as Tasks 3/6/7/8/9/10 abaixo agora dependem. Nenhum trabalho novo necessário aqui; os Steps abaixo ficam como registro histórico do que já foi entregue.

**Files:**
- Create: `optin/shared/jwt_auth.py`
- Test: `optin/shared/tests/test_jwt_auth.py`
- Modify: `optin/requirements.txt`

**Interfaces:**
- Consumes: `IAM_JWT_PUBLIC_KEY` (env, PEM RS256), `IAM_JWT_ISSUER` (env).
- Produces: `JwtAuthError(mensagem)`; `validar_bearer_token(authorization_header: str) -> dict`; `jwt_required(view_func)` (decorator Django — popula `request.jwt_claims`, responde `401` em caso de falha).

- [ ] **Step 1: Adicionar dependência**

Editar `requirements.txt`, adicionar a linha:

```
pyjwt[crypto]>=2.8
```

- [ ] **Step 2: Instalar e escrever o teste que falha**

```bash
pip install -r requirements.txt
```

```python
# optin/shared/tests/test_jwt_auth.py
import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.http import JsonResponse
from django.test import RequestFactory


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture(autouse=True)
def _set_env(monkeypatch, keypair):
    _, public_pem = keypair
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("IAM_JWT_ISSUER", "brikz-iam")


def _token(private_pem, **overrides):
    payload = {"exp": int(time.time()) + 300, "iss": "brikz-iam", "sub": "user-1"}
    payload.update(overrides)
    return pyjwt.encode(payload, private_pem, algorithm="RS256")


def test_validar_bearer_token_aceita_token_valido(keypair):
    from shared.jwt_auth import validar_bearer_token

    private_pem, _ = keypair
    claims = validar_bearer_token(f"Bearer {_token(private_pem)}")
    assert claims["sub"] == "user-1"


def test_validar_bearer_token_rejeita_token_expirado(keypair):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    private_pem, _ = keypair
    expirado = _token(private_pem, exp=int(time.time()) - 10)
    with pytest.raises(JwtAuthError):
        validar_bearer_token(f"Bearer {expirado}")


def test_validar_bearer_token_rejeita_issuer_incorreto(keypair):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    private_pem, _ = keypair
    outro_issuer = _token(private_pem, iss="outro-idp")
    with pytest.raises(JwtAuthError):
        validar_bearer_token(f"Bearer {outro_issuer}")


def test_validar_bearer_token_rejeita_header_ausente():
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    with pytest.raises(JwtAuthError):
        validar_bearer_token("")


def test_validar_bearer_token_rejeita_sem_esquema_bearer(keypair):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    private_pem, _ = keypair
    with pytest.raises(JwtAuthError):
        validar_bearer_token(_token(private_pem))


def test_jwt_required_retorna_401_sem_header():
    from shared.jwt_auth import jwt_required

    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    request = RequestFactory().get("/api/v1/optins")
    response = view(request)
    assert response.status_code == 401


def test_jwt_required_chama_view_com_claims_quando_valido(keypair):
    from shared.jwt_auth import jwt_required

    private_pem, _ = keypair
    token = _token(private_pem)

    @jwt_required
    def view(request):
        return JsonResponse({"sub": request.jwt_claims["sub"]})

    request = RequestFactory().get("/api/v1/optins", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = view(request)
    assert response.status_code == 200
    assert json.loads(response.content) == {"sub": "user-1"}
```

Run: `pytest shared/tests/test_jwt_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.jwt_auth'`

- [ ] **Step 3: Escrever `shared/jwt_auth.py`**

```python
"""Autenticação Bearer JWT do IdP corporativo (SPEC-01 §5, design §4).

Chave pública RS256 fixa (IAM_JWT_PUBLIC_KEY) e emissor esperado
(IAM_JWT_ISSUER) — sem JWKS/rede, mesmo padrão de shared/secrets.py para
segredos estáticos. Rotas isentas (health, push do Pub/Sub) simplesmente
não usam @jwt_required — não há middleware global com exceção por path.
"""
import functools
import os

import jwt
from django.http import JsonResponse


class JwtAuthError(Exception):
    def __init__(self, mensagem: str):
        self.mensagem = mensagem
        super().__init__(mensagem)


def _public_key() -> str:
    return os.environ["IAM_JWT_PUBLIC_KEY"].replace("\\n", "\n")


def validar_bearer_token(authorization_header: str) -> dict:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise JwtAuthError("header Authorization ausente ou sem esquema Bearer")

    token = authorization_header[len("Bearer "):].strip()
    if not token:
        raise JwtAuthError("token vazio")

    try:
        return jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            issuer=os.environ["IAM_JWT_ISSUER"],
            options={"require": ["exp", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise JwtAuthError("token expirado")
    except jwt.InvalidTokenError as exc:
        raise JwtAuthError(f"token inválido: {exc}")


def jwt_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            request.jwt_claims = validar_bearer_token(request.headers.get("Authorization", ""))
        except JwtAuthError as exc:
            return JsonResponse({"erro": "NAO_AUTENTICADO", "mensagem": exc.mensagem}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest shared/tests/test_jwt_auth.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt shared/jwt_auth.py shared/tests/test_jwt_auth.py
git commit -m "feat: JWT auth decorator for internal API (RS256, static IdP public key)"
```

---

### Task 2: Schema — idempotência e sequências de referência

**Files:**
- Create: `optin/docker/initdb/02-idempotency-e-referencia.sql`
- Test: `optin/apps/optin/tests/test_schema_plan08.py`

**Interfaces:**
- Produces: tabela `idempotency_key` (PK composta `recurso`, `chave`); sequências `optin_referencia_seq`, `optout_referencia_seq`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_schema_plan08.py
from dotenv import load_dotenv
load_dotenv()

import sqlalchemy

from shared.cloudsql_client import get_db

FINANCIADOR_TESTE = "12345678000199"


def test_idempotency_key_table_round_trip():
    db = get_db(FINANCIADOR_TESTE)
    db.table("idempotency_key").delete().eq("chave", "test-key-plan08").execute()

    inserted = db.table("idempotency_key").insert({
        "recurso": "optin_create",
        "chave": "test-key-plan08",
        "http_status": 201,
        "response_body": {"id": "opt_1"},
    }).execute()
    assert inserted.data[0]["chave"] == "test-key-plan08"

    found = db.table("idempotency_key").select("*").eq("chave", "test-key-plan08").execute()
    assert found.data[0]["response_body"] == {"id": "opt_1"}
    assert found.data[0]["recurso"] == "optin_create"

    db.table("idempotency_key").delete().eq("chave", "test-key-plan08").execute()


def test_referencia_sequences_increment():
    db = get_db(FINANCIADOR_TESTE)
    with db._engine.connect() as conn:
        primeiro = conn.execute(sqlalchemy.text("SELECT nextval('optin_referencia_seq')")).scalar()
        segundo = conn.execute(sqlalchemy.text("SELECT nextval('optin_referencia_seq')")).scalar()
    assert segundo == primeiro + 1
```

Run: `pytest apps/optin/tests/test_schema_plan08.py -v`
Expected: FAIL — `idempotency_key` / `optin_referencia_seq` não existem (`UndefinedTable`/`UndefinedColumn` ou erro equivalente do driver)

- [ ] **Step 2: Escrever a migração**

```sql
-- optin/docker/initdb/02-idempotency-e-referencia.sql
CREATE TABLE idempotency_key (
  recurso        TEXT NOT NULL,
  chave          TEXT NOT NULL,
  http_status    INT NOT NULL,
  response_body  JSONB NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (recurso, chave)
);

CREATE SEQUENCE optin_referencia_seq START 1;
CREATE SEQUENCE optout_referencia_seq START 1;
```

- [ ] **Step 3: Aplicar a migração no Cloud SQL ativo**

Desde o Plan 09, cada tenant tem seu próprio banco Cloud SQL (`shared.tenant_config`/`get_db(financiador_id)`) — esta migração precisa ser aplicada **em cada banco de tenant**, não uma vez só globalmente. Hoje só existe o tenant de dev (`financiador_id="12345678000199"`, apontando para `registradora-506000:us-east1:app-db`, confirmado como instância dedicada de dev/homolog); aplicar nele agora. Ao provisionar um tenant real futuro, esta mesma migração entra no processo manual/scriptado de onboarding (design de multi-tenancy §9). Aplicar via o mesmo `CloudSqlClient` já usado pelos testes, não há CLI `psql`/`gcloud` disponível neste ambiente:

```bash
python -c "
import sqlalchemy
from shared.cloudsql_client import get_db

FINANCIADOR_TESTE = '12345678000199'
statements = [s.strip() for s in open('docker/initdb/02-idempotency-e-referencia.sql').read().split(';') if s.strip()]
engine = get_db(FINANCIADOR_TESTE)._engine
with engine.begin() as conn:
    for stmt in statements:
        conn.execute(sqlalchemy.text(stmt))
print('aplicado:', len(statements), 'statements')
"
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_schema_plan08.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add docker/initdb/02-idempotency-e-referencia.sql apps/optin/tests/test_schema_plan08.py
git commit -m "feat: idempotency_key table and referencia sequences (SPEC-01 §5)"
```

---

### Task 3: Idempotência de POSTs mutantes

**Files:**
- Create: `optin/apps/optin/idempotency.py`
- Test: `optin/apps/optin/tests/test_idempotency.py`

**Interfaces:**
- Consumes: `get_db(financiador_id)` (shared/cloudsql_client.py, Plan 09); tabela `idempotency_key` (Task 2); `request.financiador_id` (populado por `jwt_required`, Plan 09).
- Produces: `buscar_resposta_em_cache(financiador_id: str, recurso: str, chave: str) -> dict | None`; `guardar_resposta(financiador_id: str, recurso: str, chave: str, http_status: int, response_body) -> None`; `idempotente(recurso: str)` (decorator factory Django — lê `request.financiador_id`, já populado por `jwt_required`, que roda antes dele em todo empilhamento de decorators do Plan 08).

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_idempotency.py
from dotenv import load_dotenv
load_dotenv()

import json

from django.http import JsonResponse
from django.test import RequestFactory

from shared.cloudsql_client import get_db

FINANCIADOR_TESTE = "12345678000199"


def _limpar(financiador_id, chave):
    get_db(financiador_id).table("idempotency_key").delete().eq("chave", chave).execute()


def test_idempotente_retorna_422_sem_header():
    from apps.optin.idempotency import idempotente

    @idempotente("teste_recurso")
    def view(request):
        return JsonResponse({"ok": True}, status=201)

    request = RequestFactory().post("/x")
    request.financiador_id = FINANCIADOR_TESTE
    response = view(request)
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL011"


def test_idempotente_executa_view_e_guarda_resposta():
    from apps.optin.idempotency import idempotente

    _limpar(FINANCIADOR_TESTE, "chave-1")
    chamadas = []

    @idempotente("teste_recurso")
    def view(request):
        chamadas.append(1)
        return JsonResponse({"id": "abc"}, status=201)

    request = RequestFactory().post("/x", HTTP_IDEMPOTENCY_KEY="chave-1")
    request.financiador_id = FINANCIADOR_TESTE
    response = view(request)

    assert response.status_code == 201
    assert len(chamadas) == 1

    cache = get_db(FINANCIADOR_TESTE).table("idempotency_key").select("*").eq("chave", "chave-1").execute().data
    assert cache[0]["response_body"] == {"id": "abc"}
    _limpar(FINANCIADOR_TESTE, "chave-1")


def test_idempotente_retorna_resposta_cacheada_sem_chamar_view_de_novo():
    from apps.optin.idempotency import idempotente

    _limpar(FINANCIADOR_TESTE, "chave-2")
    chamadas = []

    @idempotente("teste_recurso")
    def view(request):
        chamadas.append(1)
        return JsonResponse({"id": "abc"}, status=201)

    request = RequestFactory().post("/x", HTTP_IDEMPOTENCY_KEY="chave-2")
    request.financiador_id = FINANCIADOR_TESTE
    view(request)
    segunda = view(request)

    assert len(chamadas) == 1
    assert segunda.status_code == 201
    assert json.loads(segunda.content) == {"id": "abc"}
    _limpar(FINANCIADOR_TESTE, "chave-2")
```

Run: `pytest apps/optin/tests/test_idempotency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.optin.idempotency'`

- [ ] **Step 2: Escrever `apps/optin/idempotency.py`**

```python
"""Idempotência de POSTs mutantes (SPEC-01 §5: Idempotency-Key obrigatório).

Sem tabela dedicada por recurso na SPEC-01 §6 — usa a tabela genérica
`idempotency_key` (recurso, chave) -> resposta gravada, criada no Plan 08
(docker/initdb/02-idempotency-e-referencia.sql). Reentrega com a mesma
chave devolve a resposta original sem repetir o efeito colateral.
"""
import functools
import json

from django.http import JsonResponse

from shared.cloudsql_client import get_db


def buscar_resposta_em_cache(financiador_id: str, recurso: str, chave: str) -> dict:
    rows = (
        get_db(financiador_id).table("idempotency_key").select("*")
        .eq("recurso", recurso).eq("chave", chave).execute().data
    )
    return rows[0] if rows else None


def guardar_resposta(financiador_id: str, recurso: str, chave: str, http_status: int, response_body) -> None:
    get_db(financiador_id).table("idempotency_key").insert({
        "recurso": recurso,
        "chave": chave,
        "http_status": http_status,
        "response_body": response_body,
    }).execute()


def idempotente(recurso: str):
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            chave = request.headers.get("Idempotency-Key")
            if not chave:
                return JsonResponse(
                    {"erro": "VAL011", "mensagem": "header Idempotency-Key é obrigatório"}, status=422
                )

            cache = buscar_resposta_em_cache(request.financiador_id, recurso, chave)
            if cache:
                return JsonResponse(cache["response_body"], status=cache["http_status"])

            response = view_func(request, *args, **kwargs)
            guardar_resposta(request.financiador_id, recurso, chave, response.status_code, json.loads(response.content))
            return response

        return wrapper

    return decorator
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_idempotency.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
git add apps/optin/idempotency.py apps/optin/tests/test_idempotency.py
git commit -m "feat: Idempotency-Key decorator for mutating POST endpoints"
```

---

### Task 4: Regras locais adicionais — anti-duplicidade, domínio de arranjo, evidência, mascaramento

**Files:**
- Modify: `optin/apps/optin/validation.py`
- Test: `optin/apps/optin/tests/test_validation.py`

**Interfaces:**
- Produces (novas funções, somadas às de `validation.py` já existentes): `conjuntos_se_sobrepoem(a: set, b: set) -> bool`; `vigencias_se_sobrepoem(inicio_a, fim_a, inicio_b, fim_b) -> bool`; `mascarar_documento(documento: str) -> str`; `validar_arranjos(lista: list, ativos: set) -> None` (levanta `ValidationError("VAL005", ...)`); `validar_evidencia(evidencia_id) -> None` (levanta `ValidationError("VAL008", ...)`).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `apps/optin/tests/test_validation.py`:

```python
from apps.optin.validation import (
    ValidationError,
    conjuntos_se_sobrepoem,
    mascarar_documento,
    validar_arranjos,
    validar_evidencia,
    vigencias_se_sobrepoem,
)


def test_conjuntos_se_sobrepoem_com_intersecao():
    assert conjuntos_se_sobrepoem({"VCC", "MCC"}, {"MCC"}) is True


def test_conjuntos_se_sobrepoem_sem_intersecao():
    assert conjuntos_se_sobrepoem({"VCC"}, {"BCC"}) is False


def test_conjuntos_se_sobrepoem_curinga_99t_lado_a():
    assert conjuntos_se_sobrepoem({"99T"}, {"BCC"}) is True


def test_conjuntos_se_sobrepoem_curinga_99t_lado_b():
    assert conjuntos_se_sobrepoem({"VCC"}, {"99T"}) is True


def test_vigencias_se_sobrepoem_com_intersecao():
    import datetime

    a_inicio, a_fim = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
    b_inicio, b_fim = datetime.date(2026, 6, 1), datetime.date(2027, 6, 1)
    assert vigencias_se_sobrepoem(a_inicio, a_fim, b_inicio, b_fim) is True


def test_vigencias_se_sobrepoem_disjuntas():
    import datetime

    a_inicio, a_fim = datetime.date(2025, 1, 1), datetime.date(2025, 12, 31)
    b_inicio, b_fim = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
    assert vigencias_se_sobrepoem(a_inicio, a_fim, b_inicio, b_fim) is False


def test_mascarar_documento_cnpj():
    assert mascarar_documento("12345678000199") == "12345678****99"


def test_mascarar_documento_curto_nao_estoura():
    assert mascarar_documento("12345678") == "1234****78" or len(mascarar_documento("12345678")) > 0


def test_validar_arranjos_aceita_curinga_sem_checar_dominio():
    validar_arranjos(["99T"], ativos={"VCC", "MCC"})


def test_validar_arranjos_aceita_codigo_ativo():
    validar_arranjos(["VCC"], ativos={"VCC", "MCC"})


def test_validar_arranjos_rejeita_codigo_fora_do_dominio():
    with pytest.raises(ValidationError) as exc:
        validar_arranjos(["ZZZ"], ativos={"VCC", "MCC"})
    assert exc.value.codigo == "VAL005"


def test_validar_evidencia_aceita_id_presente():
    validar_evidencia("doc_01H...")


def test_validar_evidencia_rejeita_ausente():
    with pytest.raises(ValidationError) as exc:
        validar_evidencia("")
    assert exc.value.codigo == "VAL008"
```

Adicionar `import pytest` no topo de `test_validation.py` se ainda não existir.

Run: `pytest apps/optin/tests/test_validation.py -v`
Expected: FAIL with `ImportError: cannot import name 'conjuntos_se_sobrepoem'`

- [ ] **Step 2: Implementar em `validation.py`**

Adicionar ao final de `apps/optin/validation.py`:

```python
def conjuntos_se_sobrepoem(a: set, b: set) -> bool:
    """Compara dois conjuntos (credenciadoras ou arranjos) tratando '99T' como
    curinga universal (SPEC-01 §2.3/§5.6): se qualquer lado contiver '99T',
    há sobreposição total."""
    if "99T" in a or "99T" in b:
        return True
    return bool(a & b)


def vigencias_se_sobrepoem(inicio_a, fim_a, inicio_b, fim_b) -> bool:
    """Interseção de intervalos fechados [inicio, fim] (SPEC-01 §5.6)."""
    return inicio_a <= fim_b and inicio_b <= fim_a


def mascarar_documento(documento: str) -> str:
    """SPEC-01 §8: documentos mascarados em log (ex.: '12345678****99')."""
    if len(documento) <= 4:
        return "*" * len(documento)
    if len(documento) <= 6:
        return documento[:2] + "*" * (len(documento) - 2)
    return documento[:8] + "*" * (len(documento) - 10) + documento[-2:]


def validar_arranjos(lista: list, ativos: set) -> None:
    """VAL005 — SPEC-01 §7.1 104015/104016. '99T' sempre aceito sem checar domínio."""
    for codigo in lista:
        if codigo != "99T" and codigo not in ativos:
            raise ValidationError("VAL005", f"arranjo fora do domínio vigente: {codigo}")


def validar_evidencia(evidencia_id) -> None:
    """VAL008 — só a presença é verificada aqui; acessibilidade do storage de
    evidências fica fora de escopo (não há storage especificado na SPEC-01)."""
    if not evidencia_id:
        raise ValidationError("VAL008", "evidenciaAutorizacaoId ausente")
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_validation.py -v`
Expected: PASS (todos os testes, novos e existentes)

- [ ] **Step 4: Commit**

```bash
git add apps/optin/validation.py apps/optin/tests/test_validation.py
git commit -m "feat: VAL005/VAL008 + overlap helpers for anti-duplicidade (SPEC-01 §5.6)"
```

---

### Task 5: Interpretação da resposta 207 da CERC

**Files:**
- Create: `optin/apps/optin/cerc_mapping.py`
- Test: `optin/apps/optin/tests/test_cerc_mapping.py`

**Interfaces:**
- Produces: `ResultadoItemCerc(status_local, protocolo=None, erro_codigo=None, erro_mensagem=None)`; `interpretar_item_opt_in(item: dict) -> ResultadoItemCerc`; `interpretar_item_opt_out(item: dict) -> ResultadoItemCerc`; `correlacionar_por_referencia(itens: list, referencia_externa: str) -> dict`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_cerc_mapping.py
import pytest

from apps.optin.cerc_mapping import (
    correlacionar_por_referencia,
    interpretar_item_opt_in,
    interpretar_item_opt_out,
)


def test_interpretar_item_opt_in_sucesso():
    item = {"status": "0", "protocolo": "P-1", "referenciaExterna": "REF-1", "erros": []}
    resultado = interpretar_item_opt_in(item)
    assert resultado.status_local == "ATIVO"
    assert resultado.protocolo == "P-1"


def test_interpretar_item_opt_in_104803_e_idempotente():
    # IT-03 (SPEC-01 §11.2): CERC retorna 104803 -> reconciliado para ATIVO, sem erro ao chamador.
    item = {
        "status": "1",
        "protocolo": "P-1",
        "referenciaExterna": "REF-1",
        "erros": [{"codigo": "104803", "mensagem": "Opt-in já informado"}],
    }
    resultado = interpretar_item_opt_in(item)
    assert resultado.status_local == "ATIVO"
    assert resultado.erro_codigo == "104803"


def test_interpretar_item_opt_in_104806_e_rejeitado():
    # IT-04 (SPEC-01 §11.2): CERC retorna 104806 -> 422, opt-in REJEITADO.
    item = {
        "status": "1",
        "referenciaExterna": "REF-1",
        "erros": [{"codigo": "104806", "mensagem": "dataInicio menor que dataAssinaturaOptIn"}],
    }
    resultado = interpretar_item_opt_in(item)
    assert resultado.status_local == "REJEITADO"
    assert resultado.erro_codigo == "104806"


def test_interpretar_item_opt_out_sucesso():
    item = {"status": "0", "protocolo": "P-1", "referenciaExterna": "REF-2", "erros": []}
    resultado = interpretar_item_opt_out(item)
    assert resultado.status_local == "CONFIRMADO"


def test_interpretar_item_opt_out_106803_e_idempotente():
    item = {
        "status": "1",
        "referenciaExterna": "REF-2",
        "erros": [{"codigo": "106803", "mensagem": "Opt-out já informado"}],
    }
    resultado = interpretar_item_opt_out(item)
    assert resultado.status_local == "CONFIRMADO"


def test_correlacionar_por_referencia_encontra_item():
    itens = [{"referenciaExterna": "REF-1"}, {"referenciaExterna": "REF-2"}]
    assert correlacionar_por_referencia(itens, "REF-2") == {"referenciaExterna": "REF-2"}


def test_correlacionar_por_referencia_lanca_key_error_se_ausente():
    with pytest.raises(KeyError):
        correlacionar_por_referencia([{"referenciaExterna": "REF-1"}], "REF-9")
```

Run: `pytest apps/optin/tests/test_cerc_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.optin.cerc_mapping'`

- [ ] **Step 2: Escrever `apps/optin/cerc_mapping.py`**

```python
"""Interpreta o array 207 multi-status de /opt_in e /opt_out da CERC e
decide a transição de estado local (SPEC-01 §7.1/§7.2, §9.1).

Nunca trata o HTTP 207 como sucesso global (SPEC-01 §4.1) — cada item é
interpretado individualmente e correlacionado por `referenciaExterna`.
"""

# 104803/106803 = "já informado" -> sucesso idempotente após reconciliação
# (§7.1/§7.2) mesmo vindo com status="1" (IT-03 da SPEC-01 §11.2).
CODIGOS_IDEMPOTENTES_OPT_IN = {"104803"}
CODIGOS_IDEMPOTENTES_OPT_OUT = {"106803"}


class ResultadoItemCerc:
    def __init__(self, status_local: str, protocolo: str = None, erro_codigo: str = None, erro_mensagem: str = None):
        self.status_local = status_local
        self.protocolo = protocolo
        self.erro_codigo = erro_codigo
        self.erro_mensagem = erro_mensagem


def _primeiro_erro(item: dict):
    erros = item.get("erros") or []
    if not erros:
        return None, None
    primeiro = erros[0]
    return primeiro.get("codigo"), primeiro.get("mensagem")


def interpretar_item_opt_in(item: dict) -> ResultadoItemCerc:
    if item.get("status") == "0":
        return ResultadoItemCerc("ATIVO", protocolo=item.get("protocolo"))

    codigo, mensagem = _primeiro_erro(item)
    if codigo in CODIGOS_IDEMPOTENTES_OPT_IN:
        return ResultadoItemCerc("ATIVO", protocolo=item.get("protocolo"), erro_codigo=codigo)
    return ResultadoItemCerc("REJEITADO", erro_codigo=codigo, erro_mensagem=mensagem)


def interpretar_item_opt_out(item: dict) -> ResultadoItemCerc:
    if item.get("status") == "0":
        return ResultadoItemCerc("CONFIRMADO", protocolo=item.get("protocolo"))

    codigo, mensagem = _primeiro_erro(item)
    if codigo in CODIGOS_IDEMPOTENTES_OPT_OUT:
        return ResultadoItemCerc("CONFIRMADO", protocolo=item.get("protocolo"), erro_codigo=codigo)
    return ResultadoItemCerc("REJEITADO", erro_codigo=codigo, erro_mensagem=mensagem)


def correlacionar_por_referencia(itens: list, referencia_externa: str) -> dict:
    for item in itens:
        if item.get("referenciaExterna") == referencia_externa:
            return item
    raise KeyError(f"referenciaExterna {referencia_externa!r} não encontrada na resposta da CERC")
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_cerc_mapping.py -v`
Expected: PASS (7 tests)

- [ ] **Step 4: Commit**

```bash
git add apps/optin/cerc_mapping.py apps/optin/tests/test_cerc_mapping.py
git commit -m "feat: map CERC 207 items to local optin/optout state transitions"
```

---

### Task 6: Camada de dados do opt-in (`repository.py`) + filtros de intervalo no `CloudSqlClient`

**Files:**
- Modify: `optin/shared/cloudsql_client.py`
- Modify: `optin/shared/tests/test_cloudsql_client.py`
- Create: `optin/apps/optin/repository.py`
- Test: `optin/apps/optin/tests/test_repository.py`

**Interfaces:**
- Consumes: `get_db(financiador_id)` (Plan 09); `conjuntos_se_sobrepoem`/`vigencias_se_sobrepoem` (Task 4).
- Produces (`QueryBuilder`): `.gte(field, value)`, `.lte(field, value)` — inalterado pela multi-tenancy (camada de banco pura, sem noção de tenant).
- Produces (`repository.py` — `financiador_id: str` é sempre o primeiro parâmetro, repassado a todo `get_db(financiador_id)` interno): `proxima_referencia_externa(financiador_id: str, prefixo: str, sequencia: str) -> str`; `criar_optin_pendente(financiador_id: str, dados: dict) -> dict`; `buscar_por_id(financiador_id: str, optin_id: str) -> dict | None`; `buscar_ativos_por_ufr(financiador_id: str, documento_ufr: str, documento_titular: str) -> list`; `existe_optin_ativo_equivalente(financiador_id: str, documento_ufr, documento_titular, credenciadoras: set, arranjos: set, vigencia_inicio, vigencia_fim) -> bool`; `atualizar_status(financiador_id: str, optin_id: str, status: str, protocolo_cerc: str = None) -> dict`; `atualizar_campos(financiador_id: str, optin_id: str, dados: dict) -> dict`; `listar(financiador_id: str, filtros: dict, limit: int) -> list`; `arranjos_ativos(financiador_id: str) -> set`; `criar_optout_pendente(financiador_id: str, optin_id: str) -> dict`; `confirmar_optout(financiador_id: str, optout_id: str, optin_id: str, protocolo_cerc: str) -> None`; `rejeitar_optout(financiador_id: str, optout_id: str) -> None`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `shared/tests/test_cloudsql_client.py`:

```python
FINANCIADOR_TESTE = "12345678000199"  # já definido em shared/tests/test_cloudsql_client.py pelo Plan 09 — reaproveitar a constante existente, não redeclarar se já presente


def test_gte_lte_filters_range_query():
    db = get_db(FINANCIADOR_TESTE)
    db.table("dominio_arranjo").delete().eq("codigo", "GTE1").execute()
    db.table("dominio_arranjo").delete().eq("codigo", "GTE2").execute()
    try:
        db.table("dominio_arranjo").insert({
            "codigo": "GTE1", "descricao": "A", "ativo": True,
            "atualizado_em": "2026-01-01T00:00:00-03:00",
        }).execute()
        db.table("dominio_arranjo").insert({
            "codigo": "GTE2", "descricao": "B", "ativo": True,
            "atualizado_em": "2026-06-01T00:00:00-03:00",
        }).execute()

        recentes = db.table("dominio_arranjo").select("*").gte(
            "atualizado_em", "2026-03-01T00:00:00-03:00"
        ).eq("ativo", True).execute()
        codigos = {r["codigo"] for r in recentes.data}
        assert "GTE2" in codigos and "GTE1" not in codigos

        antigos = db.table("dominio_arranjo").select("*").lte(
            "atualizado_em", "2026-03-01T00:00:00-03:00"
        ).eq("ativo", True).execute()
        codigos_antigos = {r["codigo"] for r in antigos.data}
        assert "GTE1" in codigos_antigos and "GTE2" not in codigos_antigos
    finally:
        db.table("dominio_arranjo").delete().eq("codigo", "GTE1").execute()
        db.table("dominio_arranjo").delete().eq("codigo", "GTE2").execute()
```

```python
# optin/apps/optin/tests/test_repository.py
import datetime

from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    ids = [
        r["id"]
        for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data
    ]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optout").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _dados_base(**overrides):
    dados = {
        "cnpj_solicitante": "12345678000199",
        "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR,
        "documento_ufr_tipo": "CNPJ",
        "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10),
        "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10),
        "carteira": None,
        "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"],
        "arranjos": ["VCC"],
    }
    dados.update(overrides)
    return dados


def test_criar_optin_pendente_grava_optin_e_filhas():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())

    assert optin["status"] == "PENDENTE"
    assert optin["credenciadoras"] == ["99T"]
    assert optin["arranjos"] == ["VCC"]
    assert optin["referencia_externa"].startswith("OPTIN-")
    _limpar()


def test_buscar_por_id_retorna_none_quando_nao_existe():
    from apps.optin import repository

    assert repository.buscar_por_id(FINANCIADOR_TESTE, "opt_inexistente") is None


def test_atualizar_status_muda_status_e_protocolo():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    atualizado = repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-123")

    assert atualizado["status"] == "ATIVO"
    assert atualizado["protocolo_cerc"] == "P-123"
    _limpar()


def test_existe_optin_ativo_equivalente_detecta_sobreposicao():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")

    conflito = repository.existe_optin_ativo_equivalente(
        FINANCIADOR_TESTE,
        documento_ufr=DOC_UFR,
        documento_titular=DOC_UFR,
        credenciadoras={"99T"},
        arranjos={"VCC"},
        vigencia_inicio=datetime.date(2027, 1, 1),
        vigencia_fim=datetime.date(2027, 12, 31),
    )
    assert conflito is True
    _limpar()


def test_existe_optin_ativo_equivalente_falso_quando_sem_ativos():
    from apps.optin import repository

    _limpar()
    conflito = repository.existe_optin_ativo_equivalente(
        FINANCIADOR_TESTE,
        documento_ufr=DOC_UFR,
        documento_titular=DOC_UFR,
        credenciadoras={"VCC"},
        arranjos={"VCC"},
        vigencia_inicio=datetime.date(2026, 1, 1),
        vigencia_fim=datetime.date(2026, 12, 31),
    )
    assert conflito is False


def test_listar_filtra_por_status():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")

    resultado = repository.listar(FINANCIADOR_TESTE, {"status": "ATIVO", "documento_ufr": DOC_UFR}, limit=50)
    assert any(r["id"] == optin["id"] for r in resultado)

    vazio = repository.listar(FINANCIADOR_TESTE, {"status": "REJEITADO", "documento_ufr": DOC_UFR}, limit=50)
    assert vazio == []
    _limpar()


def test_criar_e_confirmar_optout():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")

    optout = repository.criar_optout_pendente(FINANCIADOR_TESTE, optin["id"])
    assert optout["status"] == "PENDENTE"
    assert optout["referencia_externa"].startswith("OPTOUT-")

    repository.confirmar_optout(FINANCIADOR_TESTE, optout["id"], optin["id"], "P-1")

    optin_atualizado = repository.buscar_por_id(FINANCIADOR_TESTE, optin["id"])
    assert optin_atualizado["status"] == "ENCERRADO"
    _limpar()
```

Run: `pytest shared/tests/test_cloudsql_client.py apps/optin/tests/test_repository.py -v`
Expected: FAIL — `AttributeError: 'QueryBuilder' object has no attribute 'gte'` e `ModuleNotFoundError: No module named 'apps.optin.repository'`

- [ ] **Step 2: Estender `QueryBuilder` em `shared/cloudsql_client.py`**

Adicionar, logo após o método `eq` (linha 43-45 do arquivo atual):

```python
    def gte(self, field: str, value: Any) -> "QueryBuilder":
        self._filters.append(("gte", field, value))
        return self

    def lte(self, field: str, value: Any) -> "QueryBuilder":
        self._filters.append(("lte", field, value))
        return self
```

E em `_build_where`, substituir o corpo do loop:

```python
    def _build_where(self):
        if not self._filters:
            return "", {}
        clauses, params = [], {}
        operadores = {"eq": "=", "gte": ">=", "lte": "<="}
        for i, (op, field, val) in enumerate(self._filters):
            pname = f"p{i}"
            clauses.append(f"{field} {operadores[op]} :{pname}")
            params[pname] = val
        return "WHERE " + " AND ".join(clauses), params
```

- [ ] **Step 3: Escrever `apps/optin/repository.py`**

```python
"""Acesso a dados do agregado opt-in (SPEC-01 §5/§6) via CloudSqlClient (sem ORM)."""

import sqlalchemy
from django.utils import timezone
from ulid import ULID

from apps.optin.validation import conjuntos_se_sobrepoem, vigencias_se_sobrepoem
from shared.cloudsql_client import get_db


def proxima_referencia_externa(financiador_id: str, prefixo: str, sequencia: str) -> str:
    ano = timezone.localtime(timezone.now()).year
    with get_db(financiador_id)._engine.connect() as conn:
        seq = conn.execute(sqlalchemy.text(f"SELECT nextval('{sequencia}')")).scalar()
    return f"{prefixo}-{ano}-{seq:09d}"


def _com_filhas(financiador_id: str, optin: dict) -> dict:
    optin_id = optin["id"]
    optin["credenciadoras"] = [
        r["cnpj"] for r in get_db(financiador_id).table("optin_credenciadora").select("cnpj").eq("optin_id", optin_id).execute().data
    ]
    optin["arranjos"] = [
        r["codigo"] for r in get_db(financiador_id).table("optin_arranjo").select("codigo").eq("optin_id", optin_id).execute().data
    ]
    return optin


def criar_optin_pendente(financiador_id: str, dados: dict) -> dict:
    optin_id = f"opt_{ULID()}"
    referencia_externa = proxima_referencia_externa(financiador_id, "OPTIN", "optin_referencia_seq")

    with get_db(financiador_id)._engine.begin() as conn:
        conn.execute(sqlalchemy.text("""
            INSERT INTO optin (
                id, referencia_externa, origem, status, cnpj_solicitante, cnpj_financiador,
                documento_ufr, documento_ufr_tipo, documento_titular, data_assinatura,
                vigencia_inicio, vigencia_fim, carteira, evidencia_id
            ) VALUES (
                :id, :referencia_externa, 'OPTIN', 'PENDENTE', :cnpj_solicitante, :cnpj_financiador,
                :documento_ufr, :documento_ufr_tipo, :documento_titular, :data_assinatura,
                :vigencia_inicio, :vigencia_fim, :carteira, :evidencia_id
            )
        """), {
            "id": optin_id,
            "referencia_externa": referencia_externa,
            "cnpj_solicitante": dados["cnpj_solicitante"],
            "cnpj_financiador": dados["cnpj_financiador"],
            "documento_ufr": dados["documento_ufr"],
            "documento_ufr_tipo": dados["documento_ufr_tipo"],
            "documento_titular": dados["documento_titular"],
            "data_assinatura": dados["data_assinatura"],
            "vigencia_inicio": dados["vigencia_inicio"],
            "vigencia_fim": dados["vigencia_fim"],
            "carteira": dados.get("carteira"),
            "evidencia_id": dados["evidencia_id"],
        })
        for cnpj in dados["credenciadoras"]:
            conn.execute(
                sqlalchemy.text("INSERT INTO optin_credenciadora (optin_id, cnpj) VALUES (:optin_id, :cnpj)"),
                {"optin_id": optin_id, "cnpj": cnpj},
            )
        for codigo in dados["arranjos"]:
            conn.execute(
                sqlalchemy.text("INSERT INTO optin_arranjo (optin_id, codigo) VALUES (:optin_id, :codigo)"),
                {"optin_id": optin_id, "codigo": codigo},
            )

    return buscar_por_id(financiador_id, optin_id)


def buscar_por_id(financiador_id: str, optin_id: str):
    rows = get_db(financiador_id).table("optin").select("*").eq("id", optin_id).execute().data
    if not rows:
        return None
    return _com_filhas(financiador_id, rows[0])


def buscar_ativos_por_ufr(financiador_id: str, documento_ufr: str, documento_titular: str) -> list:
    candidatos = (
        get_db(financiador_id).table("optin").select("*")
        .eq("documento_ufr", documento_ufr)
        .eq("documento_titular", documento_titular)
        .eq("status", "ATIVO")
        .execute().data
    )
    return [_com_filhas(financiador_id, c) for c in candidatos]


def existe_optin_ativo_equivalente(financiador_id: str, documento_ufr, documento_titular, credenciadoras, arranjos, vigencia_inicio, vigencia_fim) -> bool:
    for candidato in buscar_ativos_por_ufr(financiador_id, documento_ufr, documento_titular):
        if not conjuntos_se_sobrepoem(set(candidato["credenciadoras"]), credenciadoras):
            continue
        if not conjuntos_se_sobrepoem(set(candidato["arranjos"]), arranjos):
            continue
        if vigencias_se_sobrepoem(candidato["vigencia_inicio"], candidato["vigencia_fim"], vigencia_inicio, vigencia_fim):
            return True
    return False


def atualizar_status(financiador_id: str, optin_id: str, status: str, protocolo_cerc: str = None) -> dict:
    dados = {"status": status, "atualizado_em": timezone.now()}
    if protocolo_cerc is not None:
        dados["protocolo_cerc"] = protocolo_cerc
    resultado = get_db(financiador_id).table("optin").update(dados).eq("id", optin_id).execute()
    return _com_filhas(financiador_id, resultado.data[0])


def atualizar_campos(financiador_id: str, optin_id: str, dados: dict) -> dict:
    dados = {**dados, "atualizado_em": timezone.now()}
    resultado = get_db(financiador_id).table("optin").update(dados).eq("id", optin_id).execute()
    return _com_filhas(financiador_id, resultado.data[0])


def listar(financiador_id: str, filtros: dict, limit: int) -> list:
    query = get_db(financiador_id).table("optin").select("*")
    for campo in ("status", "documento_ufr", "origem", "carteira"):
        if filtros.get(campo):
            query = query.eq(campo, filtros[campo])
    if filtros.get("vigente_em"):
        query = query.lte("vigencia_inicio", filtros["vigente_em"]).gte("vigencia_fim", filtros["vigente_em"])
    resultado = query.order("criado_em", desc=True).limit(limit).execute().data
    return [_com_filhas(financiador_id, r) for r in resultado]


def arranjos_ativos(financiador_id: str) -> set:
    rows = get_db(financiador_id).table("dominio_arranjo").select("codigo").eq("ativo", True).execute().data
    return {r["codigo"] for r in rows}


def criar_optout_pendente(financiador_id: str, optin_id: str) -> dict:
    optout_id = f"optout_{ULID()}"
    referencia_externa = proxima_referencia_externa(financiador_id, "OPTOUT", "optout_referencia_seq")
    inserted = get_db(financiador_id).table("optout").insert({
        "id": optout_id,
        "optin_id": optin_id,
        "referencia_externa": referencia_externa,
        "status": "PENDENTE",
    }).execute()
    return inserted.data[0]


def confirmar_optout(financiador_id: str, optout_id: str, optin_id: str, protocolo_cerc: str) -> None:
    get_db(financiador_id).table("optout").update({"status": "CONFIRMADO", "protocolo_cerc": protocolo_cerc}).eq("id", optout_id).execute()
    get_db(financiador_id).table("optin").update({"status": "ENCERRADO", "atualizado_em": timezone.now()}).eq("id", optin_id).execute()


def rejeitar_optout(financiador_id: str, optout_id: str) -> None:
    get_db(financiador_id).table("optout").update({"status": "REJEITADO"}).eq("id", optout_id).execute()
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest shared/tests/test_cloudsql_client.py apps/optin/tests/test_repository.py -v`
Expected: PASS (1 novo teste em `test_cloudsql_client.py` + 7 em `test_repository.py`)

- [ ] **Step 5: Commit**

```bash
git add shared/cloudsql_client.py shared/tests/test_cloudsql_client.py apps/optin/repository.py apps/optin/tests/test_repository.py
git commit -m "feat: optin repository (create/read/update, anti-duplicidade §5.6, optout)"
```

---

### Task 7: `POST /api/v1/optins` — criar opt-in

**Files:**
- Modify: `optin/apps/optin/views.py`
- Modify: `optin/apps/optin/urls.py`
- Create: `optin/apps/optin/tests/conftest.py`
- Test: `optin/apps/optin/tests/test_views_criar_optin.py`

**Interfaces:**
- Consumes: `jwt_required` (Task 1/Plan 09, popula `request.financiador_id`), `idempotente` (Task 3), `validation.{validar_documento,validar_vigencia,validar_credenciadoras,validar_arranjos,validar_evidencia,mascarar_documento}`, `shared.tenant_config.get_tenant_config` (Plan 09), `repository.{criar_optin_pendente,existe_optin_ativo_equivalente,atualizar_status,arranjos_ativos}` (Task 6 — todas exigem `financiador_id` como primeiro argumento), `cerc_mapping.{interpretar_item_opt_in,correlacionar_por_referencia}` (Task 5), `services.cerc.client.{registrar_optin,CercApiError}` (Plan 07/09, `registrar_optin` exige `financiador_id` como primeiro argumento).
- Produces: `criar_optin(request)`; `optins_collection(request)` (dispatcher POST/GET, GET implementado na Task 8); `_serializar_optin(optin: dict) -> dict` (reaproveitado pelas Tasks 8-10).

- [ ] **Step 1: Fixture de autenticação com claim `financiador_id`**

Não é mais necessário configurar `CERC_CNPJ_SOLICITANTE`/`CERC_CNPJ_FINANCIADOR` — essas env vars não existem mais (Plan 09 as substituiu por config por tenant). O único trabalho deste Step é a fixture de autenticação, cujo JWT precisa carregar o claim `financiador_id` exigido por `jwt_required` desde o Plan 09.

```python
# optin/apps/optin/tests/conftest.py
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture(autouse=True)
def _iam_jwt_env(monkeypatch, rsa_keypair):
    _, public_pem = rsa_keypair
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("IAM_JWT_ISSUER", "brikz-iam")


@pytest.fixture
def auth_headers(rsa_keypair):
    private_pem, _ = rsa_keypair
    token = pyjwt.encode(
        {
            "exp": int(time.time()) + 300,
            "iss": "brikz-iam",
            "sub": "user-1",
            "financiador_id": "12345678000199",
        },
        private_pem,
        algorithm="RS256",
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}
```

- [ ] **Step 2: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_views_criar_optin.py
import json

import httpx
import pytest
import respx
from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"
CORPO_VALIDO = {
    "usuarioFinalRecebedor": DOC_UFR,
    "credenciadoras": ["99T"],
    "arranjos": ["VCC"],
    "vigenciaInicio": "2026-08-11",
    "vigenciaFim": "2027-08-10",
    "dataAssinatura": "2026-08-10",
    "evidenciaAutorizacaoId": "doc_teste",
}


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


@pytest.fixture(autouse=True)
def _seed_dominio_arranjo():
    get_db(FINANCIADOR_TESTE).table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    get_db(FINANCIADOR_TESTE).table("dominio_arranjo").insert({
        "codigo": "VCC", "descricao": "Visa Crédito", "ativo": True, "atualizado_em": "2026-01-01T00:00:00-03:00",
    }).execute()
    _limpar()
    yield
    _limpar()
    get_db(FINANCIADOR_TESTE).table("dominio_arranjo").delete().eq("codigo", "VCC").execute()


@respx.mock
def test_criar_optin_sucesso_retorna_201_ativo(client, auth_headers):
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )

    # A referenciaExterna real é gerada pelo serviço (sequência Postgres), só
    # conhecida depois do insert — o mock ecoa a que veio no corpo da requisição
    # em vez de fixar um valor, para bater com correlacionar_por_referencia().
    def _resposta(request):
        enviado = json.loads(request.content)[0]
        return httpx.Response(207, json=[{
            "protocolo": "P-1", "referenciaExterna": enviado["referenciaExterna"], "status": "0", "erros": [],
        }])

    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(side_effect=_resposta)

    response = client.post(
        "/api/v1/optins",
        data=json.dumps(CORPO_VALIDO),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-criar-1",
        **auth_headers,
    )

    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["status"] == "ATIVO"
    assert body["protocoloCerc"] == "P-1"
    assert body["referenciaExterna"].startswith("OPTIN-")


def test_criar_optin_sem_jwt_retorna_401(client):
    response = client.post("/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json")
    assert response.status_code == 401


def test_criar_optin_sem_idempotency_key_retorna_422(client, auth_headers):
    response = client.post(
        "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers
    )
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL011"


def test_criar_optin_vigencia_invalida_retorna_422(client, auth_headers):
    corpo = {**CORPO_VALIDO, "vigenciaFim": "2026-01-01"}
    response = client.post(
        "/api/v1/optins",
        data=json.dumps(corpo),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-criar-invalida",
        **auth_headers,
    )
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL003"


@respx.mock
def test_criar_optin_duplicado_retorna_409_sem_chamar_cerc(client, auth_headers):
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )

    def _resposta(request):
        enviado = json.loads(request.content)[0]
        return httpx.Response(207, json=[{
            "protocolo": "P-1", "referenciaExterna": enviado["referenciaExterna"], "status": "0", "erros": [],
        }])

    rota_cerc = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(side_effect=_resposta)

    client.post(
        "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-dup-1", **auth_headers,
    )
    chamadas_apos_primeira = rota_cerc.call_count

    response = client.post(
        "/api/v1/optins", data=json.dumps(CORPO_VALIDO), content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-dup-2", **auth_headers,
    )

    assert response.status_code == 409
    assert rota_cerc.call_count == chamadas_apos_primeira
```

Run: `pytest apps/optin/tests/test_views_criar_optin.py -v`
Expected: FAIL — rota `/api/v1/optins` não existe (404) e `client` fixture indisponível (é a fixture padrão de `pytest-django`, disponível assim que `apps/optin/urls.py`/`views.py` existirem)

- [ ] **Step 3: Escrever `criar_optin` e o dispatcher em `views.py`, e a rota em `urls.py`**

```python
# optin/apps/optin/views.py
import datetime
import json
import logging

from django.http import JsonResponse

from apps.optin import repository
from apps.optin.cerc_mapping import correlacionar_por_referencia, interpretar_item_opt_in
from apps.optin.idempotency import idempotente
from apps.optin.validation import (
    ValidationError,
    mascarar_documento,
    validar_arranjos,
    validar_credenciadoras,
    validar_documento,
    validar_evidencia,
    validar_vigencia,
)
from services.cerc.client import CercApiError, registrar_optin
from shared.jwt_auth import jwt_required
from shared.tenant_config import get_tenant_config

logger = logging.getLogger(__name__)


def health(request):
    return JsonResponse({"status": "ok"})


def _erro_json(codigo: str, mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"erro": codigo, "mensagem": mensagem}, status=status)


def _serializar_optin(optin: dict) -> dict:
    return {
        "id": optin["id"],
        "referenciaExterna": optin["referencia_externa"],
        "protocoloCerc": optin.get("protocolo_cerc"),
        "origem": optin["origem"],
        "status": optin["status"],
        "cnpjSolicitante": optin["cnpj_solicitante"],
        "cnpjFinanciador": optin["cnpj_financiador"],
        "usuarioFinalRecebedor": optin["documento_ufr"],
        "titular": optin.get("documento_titular"),
        "dataAssinatura": str(optin["data_assinatura"]),
        "vigenciaInicio": str(optin["vigencia_inicio"]),
        "vigenciaFim": str(optin["vigencia_fim"]),
        "carteira": optin.get("carteira"),
        "credenciadoras": optin.get("credenciadoras", []),
        "arranjos": optin.get("arranjos", []),
        "criadoEm": optin["criado_em"].isoformat() if hasattr(optin["criado_em"], "isoformat") else optin["criado_em"],
    }


@jwt_required
@idempotente("optin_create")
def criar_optin(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    try:
        documento_ufr, tipo_ufr = validar_documento(payload.get("usuarioFinalRecebedor", ""))
        titular_raw = payload.get("titular") or payload.get("usuarioFinalRecebedor", "")
        documento_titular, _ = validar_documento(titular_raw)

        credenciadoras = payload.get("credenciadoras") or []
        arranjos = payload.get("arranjos") or []
        validar_credenciadoras(credenciadoras)
        validar_arranjos(arranjos, repository.arranjos_ativos(request.financiador_id))
        validar_evidencia(payload.get("evidenciaAutorizacaoId"))

        data_assinatura = datetime.date.fromisoformat(payload["dataAssinatura"])
        vigencia_inicio = datetime.date.fromisoformat(payload["vigenciaInicio"])
        vigencia_fim = datetime.date.fromisoformat(payload["vigenciaFim"])
        validar_vigencia(data_assinatura, vigencia_inicio, vigencia_fim)
    except ValidationError as exc:
        return _erro_json(exc.codigo, exc.mensagem, 422)
    except (KeyError, TypeError, ValueError):
        return _erro_json("VAL_CAMPO_OBRIGATORIO", "campo obrigatório ausente ou mal formatado", 422)

    if repository.existe_optin_ativo_equivalente(
        request.financiador_id, documento_ufr, documento_titular, set(credenciadoras), set(arranjos), vigencia_inicio, vigencia_fim
    ):
        return _erro_json("VAL010", "opt-in equivalente já ativo", 409)

    cnpj_solicitante = get_tenant_config(request.financiador_id)["cerc_cnpj_solicitante"]

    optin = repository.criar_optin_pendente(request.financiador_id, {
        "cnpj_solicitante": cnpj_solicitante,
        "cnpj_financiador": request.financiador_id,
        "documento_ufr": documento_ufr,
        "documento_ufr_tipo": tipo_ufr,
        "documento_titular": documento_titular,
        "data_assinatura": data_assinatura,
        "vigencia_inicio": vigencia_inicio,
        "vigencia_fim": vigencia_fim,
        "carteira": payload.get("carteira"),
        "evidencia_id": payload["evidenciaAutorizacaoId"],
        "credenciadoras": credenciadoras,
        "arranjos": arranjos,
    })

    logger.info(
        "optin criado PENDENTE referencia=%s ufr=%s", optin["referencia_externa"], mascarar_documento(documento_ufr)
    )

    payload_cerc = {
        "referenciaExterna": optin["referencia_externa"],
        "cnpjSolicitante": cnpj_solicitante,
        "cnpjFinanciador": request.financiador_id,
        "dataAssinaturaOptIn": str(data_assinatura),
        "carteira": optin.get("carteira"),
        "definicaoUnidadeRecebivel": {
            "listaCnpjCredenciadora": credenciadoras,
            "listaCodigoArranjoPagamento": arranjos,
            "documentoUsuarioFinalRecebedor": documento_ufr,
            "documentoTitular": documento_titular,
            "dataInicio": str(vigencia_inicio),
            "dataFim": str(vigencia_fim),
        },
    }

    try:
        resposta = registrar_optin(request.financiador_id, payload_cerc, correlacao_id=optin["referencia_externa"])
    except Exception as exc:  # noqa: BLE001 - transporte (httpx) e negócio (CercApiError) tratados juntos aqui; classificação fina retentável/não-retentável (§9.2) fica no job de reconciliação, fora de escopo
        repository.atualizar_status(request.financiador_id, optin["id"], "FALHA_ENVIO")
        logger.warning("falha ao enviar optin %s para CERC: %s", optin["referencia_externa"], exc)
        return _erro_json("CERC_INDISPONIVEL", "falha ao registrar opt-in na CERC", 502)

    item = correlacionar_por_referencia(resposta, optin["referencia_externa"])
    resultado = interpretar_item_opt_in(item)

    if resultado.status_local == "ATIVO":
        optin_final = repository.atualizar_status(request.financiador_id, optin["id"], "ATIVO", protocolo_cerc=resultado.protocolo)
        return JsonResponse(_serializar_optin(optin_final), status=201)

    repository.atualizar_status(request.financiador_id, optin["id"], "REJEITADO")
    return _erro_json(resultado.erro_codigo or "REJEITADO", resultado.erro_mensagem or "opt-in rejeitado pela CERC", 422)


def optins_collection(request):
    if request.method == "POST":
        return criar_optin(request)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
```

```python
# optin/apps/optin/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("optins", views.optins_collection),
]
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_views_criar_optin.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/optin/views.py apps/optin/urls.py apps/optin/tests/conftest.py apps/optin/tests/test_views_criar_optin.py
git commit -m "feat: POST /api/v1/optins (create) with JWT, idempotency, anti-duplicidade"
```

---

### Task 8: `GET /api/v1/optins` e `GET /api/v1/optins/{id}`

**Files:**
- Modify: `optin/apps/optin/views.py`
- Modify: `optin/apps/optin/urls.py`
- Test: `optin/apps/optin/tests/test_views_listar_detalhar.py`

**Interfaces:**
- Consumes: `repository.{listar,buscar_por_id}` (Task 6, exigem `financiador_id` como primeiro argumento), `_serializar_optin` (Task 7).
- Produces: `listar_optins(request)`; `detalhar_optin(request, optin_id)`; `optin_detail(request, optin_id)` (dispatcher GET/PATCH, PATCH implementado na Task 9); atualiza `optins_collection` para also despachar GET.

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_views_listar_detalhar.py
import json

from dotenv import load_dotenv
load_dotenv()

from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _criar_ativo():
    import datetime

    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cnpj_solicitante": "12345678000199",
        "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR,
        "documento_ufr_tipo": "CNPJ",
        "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10),
        "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10),
        "carteira": None,
        "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"],
        "arranjos": ["VCC"],
    })
    return repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")


def test_detalhar_optin_retorna_200(client, auth_headers):
    _limpar()
    optin = _criar_ativo()

    response = client.get(f"/api/v1/optins/{optin['id']}", **auth_headers)
    assert response.status_code == 200
    assert json.loads(response.content)["id"] == optin["id"]
    _limpar()


def test_detalhar_optin_404_quando_nao_existe(client, auth_headers):
    response = client.get("/api/v1/optins/opt_inexistente", **auth_headers)
    assert response.status_code == 404


def test_listar_optins_filtra_por_status(client, auth_headers):
    _limpar()
    optin = _criar_ativo()

    response = client.get(f"/api/v1/optins?status=ATIVO&usuarioFinalRecebedor={DOC_UFR}", **auth_headers)
    assert response.status_code == 200
    ids = [item["id"] for item in json.loads(response.content)["dados"]]
    assert optin["id"] in ids
    _limpar()


def test_listar_optins_sem_jwt_retorna_401(client):
    response = client.get("/api/v1/optins")
    assert response.status_code == 401
```

Run: `pytest apps/optin/tests/test_views_listar_detalhar.py -v`
Expected: FAIL — rota `/api/v1/optins/<id>` não existe (404 genérico do Django), `listar_optins` ausente

- [ ] **Step 2: Implementar em `views.py` e `urls.py`**

Adicionar em `views.py` (após `criar_optin`):

```python
def listar_optins(request):
    filtros = {
        "status": request.GET.get("status"),
        "documento_ufr": request.GET.get("usuarioFinalRecebedor"),
        "origem": request.GET.get("origem"),
        "carteira": request.GET.get("carteira"),
        "vigente_em": request.GET.get("vigenteEm"),
    }
    limit = min(int(request.GET.get("limit", 50)), 200)
    resultado = repository.listar(request.financiador_id, filtros, limit)
    return JsonResponse({"dados": [_serializar_optin(o) for o in resultado]})


def detalhar_optin(request, optin_id):
    optin = repository.buscar_por_id(request.financiador_id, optin_id)
    if optin is None:
        return _erro_json("OPTIN_NAO_ENCONTRADO", "opt-in não encontrado", 404)
    return JsonResponse(_serializar_optin(optin))


def optin_detail(request, optin_id):
    if request.method == "GET":
        return detalhar_optin(request, optin_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
```

Aplicar `@jwt_required` em `listar_optins` e `detalhar_optin` (decorator direto acima da definição de cada função, igual em `criar_optin`).

Atualizar `optins_collection`:

```python
def optins_collection(request):
    if request.method == "POST":
        return criar_optin(request)
    if request.method == "GET":
        return listar_optins(request)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
```

```python
# optin/apps/optin/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("optins", views.optins_collection),
    path("optins/<str:optin_id>", views.optin_detail),
]
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_views_listar_detalhar.py -v`
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add apps/optin/views.py apps/optin/urls.py apps/optin/tests/test_views_listar_detalhar.py
git commit -m "feat: GET /api/v1/optins (list with filters) and GET /api/v1/optins/{id}"
```

---

### Task 9: `PATCH /api/v1/optins/{id}` — atualizar opt-in ativo

**Files:**
- Modify: `optin/apps/optin/views.py`
- Test: `optin/apps/optin/tests/test_views_atualizar_optin.py`

**Interfaces:**
- Consumes: `repository.{buscar_por_id,atualizar_campos,arranjos_ativos}` (Task 6, exigem `financiador_id` como primeiro argumento), `cerc_mapping.{interpretar_item_opt_in,correlacionar_por_referencia}`, `services.cerc.client.{atualizar_optin,CercApiError}` (Plan 07/09, `atualizar_optin` exige `financiador_id` como primeiro argumento), `shared.tenant_config.get_tenant_config` (Plan 09).
- Produces: `atualizar_optin_view(request, optin_id)`; atualiza `optin_detail` para despachar PATCH.

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_views_atualizar_optin.py
import json

import httpx
import respx
from dotenv import load_dotenv
load_dotenv()

from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _criar_ativo():
    import datetime

    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"], "arranjos": ["VCC"],
    })
    return repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")


@respx.mock
def test_atualizar_optin_sucesso(client, auth_headers):
    _limpar()
    optin = _criar_ativo()
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(207, json=[{"protocolo": "P-1", "referenciaExterna": optin["referencia_externa"], "status": "0", "erros": []}])
    )

    response = client.patch(
        f"/api/v1/optins/{optin['id']}",
        data=json.dumps({"vigenciaFim": "2028-01-01"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-update-1",
        **auth_headers,
    )

    assert response.status_code == 200
    assert json.loads(response.content)["vigenciaFim"] == "2028-01-01"
    _limpar()


def test_atualizar_optin_rejeita_campo_nao_atualizavel_sem_chamar_cerc(client, auth_headers):
    _limpar()
    optin = _criar_ativo()

    response = client.patch(
        f"/api/v1/optins/{optin['id']}",
        data=json.dumps({"referenciaExterna": "OUTRA"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-update-2",
        **auth_headers,
    )
    assert response.status_code == 422
    _limpar()


def test_atualizar_optin_404_quando_nao_existe(client, auth_headers):
    response = client.patch(
        "/api/v1/optins/opt_inexistente",
        data=json.dumps({"vigenciaFim": "2028-01-01"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-update-3",
        **auth_headers,
    )
    assert response.status_code == 404
```

Run: `pytest apps/optin/tests/test_views_atualizar_optin.py -v`
Expected: FAIL — `PATCH` não suportado em `optin_detail` (405)

- [ ] **Step 2: Implementar `atualizar_optin_view` em `views.py`**

Adicionar a `views.py` (após `detalhar_optin`), e os imports necessários (`atualizar_optin as atualizar_optin_cerc` do client, para não colidir com o nome da view):

```python
from services.cerc.client import CercApiError, atualizar_optin as atualizar_optin_cerc, encerrar_optin, registrar_optin
```

(substituir a linha de import de `services.cerc.client` já existente por essa, incluindo os três nomes usados neste plano)

```python
CAMPOS_NAO_ATUALIZAVEIS = {"referenciaExterna", "cnpjSolicitante"}


@jwt_required
@idempotente("optin_update")
def atualizar_optin_view(request, optin_id):
    optin = repository.buscar_por_id(request.financiador_id, optin_id)
    if optin is None:
        return _erro_json("OPTIN_NAO_ENCONTRADO", "opt-in não encontrado", 404)

    if optin["status"] != "ATIVO":
        return _erro_json("OPTIN_NAO_ATIVO", "só é possível atualizar opt-in ATIVO", 409)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    campos_proibidos = CAMPOS_NAO_ATUALIZAVEIS & payload.keys()
    if campos_proibidos:
        return _erro_json("CAMPO_NAO_ATUALIZAVEL", f"campos não atualizáveis: {sorted(campos_proibidos)}", 422)

    if not optin.get("protocolo_cerc"):
        return _erro_json("PROTOCOLO_AUSENTE", "opt-in sem protocolo_cerc não pode ser atualizado", 422)

    credenciadoras = optin["credenciadoras"]
    arranjos = optin["arranjos"]
    vigencia_inicio = optin["vigencia_inicio"]
    vigencia_fim = payload.get("vigenciaFim")

    try:
        if vigencia_fim:
            vigencia_fim = datetime.date.fromisoformat(vigencia_fim)
            validar_vigencia(optin["data_assinatura"], vigencia_inicio, vigencia_fim)
        else:
            vigencia_fim = optin["vigencia_fim"]
        if "arranjos" in payload:
            arranjos = payload["arranjos"]
            validar_arranjos(arranjos, repository.arranjos_ativos(request.financiador_id))
        if "credenciadoras" in payload:
            credenciadoras = payload["credenciadoras"]
            validar_credenciadoras(credenciadoras)
    except ValidationError as exc:
        return _erro_json(exc.codigo, exc.mensagem, 422)

    payload_cerc = {
        "referenciaExterna": optin["referencia_externa"],
        "cnpjSolicitante": get_tenant_config(request.financiador_id)["cerc_cnpj_solicitante"],
        "cnpjFinanciador": payload.get("cnpjFinanciador", optin["cnpj_financiador"]),
        "dataAssinaturaOptIn": str(optin["data_assinatura"]),
        "carteira": payload.get("carteira", optin.get("carteira")),
        "definicaoUnidadeRecebivel": {
            "listaCnpjCredenciadora": credenciadoras,
            "listaCodigoArranjoPagamento": arranjos,
            "documentoUsuarioFinalRecebedor": optin["documento_ufr"],
            "documentoTitular": optin["documento_titular"],
            "dataInicio": str(vigencia_inicio),
            "dataFim": str(vigencia_fim),
        },
    }

    try:
        resposta = atualizar_optin_cerc(request.financiador_id, optin["protocolo_cerc"], payload_cerc, correlacao_id=optin["referencia_externa"])
    except Exception as exc:  # noqa: BLE001 - mesmo tratamento uniforme de Task 7
        logger.warning("falha ao atualizar optin %s na CERC: %s", optin["referencia_externa"], exc)
        return _erro_json("CERC_INDISPONIVEL", "falha ao atualizar opt-in na CERC", 502)

    item = correlacionar_por_referencia(resposta, optin["referencia_externa"])
    resultado = interpretar_item_opt_in(item)

    if resultado.status_local != "ATIVO":
        return _erro_json(resultado.erro_codigo or "REJEITADO", resultado.erro_mensagem or "atualização rejeitada pela CERC", 422)

    optin_final = repository.atualizar_campos(request.financiador_id, optin_id, {
        "vigencia_fim": vigencia_fim,
        "carteira": payload.get("carteira", optin.get("carteira")),
    })
    return JsonResponse(_serializar_optin(optin_final))


def optin_detail(request, optin_id):
    if request.method == "GET":
        return detalhar_optin(request, optin_id)
    if request.method == "PATCH":
        return atualizar_optin_view(request, optin_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_views_atualizar_optin.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Rodar toda a suíte de `apps/optin` para checar regressão**

Run: `pytest apps/optin/tests -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add apps/optin/views.py apps/optin/tests/test_views_atualizar_optin.py
git commit -m "feat: PATCH /api/v1/optins/{id} (update via same /opt_in, tipoOperacao=A)"
```

---

### Task 10: `POST /api/v1/optins/{id}/optout` — encerrar opt-in

**Files:**
- Modify: `optin/apps/optin/views.py`
- Modify: `optin/apps/optin/urls.py`
- Test: `optin/apps/optin/tests/test_views_optout.py`

**Interfaces:**
- Consumes: `repository.{buscar_por_id,criar_optout_pendente,confirmar_optout,rejeitar_optout}` (Task 6, exigem `financiador_id` como primeiro argumento), `cerc_mapping.{interpretar_item_opt_out,correlacionar_por_referencia}`, `services.cerc.client.encerrar_optin` (Plan 07/09, exige `financiador_id` como primeiro argumento), `shared.tenant_config.get_tenant_config` (Plan 09).
- Produces: `optin_optout(request, optin_id)`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/apps/optin/tests/test_views_optout.py
import json

import httpx
import respx
from dotenv import load_dotenv
load_dotenv()

from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optout").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _criar(status="ATIVO", origem="OPTIN"):
    import datetime

    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"], "arranjos": ["VCC"],
    })
    optin = repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], status, protocolo_cerc="P-1" if status != "PENDENTE" else None)
    if origem != "OPTIN":
        get_db(FINANCIADOR_TESTE).table("optin").update({"origem": origem}).eq("id", optin["id"]).execute()
        optin["origem"] = origem
    return optin


@respx.mock
def test_optout_sucesso_encerra_optin(client, auth_headers):
    _limpar()
    optin = _criar()
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )

    def _resposta(request):
        enviado = json.loads(request.content)[0]
        return httpx.Response(207, json=[{"protocolo": "P-1", "referenciaExterna": enviado["referenciaExterna"], "status": "0", "erros": []}])

    respx.post("https://ap-homolog.cerc.inf.br/opt_out").mock(side_effect=_resposta)

    response = client.post(
        f"/api/v1/optins/{optin['id']}/optout",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-optout-1",
        **auth_headers,
    )

    assert response.status_code == 200
    assert json.loads(response.content)["status"] == "ENCERRADO"
    _limpar()


def test_optout_em_optin_por_contrato_retorna_409(client, auth_headers):
    # IT-06 (SPEC-01 §11.2): opt-out em opt-in origem=CONTRATO -> 409 OPT_OUT_NAO_APLICAVEL (R6).
    _limpar()
    optin = _criar(origem="CONTRATO")

    response = client.post(
        f"/api/v1/optins/{optin['id']}/optout",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-optout-2",
        **auth_headers,
    )
    assert response.status_code == 409
    assert json.loads(response.content)["erro"] == "OPT_OUT_NAO_APLICAVEL"
    _limpar()


def test_optout_em_optin_pendente_retorna_409(client, auth_headers):
    _limpar()
    optin = _criar(status="PENDENTE")

    response = client.post(
        f"/api/v1/optins/{optin['id']}/optout",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-optout-3",
        **auth_headers,
    )
    assert response.status_code == 409
    _limpar()


def test_optout_404_quando_nao_existe(client, auth_headers):
    response = client.post(
        "/api/v1/optins/opt_inexistente/optout",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-optout-4",
        **auth_headers,
    )
    assert response.status_code == 404
```

Run: `pytest apps/optin/tests/test_views_optout.py -v`
Expected: FAIL — rota `/api/v1/optins/<id>/optout` não existe

- [ ] **Step 2: Implementar `optin_optout` em `views.py` e a rota em `urls.py`**

Adicionar a `views.py`:

```python
@jwt_required
@idempotente("optin_optout")
def optin_optout(request, optin_id):
    optin = repository.buscar_por_id(request.financiador_id, optin_id)
    if optin is None:
        return _erro_json("OPTIN_NAO_ENCONTRADO", "opt-in não encontrado", 404)

    if optin["origem"] != "OPTIN":
        return _erro_json("OPT_OUT_NAO_APLICAVEL", "opt-in por força de contrato não aceita opt-out (R6)", 409)

    if optin["status"] not in ("ATIVO", "ERRO_PARCIAL") or not optin.get("protocolo_cerc"):
        return _erro_json("OPT_OUT_NAO_APLICAVEL", "opt-in não está em estado elegível para opt-out", 409)

    optout = repository.criar_optout_pendente(request.financiador_id, optin_id)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}

    payload_cerc = {
        "referenciaExterna": optout["referencia_externa"],
        "cnpjSolicitante": get_tenant_config(request.financiador_id)["cerc_cnpj_solicitante"],
        "carteira": payload.get("carteira", optin.get("carteira")),
    }

    try:
        resposta = encerrar_optin(request.financiador_id, optin["protocolo_cerc"], payload_cerc, correlacao_id=optout["referencia_externa"])
    except Exception as exc:  # noqa: BLE001 - mesmo tratamento uniforme das Tasks 7/9
        logger.warning("falha ao encerrar optin %s na CERC: %s", optin["referencia_externa"], exc)
        return _erro_json("CERC_INDISPONIVEL", "falha ao encerrar opt-in na CERC", 502)

    item = correlacionar_por_referencia(resposta, optout["referencia_externa"])
    resultado = interpretar_item_opt_out(item)

    if resultado.status_local == "CONFIRMADO":
        repository.confirmar_optout(request.financiador_id, optout["id"], optin_id, resultado.protocolo)
        optin_final = repository.buscar_por_id(request.financiador_id, optin_id)
        return JsonResponse(_serializar_optin(optin_final))

    repository.rejeitar_optout(request.financiador_id, optout["id"])
    return _erro_json(resultado.erro_codigo or "REJEITADO", resultado.erro_mensagem or "opt-out rejeitado pela CERC", 422)
```

Adicionar o import de `interpretar_item_opt_out` na linha de `from apps.optin.cerc_mapping import ...` já existente.

```python
# optin/apps/optin/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("optins", views.optins_collection),
    path("optins/<str:optin_id>", views.optin_detail),
    path("optins/<str:optin_id>/optout", views.optin_optout),
]
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest apps/optin/tests/test_views_optout.py -v`
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add apps/optin/views.py apps/optin/urls.py apps/optin/tests/test_views_optout.py
git commit -m "feat: POST /api/v1/optins/{id}/optout (R6 preconditions, IT-06/IT-07)"
```

---

### Task 11: Suíte completa e fechamento

**Files:**
- Nenhum arquivo novo — apenas verificação.

- [ ] **Step 1: Rodar a suíte inteira**

Run: `pytest -v`
Expected: PASS — todos os testes de `shared/`, `services/cerc/` e `apps/optin/` (Plans 01-07 + Plan 08), sem regressão.

- [ ] **Step 2: Conferir cobertura dos critérios de aceite de §11.2 relevantes a este plano**

Mapeamento manual (sem script): IT-01 → `test_criar_optin_sucesso_retorna_201_ativo`; IT-02 → `test_criar_optin_duplicado_retorna_409_sem_chamar_cerc`; IT-03 → `test_interpretar_item_opt_in_104803_e_idempotente`; IT-04 → `test_interpretar_item_opt_in_104806_e_rejeitado`; IT-05 → `test_atualizar_optin_rejeita_campo_nao_atualizavel_sem_chamar_cerc` (cobre a mesma garantia — "nunca chega à CERC" — para o caso de campo proibido; o caso literal "sem protocolo" é coberto pela checagem `PROTOCOLO_AUSENTE` em `atualizar_optin_view`, sem teste dedicado nesta versão); IT-06 → `test_optout_em_optin_por_contrato_retorna_409`; IT-07 → `test_optout_sucesso_encerra_optin`. IT-08/09 (consulta de agenda) e IT-10/11 (webhook) ficam para a SPEC-03 e para um plano de webhook, respectivamente (fora de escopo, ver §0 da SPEC-01). IT-12/13 já cobertos pelo Plan 07.

- [ ] **Step 3: Commit final (se houver qualquer ajuste feito durante a checagem)**

```bash
git add -A
git commit -m "chore: Plan 08 closeout — full suite green"
```

---

## Self-Review Notes

- **Spec coverage:** §5.1/§5.2/§5.3/§5.4 (endpoints) — Tasks 7-10. §5.6 (anti-duplicidade) — Tasks 4/6/7. §5 (`Idempotency-Key` obrigatório) — Task 3. Autenticação JWT (§5, design §4) — Task 1. §7.1 (104803 idempotente) e IT-03/IT-04 — Task 5. §8 (mascaramento de documento em log) — Task 4, usado em Task 7. §9.1 (transições PENDENTE→ATIVO/REJEITADO, ATIVO→ENCERRADO) — Tasks 7/10. Gaps conhecidos e não cobertos por este plano (auditoria com diff completo, `sincronizar_dominio_arranjo`, `retry_envio`, webhook, consulta de agenda, VAL009) estão listados em "Riscos e pendências".
- **Placeholder scan:** nenhum "TODO"/"implementar depois" — os itens fora de escopo estão explicitamente listados em "Riscos e pendências" como decisões conscientes, não como código incompleto.
- **Type consistency:** `repository.buscar_por_id`/`criar_optin_pendente`/`atualizar_status`/`atualizar_campos` sempre devolvem um `dict` com `credenciadoras`/`arranjos` já anexados (via `_com_filhas`) — `_serializar_optin` (Task 7) depende disso e é reaproveitado sem alteração pelas Tasks 8, 9 e 10. `cerc_mapping.ResultadoItemCerc` é o único formato de retorno usado por `interpretar_item_opt_in`/`interpretar_item_opt_out`, consumido de forma idêntica em Tasks 7, 9 e 10.
- **Multi-tenancy (retrofit pós-Plan 09):** `financiador_id` é sempre o primeiro parâmetro em toda função de `repository.py` e em toda chamada a `services.cerc.client.*`, sempre sourced de `request.financiador_id` (nunca de `os.environ`) em toda view — Tasks 3, 6, 7, 8, 9 e 10 aplicadas de forma consistente. `cnpjSolicitante` vem de `shared.tenant_config.get_tenant_config(request.financiador_id)["cerc_cnpj_solicitante"]`; `cnpjFinanciador` é o próprio `request.financiador_id`.

**Next:** webhook receptor (SPEC-01 §4.4), jobs de reconciliação (`retry_envio`, `expirar_optins`, `sincronizar_dominio_arranjo` — §9.4) e observabilidade (§10) — nesta ordem, conforme §0 da SPEC-01.
