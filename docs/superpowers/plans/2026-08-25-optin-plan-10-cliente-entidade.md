# Entidade Cliente + CORS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar CORS ao backend e criar a entidade `cliente` (mínima: documento/nome/email/telefone) com endpoints `POST/GET /api/v1/clientes` e `GET /api/v1/clientes/{id}`, como pré-requisito independente para a integração com o front.

**Architecture:** Novo app Django `apps/cliente/`, no mesmo padrão sem-ORM de `apps/optin/` (repository.py fala com `shared.cloudsql_client.get_db`, views.py monta JSON à mão). CORS via `django-cors-headers`, configurado só para as origens explicitamente permitidas.

**Tech Stack:** Django 4.2, `django-cors-headers`, pytest + pytest-django contra o Cloud SQL real do tenant dev (`12345678000199`).

**Spec:** `docs/superpowers/specs/2026-08-25-frontend-integration-design.md` (§2, §4)

## Global Constraints

- Sem Django ORM — todo acesso a dados passa por `shared.cloudsql_client.get_db(financiador_id)` (design §2/§3 do repo).
- Toda função de repository recebe `financiador_id` como primeiro parâmetro.
- IDs de entidade usam prefixo + ULID (`f"cli_{ULID()}"`, mesmo padrão de `f"opt_{ULID()}"` em `apps/optin/repository.py`).
- Erros HTTP sempre `{"erro": "<codigo>", "mensagem": "<texto>"}`, via um helper `_erro_json` local ao módulo de views (mesmo padrão de `apps/optin/views.py`).
- Toda rota exige JWT (`@jwt_required` de `shared/jwt_auth.py`) exceto `health`.
- Testes rodam contra o Cloud SQL real do tenant dev (`FINANCIADOR_TESTE = "12345678000199"`) — todo teste que grava dados limpa em `try/finally`.
- Sem ferramenta de migração — schema novo é aplicado manualmente no Cloud SQL de dev (Task 2, Step 2).

---

### Task 1: CORS no backend

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`
- Create: `.env` (local, gitignored) — adicionar `CORS_ALLOWED_ORIGINS`
- Modify: `.env.example`
- Create: `config/tests/__init__.py`
- Create: `config/tests/test_cors.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (task independente).
- Produces: nenhuma função nova — só configuração. Nenhuma task depende disto para código, mas o Plan 12 (front) depende deste CORS estar ativo para as chamadas HTTP funcionarem no navegador.

- [ ] **Step 1: Adicionar `django-cors-headers` a `requirements.txt`**

Edite `requirements.txt`, adicionando a linha (mantendo a ordem alfabética não é exigida pelo arquivo atual, então adicione perto de `djangorestframework`):

```
django-cors-headers
```

- [ ] **Step 2: Instalar a dependência**

Run: `pip install django-cors-headers`
Expected: instalação sem erro, `django-cors-headers` aparece em `pip freeze`.

- [ ] **Step 3: Configurar `CorsMiddleware` e `CORS_ALLOWED_ORIGINS` em `config/settings.py`**

Edite `config/settings.py`. Adicione `"corsheaders"` a `INSTALLED_APPS` (antes de `"apps.optin"`):

```python
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "corsheaders",
    "apps.optin",
]
```

Adicione o middleware **antes** de `CommonMiddleware`:

```python
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]
```

Adicione, depois do bloco de `MIDDLEWARE`/`ROOT_URLCONF` (não precisa ser nesta posição exata, mas mantenha perto de outras configs de origem/host):

```python
# CORS — origens da SPA que consome esta API (ap-front). Config direta via
# env var (não é segredo, não passa por shared/secrets.py — mesmo padrão já
# usado para ALLOWED_HOSTS acima).
from corsheaders.defaults import default_headers

CORS_ALLOWED_ORIGINS = [o for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o]
CORS_ALLOW_HEADERS = list(default_headers) + ["idempotency-key"]
```

`CORS_ALLOW_HEADERS` precisa incluir `idempotency-key` explicitamente — é um header customizado (não está na lista default do `django-cors-headers`) e o contrato de `POST`/`PATCH` de `apps/optin` já exige `Idempotency-Key` em toda mutação (`apps/optin/idempotency.py`). Sem isso, o preflight `OPTIONS` do navegador rejeitaria a requisição real antes mesmo de chegar na view.

- [ ] **Step 4: Configurar a env var localmente e documentar no `.env.example`**

No `.env` local (arquivo já existe, gitignored — não crie um novo, edite o existente), adicione:

```
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

Em `.env.example`, adicione (depois do bloco de CERC, no fim do arquivo):

```
# CORS — origens da SPA (ap-front) autorizadas a chamar esta API.
# Dev local: http://localhost:5173 (porta padrão do Vite).
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

- [ ] **Step 5: Escrever o teste de CORS**

Crie `config/tests/__init__.py` (vazio).

Crie `config/tests/test_cors.py`:

```python
def test_cors_permite_origem_configurada(client):
    response = client.get("/api/v1/health", HTTP_ORIGIN="http://localhost:5173")
    assert response["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_cors_nao_libera_origem_nao_configurada(client):
    response = client.get("/api/v1/health", HTTP_ORIGIN="http://nao-autorizado.example.com")
    assert "Access-Control-Allow-Origin" not in response
```

- [ ] **Step 6: Rodar o teste**

Run: `python -m pytest config/tests/test_cors.py -v`
Expected: `2 passed`. Se falhar com `KeyError`/origem ausente, confirme que `CORS_ALLOWED_ORIGINS=http://localhost:5173` está mesmo no `.env` local (Step 4) — `settings.py` só lê essa env var uma vez, no import (`load_dotenv()` já roda no topo do arquivo).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt config/settings.py .env.example config/tests/__init__.py config/tests/test_cors.py
git commit -m "feat: CORS para a SPA do front (ap-front)"
```

---

### Task 2: Schema da tabela `cliente`

**Files:**
- Create: `docker/initdb/03-cliente.sql`

**Interfaces:**
- Consumes: nada.
- Produces: tabela `cliente(id, documento, documento_tipo, nome, email, telefone, criado_em)` no banco do tenant `12345678000199`, usada pelas Tasks 3 e 4.

- [ ] **Step 1: Escrever o DDL**

Crie `docker/initdb/03-cliente.sql`:

```sql
CREATE TABLE cliente (
  id             TEXT PRIMARY KEY,
  documento      TEXT NOT NULL,
  documento_tipo TEXT NOT NULL,
  nome           TEXT NOT NULL,
  email          TEXT,
  telefone       TEXT,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (documento)
);
```

- [ ] **Step 2: Aplicar o DDL no Cloud SQL de dev**

Não há ferramenta de migração neste projeto — `docker/initdb/*.sql` só roda automaticamente num banco novo. O Cloud SQL do tenant dev já existe, então este DDL precisa ser executado manualmente, uma vez, contra ele. Rode (a partir da raiz do projeto, com o `.env` carregado):

```bash
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
import sqlalchemy
from shared.cloudsql_client import get_db
ddl = open('docker/initdb/03-cliente.sql').read()
with get_db('12345678000199')._engine.begin() as conn:
    conn.execute(sqlalchemy.text(ddl))
print('tabela cliente criada')
"
```

Expected: imprime `tabela cliente criada` sem erro. Se der erro `relation "cliente" already exists`, a tabela já existe (rodou antes) — pode seguir.

- [ ] **Step 3: Commit**

```bash
git add docker/initdb/03-cliente.sql
git commit -m "feat: schema da tabela cliente"
```

---

### Task 3: `apps/cliente/repository.py` (TDD)

**Files:**
- Create: `apps/cliente/__init__.py`
- Create: `apps/cliente/repository.py`
- Create: `apps/cliente/tests/__init__.py`
- Create: `apps/cliente/tests/conftest.py`
- Create: `apps/cliente/tests/test_repository.py`

**Interfaces:**
- Consumes: `shared.cloudsql_client.get_db(financiador_id)` (já existe).
- Produces (usado por Task 4 e pelo Plan 11):
  - `criar(financiador_id: str, dados: dict) -> dict` — `dados` = `{"documento", "documento_tipo", "nome", "email", "telefone"}` (`email`/`telefone` podem ser `None`). Retorna a linha inserida (dict com chaves em snake_case: `id`, `documento`, `documento_tipo`, `nome`, `email`, `telefone`, `criado_em`).
  - `buscar_por_documento(financiador_id: str, documento: str) -> dict | None`
  - `buscar_por_id(financiador_id: str, cliente_id: str) -> dict | None`
  - `listar(financiador_id: str, filtros: dict, limit: int) -> list[dict]` — `filtros` aceita chave `"documento"` (opcional).

- [ ] **Step 1: Criar `apps/cliente/__init__.py` e `apps/cliente/tests/__init__.py` (vazios)**

- [ ] **Step 2: Copiar o conftest de autenticação para `apps/cliente/tests/`**

Não há conftest compartilhado no projeto (cada pasta de testes tem o seu, mesmo padrão de `apps/optin/tests/`). Crie `apps/cliente/tests/conftest.py` com o mesmo conteúdo de `apps/optin/tests/conftest.py`:

```python
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

- [ ] **Step 3: Escrever os testes de repository (falhando)**

Crie `apps/cliente/tests/test_repository.py`:

```python
from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOCUMENTO_TESTE = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    get_db(FINANCIADOR_TESTE).table("cliente").delete().eq("documento", DOCUMENTO_TESTE).execute()


def test_criar_grava_cliente():
    from apps.cliente import repository

    _limpar()
    try:
        cliente = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": "teste@example.com", "telefone": "11999999999",
        })
        assert cliente["nome"] == "Cliente Teste"
        assert cliente["documento"] == DOCUMENTO_TESTE
        assert cliente["id"].startswith("cli_")
    finally:
        _limpar()


def test_buscar_por_documento_retorna_none_quando_nao_existe():
    from apps.cliente import repository

    _limpar()
    assert repository.buscar_por_documento(FINANCIADOR_TESTE, DOCUMENTO_TESTE) is None


def test_buscar_por_documento_encontra_cliente_criado():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        encontrado = repository.buscar_por_documento(FINANCIADOR_TESTE, DOCUMENTO_TESTE)
        assert encontrado["id"] == criado["id"]
    finally:
        _limpar()


def test_buscar_por_id_retorna_none_quando_nao_existe():
    from apps.cliente import repository

    assert repository.buscar_por_id(FINANCIADOR_TESTE, "cli_inexistente") is None


def test_listar_filtra_por_documento():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        resultado = repository.listar(FINANCIADOR_TESTE, {"documento": DOCUMENTO_TESTE}, limit=50)
        assert any(c["id"] == criado["id"] for c in resultado)

        vazio = repository.listar(FINANCIADOR_TESTE, {"documento": "00000000000000"}, limit=50)
        assert vazio == []
    finally:
        _limpar()
```

- [ ] **Step 4: Rodar os testes e confirmar que falham**

Run: `python -m pytest apps/cliente/tests/test_repository.py -v`
Expected: `FAIL` — `ModuleNotFoundError: No module named 'apps.cliente.repository'` (ou `ImportError`) em todos os testes.

- [ ] **Step 5: Implementar `apps/cliente/repository.py`**

```python
"""Acesso a dados da entidade cliente — pré-requisito mínimo para gerir opt-ins
(design: docs/superpowers/specs/2026-08-25-frontend-integration-design.md §2)."""

from ulid import ULID

from shared.cloudsql_client import get_db


def criar(financiador_id: str, dados: dict) -> dict:
    cliente_id = f"cli_{ULID()}"
    inserted = get_db(financiador_id).table("cliente").insert({
        "id": cliente_id,
        "documento": dados["documento"],
        "documento_tipo": dados["documento_tipo"],
        "nome": dados["nome"],
        "email": dados.get("email"),
        "telefone": dados.get("telefone"),
    }).execute()
    return inserted.data[0]


def buscar_por_documento(financiador_id: str, documento: str):
    rows = get_db(financiador_id).table("cliente").select("*").eq("documento", documento).execute().data
    return rows[0] if rows else None


def buscar_por_id(financiador_id: str, cliente_id: str):
    rows = get_db(financiador_id).table("cliente").select("*").eq("id", cliente_id).execute().data
    return rows[0] if rows else None


def listar(financiador_id: str, filtros: dict, limit: int) -> list:
    query = get_db(financiador_id).table("cliente").select("*")
    if filtros.get("documento"):
        query = query.eq("documento", filtros["documento"])
    return query.order("criado_em", desc=True).limit(limit).execute().data
```

- [ ] **Step 6: Rodar os testes de novo e confirmar que passam**

Run: `python -m pytest apps/cliente/tests/test_repository.py -v`
Expected: `5 passed`.

- [ ] **Step 7: Commit**

```bash
git add apps/cliente/__init__.py apps/cliente/repository.py apps/cliente/tests/__init__.py apps/cliente/tests/conftest.py apps/cliente/tests/test_repository.py
git commit -m "feat: repository da entidade cliente"
```

---

### Task 4: `apps/cliente/views.py` + `urls.py` (TDD, HTTP)

**Files:**
- Create: `apps/cliente/views.py`
- Create: `apps/cliente/urls.py`
- Create: `apps/cliente/tests/test_views.py`
- Modify: `config/urls.py`

**Interfaces:**
- Consumes: `apps.cliente.repository.{criar, buscar_por_documento, buscar_por_id, listar}` (Task 3); `apps.optin.validation.{ValidationError, validar_documento}` (já existe); `shared.jwt_auth.jwt_required` (já existe).
- Produces (usado pelo Plan 11 e pelo front no Plan 12):
  - `POST /api/v1/clientes` — corpo `{"documento", "nome", "email"?, "telefone"?}` → 201 com cliente serializado, ou 422 (`VAL001`/`VAL002` documento inválido, `CLI001` nome ausente), ou 409 (`CLIENTE_JA_CADASTRADO`), ou 401 sem JWT.
  - `GET /api/v1/clientes?documento=&limit=` → `{"dados": [...]}`.
  - `GET /api/v1/clientes/{id}` → cliente serializado ou 404 (`CLIENTE_NAO_ENCONTRADO`).
  - Serialização: `{"id", "documento", "documentoTipo", "nome", "email", "telefone", "criadoEm"}`.

- [ ] **Step 1: Escrever os testes de view (falhando)**

Crie `apps/cliente/tests/test_views.py`:

```python
import json

from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOCUMENTO_TESTE = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"

CORPO_VALIDO = {
    "documento": DOCUMENTO_TESTE,
    "nome": "Cliente Teste",
    "email": "teste@example.com",
    "telefone": "11999999999",
}


def _limpar():
    get_db(FINANCIADOR_TESTE).table("cliente").delete().eq("documento", DOCUMENTO_TESTE).execute()


def test_criar_cliente_sucesso_retorna_201(client, auth_headers):
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 201
        body = json.loads(response.content)
        assert body["documento"] == DOCUMENTO_TESTE
        assert body["documentoTipo"] == "CNPJ"
        assert body["nome"] == "Cliente Teste"
        assert body["id"].startswith("cli_")
    finally:
        _limpar()


def test_criar_cliente_sem_jwt_retorna_401(client):
    response = client.post("/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json")
    assert response.status_code == 401


def test_criar_cliente_documento_invalido_retorna_422(client, auth_headers):
    corpo = {**CORPO_VALIDO, "documento": "11111111111111"}
    response = client.post(
        "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
    )
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL002"


def test_criar_cliente_sem_nome_retorna_422(client, auth_headers):
    corpo = {"documento": DOCUMENTO_TESTE, "email": None, "telefone": None}
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "CLI001"
    finally:
        _limpar()


def test_criar_cliente_duplicado_retorna_409(client, auth_headers):
    _limpar()
    try:
        client.post("/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers)
        response = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 409
        assert json.loads(response.content)["erro"] == "CLIENTE_JA_CADASTRADO"
    finally:
        _limpar()


def test_listar_clientes_filtra_por_documento(client, auth_headers):
    _limpar()
    try:
        criado_resp = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        criado = json.loads(criado_resp.content)

        response = client.get(f"/api/v1/clientes?documento={DOCUMENTO_TESTE}", **auth_headers)
        assert response.status_code == 200
        ids = [item["id"] for item in json.loads(response.content)["dados"]]
        assert criado["id"] in ids
    finally:
        _limpar()


def test_detalhar_cliente_retorna_200(client, auth_headers):
    _limpar()
    try:
        criado_resp = client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        )
        criado = json.loads(criado_resp.content)

        response = client.get(f"/api/v1/clientes/{criado['id']}", **auth_headers)
        assert response.status_code == 200
        assert json.loads(response.content)["id"] == criado["id"]
    finally:
        _limpar()


def test_detalhar_cliente_404_quando_nao_existe(client, auth_headers):
    response = client.get("/api/v1/clientes/cli_inexistente", **auth_headers)
    assert response.status_code == 404
    assert json.loads(response.content)["erro"] == "CLIENTE_NAO_ENCONTRADO"
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest apps/cliente/tests/test_views.py -v`
Expected: `FAIL` com `404` (rota `/api/v1/clientes` não existe ainda) em todos os testes.

- [ ] **Step 3: Implementar `apps/cliente/views.py`**

```python
import json

from django.http import JsonResponse

from apps.cliente import repository
from apps.optin.validation import ValidationError, validar_documento
from shared.jwt_auth import jwt_required


def _erro_json(codigo: str, mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"erro": codigo, "mensagem": mensagem}, status=status)


def _serializar_cliente(cliente: dict) -> dict:
    return {
        "id": cliente["id"],
        "documento": cliente["documento"],
        "documentoTipo": cliente["documento_tipo"],
        "nome": cliente["nome"],
        "email": cliente.get("email"),
        "telefone": cliente.get("telefone"),
        "criadoEm": cliente["criado_em"].isoformat() if hasattr(cliente["criado_em"], "isoformat") else cliente["criado_em"],
    }


@jwt_required
def criar_cliente(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    try:
        documento, tipo = validar_documento(payload.get("documento", ""))
    except ValidationError as exc:
        return _erro_json(exc.codigo, exc.mensagem, 422)

    nome = payload.get("nome")
    if not nome:
        return _erro_json("CLI001", "nome é obrigatório", 422)

    if repository.buscar_por_documento(request.financiador_id, documento):
        return _erro_json("CLIENTE_JA_CADASTRADO", "já existe cliente cadastrado com esse documento", 409)

    cliente = repository.criar(request.financiador_id, {
        "documento": documento,
        "documento_tipo": tipo,
        "nome": nome,
        "email": payload.get("email"),
        "telefone": payload.get("telefone"),
    })
    return JsonResponse(_serializar_cliente(cliente), status=201)


@jwt_required
def listar_clientes(request):
    filtros = {"documento": request.GET.get("documento")}
    limit = min(int(request.GET.get("limit", 50)), 200)
    resultado = repository.listar(request.financiador_id, filtros, limit)
    return JsonResponse({"dados": [_serializar_cliente(c) for c in resultado]})


@jwt_required
def detalhar_cliente(request, cliente_id):
    cliente = repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)
    return JsonResponse(_serializar_cliente(cliente))


def clientes_collection(request):
    if request.method == "POST":
        return criar_cliente(request)
    if request.method == "GET":
        return listar_clientes(request)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)


def cliente_detail(request, cliente_id):
    if request.method == "GET":
        return detalhar_cliente(request, cliente_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
```

- [ ] **Step 4: Criar `apps/cliente/urls.py`**

```python
from django.urls import path

from . import views

urlpatterns = [
    path("clientes", views.clientes_collection),
    path("clientes/<str:cliente_id>", views.cliente_detail),
]
```

- [ ] **Step 5: Registrar o app e as rotas**

Em `config/settings.py`, adicione `"apps.cliente"` a `INSTALLED_APPS` (depois de `"apps.optin"`):

```python
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "corsheaders",
    "apps.optin",
    "apps.cliente",
]
```

Em `config/urls.py`, adicione o include:

```python
from django.urls import path, include

urlpatterns = [
    path("api/v1/", include("apps.optin.urls")),
    path("api/v1/", include("apps.cliente.urls")),
]
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `python -m pytest apps/cliente/tests/test_views.py -v`
Expected: `8 passed`.

- [ ] **Step 7: Rodar a suíte inteira para garantir que nada quebrou**

Run: `python -m pytest`
Expected: todos os testes existentes de `apps/optin` continuam passando (nenhuma mudança neste plano toca `apps/optin`).

- [ ] **Step 8: Commit**

```bash
git add apps/cliente/views.py apps/cliente/urls.py apps/cliente/tests/test_views.py config/settings.py config/urls.py
git commit -m "feat: endpoints POST/GET /api/v1/clientes e GET /api/v1/clientes/{id}"
```
