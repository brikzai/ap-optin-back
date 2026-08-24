# optin-service — Plan 09: Fundação de Multi-tenancy (um Cloud SQL por financiador) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retrofit o código já mergeado (Plans 01-07 + Plan 08 Task 1) para resolver banco Cloud SQL e credenciais CERC **por tenant (financiador)**, em vez de uma única configuração fixa de ambiente. Ao final: `shared/tenant_config.py` (novo), `shared/cloudsql_client.py`, `services/cerc/token_provider.py`, `services/cerc/client.py` e `shared/jwt_auth.py` todos parametrizados por `financiador_id`, com a suíte inteira verde.

**Architecture:** Um segredo por tenant no Secret Manager (`TENANT_{cnpj}_CONFIG`, JSON), lido via `shared/secrets.get_secret` (sem alterar esse arquivo — a dualidade dev-local/Secret-Manager já existe lá). `financiador_id` chega pelo claim do JWT do IdP corporativo, extraído em `shared/jwt_auth.py`, e é passado explicitamente (sem contextvar/estado implícito, seguindo o estilo explícito já usado no projeto) para todo código que acessa banco ou CERC.

**Tech Stack:** idêntico ao já usado (SQLAlchemy + Cloud SQL Python Connector, httpx, pytest + pytest-django + respx).

**Spec:** `docs/superpowers/specs/2026-08-24-multitenancy-design.md` (autoridade normativa deste plano) e `docs/superpowers/specs/SPEC-01-optin-e-gestao.md` §3 (autenticação CERC, inalterada — só o *client_id/secret* usado nela passa a ser por tenant).

## Global Constraints

- `financiador_id` é sempre uma string de 14 dígitos (CNPJ do financiador) — mesmo formato normalizado usado em `apps/optin/validation.py`, mas **sem** checagem de dígito verificador em `shared/jwt_auth.py` (só formato: 14 dígitos numéricos). O JWT já vem de um IdP corporativo confiável.
- Nenhuma função pública de `shared/cloudsql_client.py`, `services/cerc/token_provider.py` ou `services/cerc/client.py` pode ter uma versão "sem tenant" depois deste plano — não há fallback implícito para "tenant padrão" em código de produção (só em fixtures de teste, explicitamente).
- `CERC_AUTH_URL` e `CERC_API_BASE_URL` continuam variáveis de ambiente globais (host do ambiente CERC — homolog/produção — não varia por tenant). Só `cerc_client_id`/`cerc_client_secret`/`cerc_cnpj_solicitante` migram para dentro do JSON do tenant.
- `LOCAL_DATABASE_URL` é removido — não deve sobrar nenhuma referência a ele em código ou `.env.example` ao final deste plano.
- CNPJ de tenant usado em todo teste deste plano: `12345678000199` (mesmo valor já usado em exemplos por toda a suíte existente) — chamado de `FINANCIADOR_TESTE` nos arquivos de teste.

## Riscos e pendências desta implementação

- Rotação de segredo de um tenant não invalida o cache em memória do processo (mesma limitação já aceita para o cache de token do Plan 06) — fora de escopo resolver aqui.
- Este plano não constrói nenhuma automação de onboarding de tenant (criar instância, aplicar schema, gravar segredo) — processo manual/scriptado, fora do código da aplicação (design §9).
- As funções de `apps/optin/repository.py` e as views de `apps/optin/views.py` do Plan 08 (Tasks 6-10, ainda não implementadas) **não são tocadas por este plano** — elas serão escritas já cientes desta fundação quando o Plan 08 for retomado (o texto do Plan 08 será atualizado separadamente, depois deste plano fechar).

---

### Task 1: `shared/tenant_config.py` — leitura de configuração por tenant

**Files:**
- Create: `optin/shared/tenant_config.py`
- Test: `optin/shared/tests/test_tenant_config.py`

**Interfaces:**
- Consumes: `shared.secrets.get_secret(name: str) -> str` (inalterado).
- Produces: `get_tenant_config(financiador_id: str) -> dict` (cacheado em memória por processo, sem TTL).

- [ ] **Step 1: Escrever o teste que falha**

```python
# optin/shared/tests/test_tenant_config.py
import json

import pytest

import shared.tenant_config as tenant_config_module


@pytest.fixture(autouse=True)
def _clear_cache():
    tenant_config_module._cache.clear()
    yield
    tenant_config_module._cache.clear()


CONFIG_EXEMPLO = {
    "cloudsql_connection_name": "proj:region:instance",
    "cloudsql_db_user": "app",
    "cloudsql_db_password": "senha",
    "cloudsql_db_name": "app",
    "cerc_client_id": "client-1",
    "cerc_client_secret": "segredo",
    "cerc_cnpj_solicitante": "12345678000199",
}


def test_get_tenant_config_le_e_parseia_json(monkeypatch):
    from shared.tenant_config import get_tenant_config

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("TENANT_12345678000199_CONFIG", json.dumps(CONFIG_EXEMPLO))

    assert get_tenant_config("12345678000199") == CONFIG_EXEMPLO


def test_get_tenant_config_usa_cache_sem_reler_env(monkeypatch):
    from shared.tenant_config import get_tenant_config

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("TENANT_99999999000191_CONFIG", json.dumps(CONFIG_EXEMPLO))

    primeira = get_tenant_config("99999999000191")
    monkeypatch.delenv("TENANT_99999999000191_CONFIG", raising=False)
    segunda = get_tenant_config("99999999000191")

    assert primeira == segunda


def test_get_tenant_config_propaga_erro_quando_segredo_ausente(monkeypatch):
    from shared.tenant_config import get_tenant_config

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("TENANT_00000000000000_CONFIG", raising=False)

    with pytest.raises(RuntimeError):
        get_tenant_config("00000000000000")
```

Run: `pytest shared/tests/test_tenant_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.tenant_config'`

- [ ] **Step 2: Escrever `shared/tenant_config.py`**

```python
"""Configuração por tenant (financiador) — multi-tenancy §3.

Um segredo por tenant (TENANT_{financiador_id}_CONFIG, JSON) via
shared.secrets.get_secret — dev local lê a env var de mesmo nome (sem
GOOGLE_CLOUD_PROJECT); produção/homolog lê do Secret Manager, um segredo
por tenant. Cacheado em memória por processo, sem TTL (mesma filosofia do
cache de token de services/cerc/token_provider.py).

Ver docs/superpowers/specs/2026-08-24-multitenancy-design.md §3.
"""
import json

from shared.secrets import get_secret

_cache: dict = {}


def get_tenant_config(financiador_id: str) -> dict:
    if financiador_id in _cache:
        return _cache[financiador_id]

    raw = get_secret(f"TENANT_{financiador_id}_CONFIG")
    config = json.loads(raw)
    _cache[financiador_id] = config
    return config
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest shared/tests/test_tenant_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
git add shared/tenant_config.py shared/tests/test_tenant_config.py
git commit -m "feat: per-tenant config reader (TENANT_{cnpj}_CONFIG secret)"
```

---

### Task 2: `shared/cloudsql_client.py` — banco por tenant

**Files:**
- Modify: `optin/shared/cloudsql_client.py`
- Modify: `optin/shared/tests/test_cloudsql_client.py`
- Modify: `optin/.env` (migração de valores reais já existentes)
- Modify: `optin/.env.example`

**Interfaces:**
- Consumes: `shared.tenant_config.get_tenant_config` (Task 1).
- Produces: `get_db(financiador_id: str) -> CloudSQLClient` (substitui o `get_db()` sem argumento). `QueryBuilder`/`ExecuteResult`/`CloudSQLClient` (classes) permanecem **inalteradas** — só a fábrica no fim do arquivo muda.

- [ ] **Step 1: Migrar `.env` e `.env.example` (pré-requisito para o teste rodar contra o Cloud SQL real)**

Em `.env` (real, já tem os valores — **não invente/hardcode senha nova, reaproveite a que já está lá**): leia os valores atuais de `CLOUDSQL_CONNECTION_NAME`, `CLOUDSQL_DB_USER`, `CLOUDSQL_DB_PASSWORD`, `CLOUDSQL_DB_NAME`, `CERC_CLIENT_ID`, `CERC_CLIENT_SECRET` e monte uma única linha:

```
TENANT_12345678000199_CONFIG={"cloudsql_connection_name":"<valor atual de CLOUDSQL_CONNECTION_NAME>","cloudsql_db_user":"<valor atual de CLOUDSQL_DB_USER>","cloudsql_db_password":"<valor atual de CLOUDSQL_DB_PASSWORD>","cloudsql_db_name":"<valor atual de CLOUDSQL_DB_NAME>","cerc_client_id":"<valor atual de CERC_CLIENT_ID>","cerc_client_secret":"<valor atual de CERC_CLIENT_SECRET>","cerc_cnpj_solicitante":"12345678000199"}
```

Depois, remova de `.env` as linhas `LOCAL_DATABASE_URL`, `CLOUDSQL_CONNECTION_NAME`, `CLOUDSQL_DB_USER`, `CLOUDSQL_DB_PASSWORD`, `CLOUDSQL_DB_NAME`, `CERC_CLIENT_ID`, `CERC_CLIENT_SECRET` (agora redundantes — os valores vivem dentro do JSON). Mantenha `CERC_AUTH_URL`/`CERC_API_BASE_URL` como estão.

Em `.env.example`, substitua o mesmo bloco de variáveis por (sem valores reais):

```
# Multi-tenancy — um segredo JSON por tenant (financiador), chave = CNPJ.
# Ver docs/superpowers/specs/2026-08-24-multitenancy-design.md §3.
TENANT_12345678000199_CONFIG={"cloudsql_connection_name":"","cloudsql_db_user":"","cloudsql_db_password":"","cloudsql_db_name":"","cerc_client_id":"","cerc_client_secret":"","cerc_cnpj_solicitante":"12345678000199"}
```

- [ ] **Step 2: Escrever o teste que falha**

Substituir o conteúdo de `shared/tests/test_cloudsql_client.py` por:

```python
# optin/shared/tests/test_cloudsql_client.py
from dotenv import load_dotenv
load_dotenv()

import json
import os

import pytest

FINANCIADOR_TESTE = "12345678000199"
FINANCIADOR_TESTE_2 = "99999999000191"

from shared.cloudsql_client import get_db  # noqa: E402
import shared.cloudsql_client as cloudsql_client_module  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_dominio_arranjo():
    db = get_db(FINANCIADOR_TESTE)
    db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    yield
    db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()


def test_insert_select_update_delete_round_trip():
    db = get_db(FINANCIADOR_TESTE)

    inserted = db.table("dominio_arranjo").insert({
        "codigo": "VCC",
        "descricao": "Visa Crédito",
        "ativo": True,
        "atualizado_em": "2026-08-19T00:00:00-03:00",
    }).execute()
    assert inserted.data[0]["codigo"] == "VCC"

    found = db.table("dominio_arranjo").select("*").eq("codigo", "VCC").execute()
    assert len(found.data) == 1
    assert found.data[0]["ativo"] is True

    updated = db.table("dominio_arranjo").update({"ativo": False}).eq("codigo", "VCC").execute()
    assert updated.data[0]["ativo"] is False

    deleted = db.table("dominio_arranjo").delete().eq("codigo", "VCC").execute()
    assert len(deleted.data) == 1

    empty = db.table("dominio_arranjo").select("*").eq("codigo", "VCC").execute()
    assert empty.data == []


def test_get_db_cacheia_por_financiador_id(monkeypatch):
    cloudsql_client_module._clients.clear()
    # Aponta o "segundo tenant" para a MESMA config do tenant de teste — o
    # objetivo aqui é provar que o cache é chaveado por financiador_id (dois
    # tenants diferentes nunca compartilham o mesmo CloudSQLClient), não
    # provisionar um segundo Cloud SQL real só para este teste.
    monkeypatch.setenv(
        f"TENANT_{FINANCIADOR_TESTE_2}_CONFIG",
        os.environ[f"TENANT_{FINANCIADOR_TESTE}_CONFIG"],
    )

    db1a = get_db(FINANCIADOR_TESTE)
    db1b = get_db(FINANCIADOR_TESTE)
    db2 = get_db(FINANCIADOR_TESTE_2)

    assert db1a is db1b
    assert db1a is not db2

    cloudsql_client_module._clients.pop(FINANCIADOR_TESTE_2, None)
```

Run: `pytest shared/tests/test_cloudsql_client.py -v`
Expected: FAIL — `get_db()` chamado sem argumento não tem mais o comportamento antigo (a assinatura muda nesta task) / `AttributeError`/`TypeError` conforme o estado atual do arquivo

- [ ] **Step 3: Retrofit em `shared/cloudsql_client.py`**

Manter `ExecuteResult`, `QueryBuilder` e `CloudSQLClient` (classes) **exatamente como estão hoje** — só substituir tudo a partir da função `_create_engine` (linha ~176 do arquivo atual) até o fim do arquivo por:

```python
def _create_engine(config: dict):
    import sqlalchemy
    from google.cloud.sql.connector import Connector, IPTypes

    connector = Connector()

    def getconn():
        return connector.connect(
            config["cloudsql_connection_name"],
            "pg8000",
            user=config["cloudsql_db_user"],
            password=config["cloudsql_db_password"],
            db=config["cloudsql_db_name"],
            ip_type=IPTypes.PUBLIC,
        )

    logger.info("[CloudSQL] Engine criado para tenant (connection=%s)", config["cloudsql_connection_name"])
    return sqlalchemy.create_engine(
        "postgresql+pg8000://", creator=getconn, pool_size=5, max_overflow=2, pool_timeout=30, pool_recycle=1800,
    )


_clients: dict = {}


def get_db(financiador_id: str) -> CloudSQLClient:
    if financiador_id in _clients:
        return _clients[financiador_id]

    config = get_tenant_config(financiador_id)
    engine = _create_engine(config)
    client = CloudSQLClient(engine)
    _clients[financiador_id] = client
    return client
```

E adicionar ao topo do arquivo (junto aos outros imports):

```python
from shared.tenant_config import get_tenant_config
```

Remover o import `from typing import Any, List, Optional` só se `Optional` deixar de ser usado em outro ponto do arquivo — checar antes de remover (ele ainda é usado em `ExecuteResult.__init__` e em `QueryBuilder`, então **mantenha o import como está**, só o bloco de baixo muda).

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest shared/tests/test_cloudsql_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/cloudsql_client.py shared/tests/test_cloudsql_client.py .env .env.example
git commit -m "feat: get_db(financiador_id) — one Cloud SQL engine cached per tenant"
```

---

### Task 3: `services/cerc/token_provider.py` — token CERC por tenant

**Files:**
- Modify: `optin/services/cerc/token_provider.py`
- Modify: `optin/services/cerc/tests/test_token_provider.py`

**Interfaces:**
- Consumes: `shared.tenant_config.get_tenant_config` (Task 1).
- Produces: `get_cerc_token(financiador_id: str) -> str`; `invalidate_token(financiador_id: str) -> None` (substituem as versões sem argumento).

- [ ] **Step 1: Escrever o teste que falha**

Substituir o conteúdo de `services/cerc/tests/test_token_provider.py` por:

```python
import json
import threading

import httpx
import pytest
import respx

from services.cerc import token_provider

FINANCIADOR_TESTE = "12345678000199"


@pytest.fixture(autouse=True)
def _reset_cache_and_env(monkeypatch):
    monkeypatch.setenv("CERC_AUTH_URL", "https://api.int.cerc.com/oauth/token")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv(f"TENANT_{FINANCIADOR_TESTE}_CONFIG", json.dumps({
        "cerc_client_id": "client-123",
        "cerc_client_secret": "segredo-local",
    }))
    token_provider._caches.clear()
    token_provider._locks.clear()

    import shared.tenant_config as tenant_config_module
    tenant_config_module._cache.clear()
    yield


@respx.mock
def test_get_cerc_token_fetches_and_caches():
    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )

    token = token_provider.get_cerc_token(FINANCIADOR_TESTE)
    assert token == "tok-1"
    assert route.call_count == 1

    token_again = token_provider.get_cerc_token(FINANCIADOR_TESTE)
    assert token_again == "tok-1"
    assert route.call_count == 1  # cached, no second call


@respx.mock
def test_get_cerc_token_refetches_after_80_percent_expiry():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    token_provider.get_cerc_token(FINANCIADOR_TESTE)

    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600})
    )
    calls_before = route.call_count
    token_provider._caches[FINANCIADOR_TESTE]["expires_at"] = 0.0  # simula 80% de expires_in decorrido

    token = token_provider.get_cerc_token(FINANCIADOR_TESTE)
    assert token == "tok-2"
    assert route.call_count == calls_before + 1


@respx.mock
def test_get_cerc_token_single_flight_under_concurrency():
    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )

    results = []

    def _call():
        results.append(token_provider.get_cerc_token(FINANCIADOR_TESTE))

    threads = [threading.Thread(target=_call) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["tok-1"] * 10
    assert route.call_count == 1


@respx.mock
def test_invalidate_token_forces_refetch():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    token_provider.get_cerc_token(FINANCIADOR_TESTE)

    token_provider.invalidate_token(FINANCIADOR_TESTE)

    route = respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600})
    )
    calls_before = route.call_count
    assert token_provider.get_cerc_token(FINANCIADOR_TESTE) == "tok-2"
    assert route.call_count == calls_before + 1


@respx.mock
def test_get_cerc_token_isola_cache_entre_tenants(monkeypatch):
    monkeypatch.setenv("TENANT_99999999000191_CONFIG", json.dumps({
        "cerc_client_id": "client-999",
        "cerc_client_secret": "outro-segredo",
    }))
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "tok-tenant-1", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "tok-tenant-2", "expires_in": 3600}),
        ]
    )

    token1 = token_provider.get_cerc_token(FINANCIADOR_TESTE)
    token2 = token_provider.get_cerc_token("99999999000191")

    assert token1 == "tok-tenant-1"
    assert token2 == "tok-tenant-2"
```

Run: `pytest services/cerc/tests/test_token_provider.py -v`
Expected: FAIL — `get_cerc_token()`/`invalidate_token()` ainda exigem zero argumentos (`TypeError`) e `token_provider._caches`/`_locks` ainda não existem

- [ ] **Step 2: Retrofit em `services/cerc/token_provider.py`**

Substituir o conteúdo do arquivo inteiro por:

```python
"""OAuth2 client-credentials — obtém e cacheia o access token da CERC, por
tenant (financiador).

Cache em memória por processo, uma entrada por financiador_id. Renovação
proativa a 80% de expires_in (uma chamada depois desse ponto sempre busca
um token novo, nunca devolve um perto de vencer). Single-flight por tenant
via threading.Lock com double-checked locking: o caminho comum (token em
cache, ainda válido) nunca bloqueia; só quem chega com o cache frio/vencido
disputa o lock daquele tenant, e só um deles de fato faz a chamada HTTP.

client_id/client_secret vêm de shared.tenant_config.get_tenant_config —
CERC_AUTH_URL continua env var global (host do ambiente CERC, não varia
por tenant). Ver docs/superpowers/specs/2026-08-24-multitenancy-design.md §5.

Em 401 numa chamada à API da CERC, quem fez a chamada (services/cerc/client.py)
invalida o cache daquele tenant com invalidate_token(financiador_id) e tenta
de novo uma única vez — o retry em si não é responsabilidade deste módulo.
"""

import os
import threading
import time

import httpx

from shared.tenant_config import get_tenant_config

_meta_lock = threading.Lock()
_locks: dict = {}
_caches: dict = {}


def _lock_for(financiador_id: str) -> threading.Lock:
    if financiador_id not in _locks:
        with _meta_lock:
            if financiador_id not in _locks:
                _locks[financiador_id] = threading.Lock()
    return _locks[financiador_id]


def _fetch_token(financiador_id: str) -> dict:
    config = get_tenant_config(financiador_id)
    response = httpx.post(
        os.environ["CERC_AUTH_URL"],
        data={
            "grant_type": "client_credentials",
            "client_id": config["cerc_client_id"],
            "client_secret": config["cerc_client_secret"],
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def get_cerc_token(financiador_id: str) -> str:
    now = time.time()
    cache = _caches.get(financiador_id)
    if cache and cache["access_token"] and now < cache["expires_at"]:
        return cache["access_token"]

    with _lock_for(financiador_id):
        now = time.time()
        cache = _caches.get(financiador_id)
        if cache and cache["access_token"] and now < cache["expires_at"]:
            return cache["access_token"]

        payload = _fetch_token(financiador_id)
        _caches[financiador_id] = {
            "access_token": payload["access_token"],
            "expires_at": now + 0.8 * payload["expires_in"],
        }
        return _caches[financiador_id]["access_token"]


def invalidate_token(financiador_id: str) -> None:
    with _lock_for(financiador_id):
        _caches.pop(financiador_id, None)
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest services/cerc/tests/test_token_provider.py -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Commit**

```bash
git add services/cerc/token_provider.py services/cerc/tests/test_token_provider.py
git commit -m "feat: per-tenant CERC token cache (cache/lock keyed by financiador_id)"
```

---

### Task 4: `services/cerc/client.py` — chamadas CERC por tenant

**Files:**
- Modify: `optin/services/cerc/client.py`
- Modify: `optin/services/cerc/tests/test_client.py`

**Interfaces:**
- Consumes: `services.cerc.token_provider.{get_cerc_token,invalidate_token}` (Task 3, agora exigem `financiador_id`); `shared.cloudsql_client.get_db(financiador_id)` (Task 2).
- Produces: `registrar_optin(financiador_id: str, payload: dict, correlacao_id: str) -> list`; `atualizar_optin(financiador_id: str, protocolo_cerc: str, payload: dict, correlacao_id: str) -> list`; `encerrar_optin(financiador_id: str, protocolo_cerc: str, payload: dict, correlacao_id: str) -> list`. `CercApiError` inalterado.

- [ ] **Step 1: Escrever o teste que falha**

Substituir o conteúdo de `services/cerc/tests/test_client.py` por:

```python
from dotenv import load_dotenv
load_dotenv()

import json
import os

import httpx
import pytest
import respx

os.environ.setdefault("CERC_AUTH_URL", "https://api.int.cerc.com/oauth/token")
os.environ.setdefault("CERC_API_BASE_URL", "https://ap-homolog.cerc.inf.br")

FINANCIADOR_TESTE = "12345678000199"

from services.cerc import client, token_provider  # noqa: E402
from shared.cloudsql_client import get_db  # noqa: E402


def _mock_token():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )


def _multistatus(protocolo="P-1", referencia="OPTIN-2026-000001", status="0"):
    return [
        {
            "protocolo": protocolo,
            "referenciaExterna": referencia,
            "dataHoraProcessamento": "2026-08-17T12:00:00.000Z",
            "status": status,
            "erros": [],
        }
    ]


@pytest.fixture(autouse=True)
def _reset_state():
    token_provider._caches.clear()
    token_provider._locks.clear()
    import shared.tenant_config as tenant_config_module
    tenant_config_module._cache.clear()

    db = get_db(FINANCIADOR_TESTE)
    db.table("cerc_requisicao").delete().eq("correlacao_id", "corr-1").execute()
    yield
    db.table("cerc_requisicao").delete().eq("correlacao_id", "corr-1").execute()


@respx.mock
def test_registrar_optin_sends_array_body_with_tipo_operacao_c():
    _mock_token()
    route = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(207, json=_multistatus())
    )

    result = client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert result == _multistatus()
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == [{"cnpjFinanciador": "12345678000199", "tipoOperacao": "C"}]

    logged = get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] == 207
    assert logged.data[0]["recurso"] == "/opt_in"
    assert logged.data[0]["tentativa"] == 1


@respx.mock
def test_registrar_optin_retries_once_on_401():
    respx.post("https://api.int.cerc.com/oauth/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "tok-expired", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "tok-fresh", "expires_in": 3600}),
        ]
    )
    opt_in_route = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        side_effect=[
            httpx.Response(401, json={"erro": "token expirado"}),
            httpx.Response(207, json=_multistatus()),
        ]
    )

    result = client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert result == _multistatus()
    assert opt_in_route.call_count == 2

    logged = (
        get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*")
        .eq("correlacao_id", "corr-1").order("tentativa").execute()
    )
    assert [row["tentativa"] for row in logged.data] == [1, 2]
    assert logged.data[0]["http_status"] == 401
    assert logged.data[1]["http_status"] == 207


@respx.mock
def test_registrar_optin_raises_cerc_api_error_on_4xx():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(422, json={"codigo": "104804", "mensagem": "duplicado"})
    )

    with pytest.raises(client.CercApiError) as exc:
        client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    assert exc.value.status_code == 422
    assert exc.value.body == {"codigo": "104804", "mensagem": "duplicado"}

    logged = get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] == 422


@respx.mock
def test_registrar_optin_logs_before_raising_on_transport_failure():
    _mock_token()
    respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with pytest.raises(httpx.ConnectError):
        client.registrar_optin(FINANCIADOR_TESTE, {"cnpjFinanciador": "12345678000199"}, correlacao_id="corr-1")

    logged = get_db(FINANCIADOR_TESTE).table("cerc_requisicao").select("*").eq("correlacao_id", "corr-1").execute()
    assert len(logged.data) == 1
    assert logged.data[0]["http_status"] is None
    assert logged.data[0]["tentativa"] == 1


@respx.mock
def test_atualizar_optin_calls_opt_in_with_tipo_operacao_a_e_protocolo():
    _mock_token()
    route = respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
        return_value=httpx.Response(207, json=_multistatus(status="0"))
    )

    result = client.atualizar_optin(FINANCIADOR_TESTE, "P-1", {"vigenciaFim": "2027-01-01"}, correlacao_id="corr-1")

    assert result[0]["status"] == "0"
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == [{"vigenciaFim": "2027-01-01", "tipoOperacao": "A", "protocolo": "P-1"}]


@respx.mock
def test_encerrar_optin_sends_array_body_to_opt_out():
    _mock_token()
    route = respx.post("https://ap-homolog.cerc.inf.br/opt_out").mock(
        return_value=httpx.Response(207, json=_multistatus(status="0"))
    )

    result = client.encerrar_optin(FINANCIADOR_TESTE, "P-1", {"referenciaExterna": "OPTOUT-2026-000001"}, correlacao_id="corr-1")

    assert result[0]["status"] == "0"
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == [{"referenciaExterna": "OPTOUT-2026-000001", "protocoloOptIn": "P-1"}]
```

Run: `pytest services/cerc/tests/test_client.py -v`
Expected: FAIL — `registrar_optin`/`atualizar_optin`/`encerrar_optin` ainda têm a assinatura antiga (sem `financiador_id`)

- [ ] **Step 2: Retrofit em `services/cerc/client.py`**

Substituir o conteúdo do arquivo inteiro por:

```python
"""Cliente REST da CERC — registrar/atualizar/encerrar opt-in.

Toda chamada grava uma linha em cerc_requisicao ANTES de decidir se levanta
CercApiError (design §4) — a trilha de auditoria existe mesmo quando a
chamada termina em erro. Em 401, invalida o token (Plan 06) e repete a
mesma chamada uma única vez, com uma segunda linha de log (tentativa=2).

Confirmado contra SPEC-01 §4.1/§4.2: `/opt_in` é o único recurso para
criar E atualizar opt-in (diferenciado por `tipoOperacao`: "C" ou "A",
com `protocolo` obrigatório na atualização) — não existe `PUT
/opt_in/{protocolo}`. Ambos os recursos (`/opt_in` e `/opt_out`) recebem
sempre um array (lote), mesmo para um único item, e respondem 207
multi-status (array, um item por entrada enviada). O parsing item-a-item
do 207 é responsabilidade de quem consome o retorno desta camada de
transporte, não deste módulo.

Multi-tenancy: toda função pública recebe financiador_id como primeiro
parâmetro — usado para buscar o token do tenant certo
(services/cerc/token_provider.py) e gravar a auditoria em cerc_requisicao
do banco do tenant certo (shared/cloudsql_client.py). Ver
docs/superpowers/specs/2026-08-24-multitenancy-design.md §5.
"""

import os
import uuid

import httpx

from services.cerc.token_provider import get_cerc_token, invalidate_token
from shared.cloudsql_client import get_db


class CercApiError(Exception):
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"CERC API respondeu {status_code}: {body}")


def _log_attempt(financiador_id: str, recurso: str, correlacao_id: str, request_body, response, tentativa: int) -> None:
    get_db(financiador_id).table("cerc_requisicao").insert({
        "id": str(uuid.uuid4()),
        "recurso": recurso,
        "correlacao_id": correlacao_id,
        "http_status": response.status_code if response is not None else None,
        "request_body": request_body,
        "response_body": _safe_json(response),
        "tentativa": tentativa,
    }).execute()


def _safe_json(response):
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _send(method: str, path: str, batch, correlacao_id: str, token: str) -> httpx.Response:
    url = os.environ["CERC_API_BASE_URL"] + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": correlacao_id,
    }
    return httpx.request(method, url, json=batch, headers=headers, timeout=15.0)


def _request(financiador_id: str, method: str, path: str, batch: list, correlacao_id: str) -> list:
    token = get_cerc_token(financiador_id)
    try:
        response = _send(method, path, batch, correlacao_id, token)
    except httpx.HTTPError:
        _log_attempt(financiador_id, path, correlacao_id, batch, None, tentativa=1)
        raise
    _log_attempt(financiador_id, path, correlacao_id, batch, response, tentativa=1)

    if response.status_code == 401:
        invalidate_token(financiador_id)
        token = get_cerc_token(financiador_id)
        try:
            response = _send(method, path, batch, correlacao_id, token)
        except httpx.HTTPError:
            _log_attempt(financiador_id, path, correlacao_id, batch, None, tentativa=2)
            raise
        _log_attempt(financiador_id, path, correlacao_id, batch, response, tentativa=2)

    if response.status_code >= 400:
        raise CercApiError(response.status_code, _safe_json(response))

    return response.json()


def registrar_optin(financiador_id: str, payload: dict, correlacao_id: str) -> list:
    item = {**payload, "tipoOperacao": "C"}
    return _request(financiador_id, "POST", "/opt_in", [item], correlacao_id)


def atualizar_optin(financiador_id: str, protocolo_cerc: str, payload: dict, correlacao_id: str) -> list:
    item = {**payload, "tipoOperacao": "A", "protocolo": protocolo_cerc}
    return _request(financiador_id, "POST", "/opt_in", [item], correlacao_id)


def encerrar_optin(financiador_id: str, protocolo_cerc: str, payload: dict, correlacao_id: str) -> list:
    item = {**payload, "protocoloOptIn": protocolo_cerc}
    return _request(financiador_id, "POST", "/opt_out", [item], correlacao_id)
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest services/cerc/tests/test_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Commit**

```bash
git add services/cerc/client.py services/cerc/tests/test_client.py
git commit -m "feat: thread financiador_id through registrar/atualizar/encerrar_optin"
```

---

### Task 5: `shared/jwt_auth.py` — claim `financiador_id`

**Files:**
- Modify: `optin/shared/jwt_auth.py`
- Modify: `optin/shared/tests/test_jwt_auth.py`

**Interfaces:**
- Consumes: `validar_bearer_token` (já existe, inalterado).
- Produces: `jwt_required` passa a exigir o claim `financiador_id` (14 dígitos numéricos) e a popular `request.financiador_id` além do já existente `request.jwt_claims`.

- [ ] **Step 1: Escrever o teste que falha**

Substituir o conteúdo de `shared/tests/test_jwt_auth.py` por:

```python
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
    payload = {
        "exp": int(time.time()) + 300,
        "iss": "brikz-iam",
        "sub": "user-1",
        "financiador_id": "12345678000199",
    }
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


def test_jwt_required_popula_claims_e_financiador_id_quando_valido(keypair):
    from shared.jwt_auth import jwt_required

    private_pem, _ = keypair
    token = _token(private_pem)

    @jwt_required
    def view(request):
        return JsonResponse({"sub": request.jwt_claims["sub"], "financiador_id": request.financiador_id})

    request = RequestFactory().get("/api/v1/optins", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = view(request)
    assert response.status_code == 200
    assert json.loads(response.content) == {"sub": "user-1", "financiador_id": "12345678000199"}


def test_jwt_required_retorna_401_sem_claim_financiador_id(keypair):
    from shared.jwt_auth import jwt_required

    private_pem, _ = keypair
    token = pyjwt.encode(
        {"exp": int(time.time()) + 300, "iss": "brikz-iam", "sub": "user-1"}, private_pem, algorithm="RS256"
    )

    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    request = RequestFactory().get("/api/v1/optins", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = view(request)
    assert response.status_code == 401


def test_jwt_required_retorna_401_com_financiador_id_mal_formatado(keypair):
    from shared.jwt_auth import jwt_required

    private_pem, _ = keypair
    token = _token(private_pem, financiador_id="abc123")

    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    request = RequestFactory().get("/api/v1/optins", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = view(request)
    assert response.status_code == 401
```

Run: `pytest shared/tests/test_jwt_auth.py -v`
Expected: FAIL — `test_jwt_required_popula_claims_e_financiador_id_quando_valido` falha com `AttributeError: 'WSGIRequest' object has no attribute 'financiador_id'`; os dois testes novos de rejeição também falham (a view atual aceita o request sem checar o claim)

- [ ] **Step 2: Atualizar `jwt_required` em `shared/jwt_auth.py`**

Adicionar `import re` ao topo do arquivo. Substituir a função `jwt_required` por:

```python
def jwt_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            claims = validar_bearer_token(request.headers.get("Authorization", ""))
        except JwtAuthError as exc:
            return JsonResponse({"erro": "NAO_AUTENTICADO", "mensagem": exc.mensagem}, status=401)

        financiador_id = claims.get("financiador_id")
        if not financiador_id or not re.fullmatch(r"\d{14}", str(financiador_id)):
            return JsonResponse(
                {"erro": "NAO_AUTENTICADO", "mensagem": "claim financiador_id ausente ou inválido"}, status=401
            )

        request.jwt_claims = claims
        request.financiador_id = financiador_id
        return view_func(request, *args, **kwargs)

    return wrapper
```

Atualizar o docstring do módulo (topo do arquivo) para mencionar o claim `financiador_id`:

```python
"""Autenticação Bearer JWT do IdP corporativo (SPEC-01 §5, design §4).

Chave pública RS256 fixa (IAM_JWT_PUBLIC_KEY) e emissor esperado
(IAM_JWT_ISSUER) — sem JWKS/rede, mesmo padrão de shared/secrets.py para
segredos estáticos. Rotas isentas (health, push do Pub/Sub) simplesmente
não usam @jwt_required — não há middleware global com exceção por path.

Multi-tenancy: exige o claim `financiador_id` (CNPJ, 14 dígitos) em todo
JWT válido e o expõe em `request.financiador_id`, além de
`request.jwt_claims`. Ver
docs/superpowers/specs/2026-08-24-multitenancy-design.md §2.
"""
```

- [ ] **Step 3: Rodar e confirmar sucesso**

Run: `pytest shared/tests/test_jwt_auth.py -v`
Expected: PASS (9 tests)

- [ ] **Step 4: Commit**

```bash
git add shared/jwt_auth.py shared/tests/test_jwt_auth.py
git commit -m "feat: require financiador_id claim in jwt_required, expose request.financiador_id"
```

---

### Task 6: Suíte completa e fechamento

**Files:**
- Nenhum arquivo novo — apenas verificação.

- [ ] **Step 1: Rodar a suíte inteira**

Run: `pytest -v`
Expected: PASS — todos os testes (Plans 01-07, Plan 08 Task 1, Plan 09), sem regressão. `apps/optin/tests/test_health.py` e `apps/optin/tests/test_validation.py` não usam `get_db`/CERC, então não deveriam ser afetados por este plano; confirmar que continuam passando mesmo assim.

- [ ] **Step 2: Checar que não sobrou nenhuma referência a `LOCAL_DATABASE_URL`**

Run: `grep -rn "LOCAL_DATABASE_URL" --include="*.py" --include=".env*" .`
Expected: nenhum resultado (fora de arquivos de plano/spec em `docs/`, que são histórico e não precisam ser editados)

- [ ] **Step 3: Commit final (se houver qualquer ajuste feito durante a checagem)**

```bash
git add -A
git commit -m "chore: Plan 09 closeout — full suite green, no LOCAL_DATABASE_URL left"
```

---

## Self-Review Notes

- **Spec coverage:** design §2 (claim `financiador_id`) — Task 5. §3 (`get_tenant_config`, reaproveitando `shared/secrets.py` sem alterá-lo) — Task 1. §4 (banco por tenant, `LOCAL_DATABASE_URL` removido) — Task 2. §5 (token e client CERC por tenant) — Tasks 3/4. §6 (impacto no Plan 08) — deliberadamente **não** implementado aqui; fica para quando o Plan 08 for retomado, conforme a spec já registra. §8 (dev/test com tenant fixo + configs fake para isolamento) — Tasks 2/3 (`FINANCIADOR_TESTE`/`FINANCIADOR_TESTE_2`).
- **Placeholder scan:** nenhum "TODO"/"implementar depois" — o retrofit de `apps/optin/repository.py`/`views.py` do Plan 08 está explicitamente fora de escopo (não é um placeholder deste plano, é um plano diferente).
- **Type consistency:** `financiador_id: str` é sempre o primeiro parâmetro posicional em toda função pública tocada por este plano (`get_db`, `get_cerc_token`, `invalidate_token`, `registrar_optin`, `atualizar_optin`, `encerrar_optin`) — consistente entre Tasks 2, 3 e 4. `get_tenant_config(financiador_id) -> dict` (Task 1) é a única fonte de config lida por Tasks 2 e 3 — nenhuma das duas lê `os.environ` diretamente para dados que agora são por tenant.

**Next:** retomar o Plan 08 (Tasks 2, 3, 6-10) já ciente desta fundação — `apps/optin/repository.py` e as views passam a receber `financiador_id` de `request.financiador_id`. O texto do Plan 08 será atualizado antes de sua Task 6 ser despachada.
