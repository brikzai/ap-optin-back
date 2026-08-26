# Cliente — Cadastro Completo (status + atualização) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cliente` ganha as colunas `status` e `atualizado_em`; `POST /api/v1/clientes` passa a aceitar `status` opcional, e um novo `PATCH /api/v1/clientes/{id}` permite editar `nome`/`email`/`telefone`/`status` de um cliente existente.

**Architecture:** `apps.cliente` vira o cadastro único de cliente consumido pelo front inteiro (não só pelo fluxo de opt-in) — precisa suportar edição, que hoje não existe. `criar()` passa a gravar `status` (default `"pending"`); um novo `atualizar()` faz update parcial (só os campos presentes no payload) e sempre recarrega `atualizado_em`. A view de PATCH verifica existência do cliente antes de delegar ao repository (mesmo padrão que `atualizar_optin_view` já usa em `apps/optin/views.py`).

**Tech Stack:** Django (sem ORM), SQLAlchemy Core via `shared.cloudsql_client`, pytest contra o Cloud SQL real do tenant dev.

**Spec:** `docs/superpowers/specs/2026-08-26-cadastro-unificado-cliente-design.md` (§1, §2)

**Depends on:** `2026-08-25-optin-plan-10-cliente-entidade.md` (tabela `cliente` e `apps.cliente.repository` precisam existir).

## Global Constraints

- Sem Django ORM — acesso via `shared.cloudsql_client.get_db(financiador_id)`.
- Toda função de repository recebe `financiador_id` como primeiro parâmetro.
- Erros HTTP sempre `{"erro": "<codigo>", "mensagem": "<texto>"}`.
- Testes rodam contra o Cloud SQL real do tenant dev (`FINANCIADOR_TESTE = "12345678000199"`, `DOCUMENTO_TESTE = "22751826000125"`) — sempre limpar em `try/finally`.
- Sem ferramenta de migração — o `ALTER TABLE` deste plano é aplicado manualmente (Task 1).
- `status` só aceita `"active"`, `"inactive"` ou `"pending"` — qualquer outro valor é `422 CLI003`.
- `documento`/`documentoTipo`/`id` não são editáveis via `PATCH` — se vierem no payload, são ignorados silenciosamente (não é erro).

---

### Task 1: Schema — `cliente.status` e `cliente.atualizado_em`

**Files:**
- Modify: `docker/initdb/00-cliente.sql`

**Interfaces:**
- Consumes: tabela `cliente` (Plan 10).
- Produces: colunas `cliente.status TEXT NOT NULL DEFAULT 'pending'` e `cliente.atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()`, usadas pela Task 2.

- [ ] **Step 1: Atualizar o arquivo de schema (para bancos novos)**

Edite `docker/initdb/00-cliente.sql` — arquivo inteiro passa a ser:

```sql
CREATE TABLE cliente (
  id             TEXT PRIMARY KEY,
  documento      TEXT NOT NULL,
  documento_tipo TEXT NOT NULL,
  nome           TEXT NOT NULL,
  email          TEXT,
  telefone       TEXT,
  status         TEXT NOT NULL DEFAULT 'pending',
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (documento)
);
```

- [ ] **Step 2: Aplicar o `ALTER TABLE` no Cloud SQL de dev**

Diferente do `optin.cliente_id` do Plan 11 (FK `NOT NULL` sem `DEFAULT`, exigia tabela vazia), estas duas colunas têm `DEFAULT` — o Postgres preenche automaticamente linhas existentes, então não precisa checar se `cliente` está vazia antes.

```bash
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
import sqlalchemy
from shared.cloudsql_client import get_db
with get_db('12345678000199')._engine.begin() as conn:
    conn.execute(sqlalchemy.text(\"ALTER TABLE cliente ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'\"))
    conn.execute(sqlalchemy.text('ALTER TABLE cliente ADD COLUMN atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()'))
print('colunas status e atualizado_em adicionadas')
"
```

Expected: imprime `colunas status e atualizado_em adicionadas`. Se der erro `column "status" of relation "cliente" already exists` (ou o mesmo pra `atualizado_em`), já rodou antes — pode seguir.

- [ ] **Step 3: Commit**

```bash
git add docker/initdb/00-cliente.sql
git commit -m "feat: colunas cliente.status e cliente.atualizado_em"
```

---

### Task 2: `apps.cliente.repository` — status em `criar()`, novo `atualizar()`

**Files:**
- Modify: `apps/cliente/repository.py`
- Modify: `apps/cliente/tests/test_repository.py`

**Interfaces:**
- Consumes: colunas `cliente.status`/`cliente.atualizado_em` (Task 1).
- Produces: `repository.criar(financiador_id, dados)` — `dados["status"]` agora opcional (default `"pending"`); novo `repository.atualizar(financiador_id: str, cliente_id: str, dados: dict) -> dict` — update parcial (só grava as chaves presentes em `dados`), sempre atualiza `atualizado_em`. Usado pela Task 3.

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/cliente/tests/test_repository.py`, adicione ao final do arquivo:

```python
def test_criar_grava_status_default_pending():
    from apps.cliente import repository

    _limpar()
    try:
        cliente = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        assert cliente["status"] == "pending"
    finally:
        _limpar()


def test_criar_aceita_status_explicito():
    from apps.cliente import repository

    _limpar()
    try:
        cliente = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None, "status": "active",
        })
        assert cliente["status"] == "active"
    finally:
        _limpar()


def test_atualizar_altera_campos_e_marca_atualizado_em():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        atualizado = repository.atualizar(
            FINANCIADOR_TESTE, criado["id"], {"nome": "Nome Novo", "email": "novo@example.com"}
        )
        assert atualizado["nome"] == "Nome Novo"
        assert atualizado["email"] == "novo@example.com"
        assert atualizado["telefone"] is None
        assert atualizado["atualizado_em"] > criado["criado_em"]
    finally:
        _limpar()


def test_atualizar_so_grava_campos_presentes_no_dict():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": "original@example.com", "telefone": "11888888888",
        })
        atualizado = repository.atualizar(FINANCIADOR_TESTE, criado["id"], {"status": "active"})
        assert atualizado["status"] == "active"
        assert atualizado["email"] == "original@example.com"
        assert atualizado["telefone"] == "11888888888"
        assert atualizado["nome"] == "Cliente Teste"
    finally:
        _limpar()
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest apps/cliente/tests/test_repository.py -v`
Expected: as 4 novas funções `FAIL` — `test_criar_grava_status_default_pending` e `test_criar_aceita_status_explicito` com `KeyError: 'status'` (a coluna existe no banco mas `criar()` ainda não grava/lê); `test_atualizar_altera_campos_e_marca_atualizado_em` e `test_atualizar_so_grava_campos_presentes_no_dict` com `AttributeError: module 'apps.cliente.repository' has no attribute 'atualizar'`.

- [ ] **Step 3: Implementar em `apps/cliente/repository.py`**

Substitua o import do topo do arquivo:

```python
"""Acesso a dados da entidade cliente — pré-requisito mínimo para gerir opt-ins
(design: docs/superpowers/specs/2026-08-25-frontend-integration-design.md §2)."""

from django.utils import timezone
from ulid import ULID

from shared.cloudsql_client import get_db
```

Substitua `criar()`:

```python
def criar(financiador_id: str, dados: dict) -> dict:
    cliente_id = f"cli_{ULID()}"
    inserted = get_db(financiador_id).table("cliente").insert({
        "id": cliente_id,
        "documento": dados["documento"],
        "documento_tipo": dados["documento_tipo"],
        "nome": dados["nome"],
        "email": dados.get("email"),
        "telefone": dados.get("telefone"),
        "status": dados.get("status") or "pending",
    }).execute()
    return inserted.data[0]
```

Adicione `atualizar()` logo depois de `criar()`:

```python
def atualizar(financiador_id: str, cliente_id: str, dados: dict) -> dict:
    campos = {**dados, "atualizado_em": timezone.now()}
    resultado = get_db(financiador_id).table("cliente").update(campos).eq("id", cliente_id).execute()
    return resultado.data[0]
```

- [ ] **Step 4: Rodar de novo**

Run: `python -m pytest apps/cliente/tests/test_repository.py -v`
Expected: todos passam.

- [ ] **Step 5: Commit**

```bash
git add apps/cliente/repository.py apps/cliente/tests/test_repository.py
git commit -m "feat: repository.atualizar + status em repository.criar (apps.cliente)"
```

---

### Task 3: `apps.cliente.views` — `status` no `POST`, novo `PATCH /clientes/{id}`

**Files:**
- Modify: `apps/cliente/views.py`
- Modify: `apps/cliente/tests/test_views.py`

**Interfaces:**
- Consumes: `repository.atualizar` (Task 2).
- Produces: `PATCH /api/v1/clientes/{id}` — aceita `nome`/`email`/`telefone`/`status` (todos opcionais); `404 CLIENTE_NAO_ENCONTRADO` se o id não existir; `422 CLI003` se `status` for inválido. Resposta de cliente (`POST`/`GET`/`PATCH`) ganha `status`/`atualizadoEm`. Usado pelo Plan 14 (frontend).

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/cliente/tests/test_views.py`, adicione ao final do arquivo:

```python
def test_criar_cliente_status_invalido_retorna_422(client, auth_headers):
    corpo = {**CORPO_VALIDO, "status": "banana"}
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
        )
        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "CLI003"
    finally:
        _limpar()


def test_criar_cliente_grava_status_explicito(client, auth_headers):
    corpo = {**CORPO_VALIDO, "status": "active"}
    _limpar()
    try:
        response = client.post(
            "/api/v1/clientes", data=json.dumps(corpo), content_type="application/json", **auth_headers,
        )
        body = json.loads(response.content)
        assert body["status"] == "active"
        assert "atualizadoEm" in body
    finally:
        _limpar()


def test_atualizar_cliente_sucesso_altera_campos(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({"nome": "Nome Editado", "status": "active"}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["nome"] == "Nome Editado"
        assert body["status"] == "active"
        assert body["email"] == CORPO_VALIDO["email"]
    finally:
        _limpar()


def test_atualizar_cliente_ignora_documento_no_payload(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({"documento": "00000000000000", "nome": "Nome Editado"}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["documento"] == DOCUMENTO_TESTE
        assert body["nome"] == "Nome Editado"
    finally:
        _limpar()


def test_atualizar_cliente_404_quando_nao_existe(client, auth_headers):
    response = client.patch(
        "/api/v1/clientes/cli_inexistente",
        data=json.dumps({"nome": "Novo Nome"}),
        content_type="application/json",
        **auth_headers,
    )
    assert response.status_code == 404
    assert json.loads(response.content)["erro"] == "CLIENTE_NAO_ENCONTRADO"


def test_atualizar_cliente_status_invalido_retorna_422(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({"status": "banana"}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 422
        assert json.loads(response.content)["erro"] == "CLI003"
    finally:
        _limpar()


def test_atualizar_cliente_corpo_vazio_nao_muda_nada(client, auth_headers):
    _limpar()
    try:
        criado = json.loads(client.post(
            "/api/v1/clientes", data=json.dumps(CORPO_VALIDO), content_type="application/json", **auth_headers,
        ).content)

        response = client.patch(
            f"/api/v1/clientes/{criado['id']}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_headers,
        )
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["nome"] == CORPO_VALIDO["nome"]
        assert body["status"] == "pending"
    finally:
        _limpar()
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest apps/cliente/tests/test_views.py -v`
Expected: `FAIL` em todos os 7 novos casos — os que testam `status` no `POST` porque o payload é ignorado silenciosamente hoje (não valida, não grava, então `body["status"]` não existe → `KeyError`); os que chamam `client.patch(...)` porque `cliente_detail` ainda responde `405 METODO_NAO_PERMITIDO` pra `PATCH`.

- [ ] **Step 3: Implementar em `apps/cliente/views.py`**

Substitua o arquivo inteiro:

```python
import json

from django.http import JsonResponse

from apps.cliente import repository
from apps.optin.validation import ValidationError, normalizar_documento, validar_documento
from shared.jwt_auth import jwt_required

STATUS_VALIDOS = {"active", "inactive", "pending"}


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
        "status": cliente["status"],
        "criadoEm": cliente["criado_em"].isoformat() if hasattr(cliente["criado_em"], "isoformat") else cliente["criado_em"],
        "atualizadoEm": cliente["atualizado_em"].isoformat() if hasattr(cliente["atualizado_em"], "isoformat") else cliente["atualizado_em"],
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

    status = payload.get("status")
    if status is not None and status not in STATUS_VALIDOS:
        return _erro_json("CLI003", "status inválido", 422)

    if repository.buscar_por_documento(request.financiador_id, documento):
        return _erro_json("CLIENTE_JA_CADASTRADO", "já existe cliente cadastrado com esse documento", 409)

    cliente = repository.criar(request.financiador_id, {
        "documento": documento,
        "documento_tipo": tipo,
        "nome": nome,
        "email": payload.get("email"),
        "telefone": payload.get("telefone"),
        "status": status,
    })
    return JsonResponse(_serializar_cliente(cliente), status=201)


@jwt_required
def listar_clientes(request):
    documento_raw = request.GET.get("documento")
    documento_filtro = documento_raw
    if documento_raw:
        try:
            documento_filtro = normalizar_documento(documento_raw)
        except ValidationError:
            pass
    filtros = {"documento": documento_filtro}
    limit = min(int(request.GET.get("limit", 50)), 200)
    resultado = repository.listar(request.financiador_id, filtros, limit)
    return JsonResponse({"dados": [_serializar_cliente(c) for c in resultado]})


@jwt_required
def detalhar_cliente(request, cliente_id):
    cliente = repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)
    return JsonResponse(_serializar_cliente(cliente))


@jwt_required
def atualizar_cliente(request, cliente_id):
    cliente = repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    status = payload.get("status")
    if status is not None and status not in STATUS_VALIDOS:
        return _erro_json("CLI003", "status inválido", 422)

    campos = {}
    for chave in ("nome", "email", "telefone", "status"):
        if chave in payload:
            campos[chave] = payload[chave]

    if campos:
        cliente = repository.atualizar(request.financiador_id, cliente_id, campos)

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
    if request.method == "PATCH":
        return atualizar_cliente(request, cliente_id)
    return JsonResponse({"erro": "METODO_NAO_PERMITIDO"}, status=405)
```

- [ ] **Step 4: Rodar de novo**

Run: `python -m pytest apps/cliente/tests/test_views.py -v`
Expected: todos passam (`14 passed` — 7 já existentes + 7 novos).

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest`
Expected: todos os testes passam — `apps/optin`, `apps/cliente`, `services/cerc`, `shared`, `config`.

- [ ] **Step 6: Commit**

```bash
git add apps/cliente/views.py apps/cliente/tests/test_views.py
git commit -m "feat: PATCH /api/v1/clientes/{id}; POST/GET ganham status e atualizadoEm"
```
