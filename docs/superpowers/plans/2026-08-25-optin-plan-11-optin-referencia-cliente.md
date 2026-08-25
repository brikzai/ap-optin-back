# Opt-in Passa a Referenciar Cliente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `optin` ganha uma FK `cliente_id`; `POST /api/v1/optins` passa a exigir `clienteId` em vez de `usuarioFinalRecebedor` cru, e a resposta serializada de opt-in ganha `clienteId`/`clienteNome`.

**Architecture:** Mudança de contrato numa API interna ainda sem consumidor em produção — sem compatibilidade retroativa. `criar_optin` resolve o `cliente` primeiro (404 se não existir) e usa `cliente.documento`/`cliente.documento_tipo` onde antes lia `payload["usuarioFinalRecebedor"]`. `_com_filhas` (repository) ganha um join simples com `cliente` para expor o nome na listagem/detalhe sem custar uma segunda chamada HTTP ao front.

**Tech Stack:** Django (sem ORM), SQLAlchemy Core via `shared.cloudsql_client`, pytest contra o Cloud SQL real do tenant dev.

**Spec:** `docs/superpowers/specs/2026-08-25-frontend-integration-design.md` (§2.3, §3)

**Depends on:** `2026-08-25-optin-plan-10-cliente-entidade.md` (tabela `cliente` e `apps.cliente.repository` precisam existir).

## Global Constraints

- Sem Django ORM — acesso via `shared.cloudsql_client.get_db(financiador_id)`.
- Toda função de repository recebe `financiador_id` como primeiro parâmetro.
- Erros HTTP sempre `{"erro": "<codigo>", "mensagem": "<texto>"}`.
- Testes rodam contra o Cloud SQL real do tenant dev (`FINANCIADOR_TESTE = "12345678000199"`, `DOC_UFR = "22751826000125"`) — sempre limpar em `try/finally`.
- Sem ferramenta de migração — o `ALTER TABLE` deste plano é aplicado manualmente (Task 1).
- Este contrato (`POST /api/v1/optins`) ainda não tem consumidor em produção — é uma mudança direta, não uma migração com compatibilidade.

---

### Task 1: Schema — `optin.cliente_id`

**Files:**
- Modify: `docker/initdb/01-optin-schema.sql`

**Interfaces:**
- Consumes: tabela `cliente` (Plan 10, Task 2).
- Produces: coluna `optin.cliente_id TEXT NOT NULL REFERENCES cliente(id)`, usada pela Task 2 deste plano.

- [ ] **Step 1: Atualizar o arquivo de schema (para bancos novos)**

Edite `docker/initdb/01-optin-schema.sql`, adicionando `cliente_id` à definição da tabela `optin` (depois de `cnpj_financiador`, antes de `documento_ufr`):

```sql
CREATE TABLE optin (
  id                    TEXT PRIMARY KEY,
  referencia_externa    TEXT UNIQUE NOT NULL,
  protocolo_cerc        TEXT UNIQUE,
  origem                TEXT NOT NULL,
  status                TEXT NOT NULL,
  cnpj_solicitante      TEXT NOT NULL,
  cnpj_financiador      TEXT NOT NULL,
  cliente_id            TEXT NOT NULL REFERENCES cliente(id),
  documento_ufr         TEXT NOT NULL,
  documento_ufr_tipo    TEXT NOT NULL,
  documento_titular     TEXT,
  data_assinatura       DATE NOT NULL,
  vigencia_inicio       DATE NOT NULL,
  vigencia_fim          DATE NOT NULL,
  carteira              TEXT,
  evidencia_id          TEXT NOT NULL,
  contrato_id           TEXT,
  criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (vigencia_fim >= vigencia_inicio),
  CHECK (vigencia_inicio >= data_assinatura)
);
CREATE INDEX ON optin (documento_ufr, status);
CREATE INDEX ON optin (vigencia_inicio, vigencia_fim);
```

Esta tabela referencia `cliente`, então em `docker/initdb/*.sql` (que só roda num banco novo, em ordem alfabética de arquivo) `03-cliente.sql` precisaria rodar antes de `01-optin-schema.sql` — mas os nomes já numerados (`01`, `02`, `03`) rodariam na ordem errada num banco novo do zero. Renomeie `docker/initdb/03-cliente.sql` (criado no Plan 10) para `docker/initdb/00-cliente.sql`:

```bash
git mv docker/initdb/03-cliente.sql docker/initdb/00-cliente.sql
```

- [ ] **Step 2: Verificar que a tabela `optin` do Cloud SQL de dev está vazia**

`ADD COLUMN ... NOT NULL` sem `DEFAULT` falha se houver linhas existentes. Toda a suíte de testes limpa os próprios dados em `try/finally`, então a tabela deveria estar vazia — confirme:

```bash
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
import sqlalchemy
from shared.cloudsql_client import get_db
with get_db('12345678000199')._engine.connect() as conn:
    print(conn.execute(sqlalchemy.text('SELECT count(*) FROM optin')).scalar())
"
```

Expected: imprime `0`. Se imprimir algo diferente de `0`, são sobras de uma rodada de teste que quebrou no meio (o `try/finally` não rodou) — rode `TRUNCATE optin_arranjo, optin_credenciadora, optout, optin;` pelo mesmo mecanismo antes de continuar (dados de teste descartáveis, não há dado real de produção neste tenant ainda).

- [ ] **Step 3: Aplicar o `ALTER TABLE` no Cloud SQL de dev**

```bash
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
import sqlalchemy
from shared.cloudsql_client import get_db
with get_db('12345678000199')._engine.begin() as conn:
    conn.execute(sqlalchemy.text('ALTER TABLE optin ADD COLUMN cliente_id TEXT NOT NULL REFERENCES cliente(id)'))
print('coluna cliente_id adicionada')
"
```

Expected: imprime `coluna cliente_id adicionada`. Se der erro `column "cliente_id" of relation "optin" already exists`, já rodou antes — pode seguir. Se der erro de `NOT NULL` por linha existente, volte ao Step 2.

- [ ] **Step 4: Commit**

```bash
git add docker/initdb/01-optin-schema.sql docker/initdb/00-cliente.sql
git commit -m "feat: coluna optin.cliente_id (FK)"
```

---

### Task 2: `criar_optin` passa a exigir `clienteId`

**Files:**
- Modify: `apps/optin/repository.py`
- Modify: `apps/optin/views.py`
- Modify: `apps/optin/tests/test_repository.py`
- Modify: `apps/optin/tests/test_views_listar_detalhar.py`
- Modify: `apps/optin/tests/test_views_atualizar_optin.py`
- Modify: `apps/optin/tests/test_views_criar_optin.py`

**Interfaces:**
- Consumes: `apps.cliente.repository.{criar, buscar_por_documento, buscar_por_id}` (Plan 10).
- Produces (usado pelo Plan 12 — front):
  - `POST /api/v1/optins` agora exige `clienteId` no corpo (em vez de `usuarioFinalRecebedor`); 404 `CLIENTE_NAO_ENCONTRADO` se o id não existir nesse tenant; 422 `CLI002` se `clienteId` ausente.
  - Toda resposta de opt-in serializado ganha `clienteId` e `clienteNome`.

- [ ] **Step 1: Atualizar os testes de repository que criam optin diretamente**

Estes três arquivos chamam `repository.criar_optin_pendente` direto (sem passar pela view HTTP), então só precisam de um `cliente_id` real no dict de dados — como a coluna agora é uma FK `NOT NULL`, um id inventado quebraria com violação de constraint.

Em `apps/optin/tests/test_repository.py`, adicione o import e o helper depois da linha `FINANCIADOR_TESTE = "12345678000199"`, e adicione `"cliente_id": _cliente_id_teste(),` como primeira chave de `_dados_base`:

```python
from apps.cliente import repository as cliente_repository


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _dados_base(**overrides):
    dados = {
        "cliente_id": _cliente_id_teste(),
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
```

(o resto do arquivo não muda — `_limpar`, todas as `test_*` funções continuam iguais, elas só passam por `_dados_base()`).

Em `apps/optin/tests/test_views_listar_detalhar.py`, adicione o mesmo helper (depois de `FINANCIADOR_TESTE = "12345678000199"`) e `"cliente_id": _cliente_id_teste(),` dentro do dict de `_criar_ativo`:

```python
from apps.cliente import repository as cliente_repository


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _criar_ativo():
    import datetime

    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cliente_id": _cliente_id_teste(),
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
```

Em `apps/optin/tests/test_views_atualizar_optin.py`, o mesmo helper e o mesmo ajuste em `_criar_pendente`:

```python
from apps.cliente import repository as cliente_repository


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _criar_pendente():
    import datetime

    return repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cliente_id": _cliente_id_teste(),
        "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"], "arranjos": ["VCC"],
    })
```

- [ ] **Step 2: Rodar esses três arquivos de teste e confirmar que falham**

Run: `python -m pytest apps/optin/tests/test_repository.py apps/optin/tests/test_views_listar_detalhar.py apps/optin/tests/test_views_atualizar_optin.py -v`
Expected: `FAIL` — erro de banco (`null value in column "cliente_id" violates not-null constraint` ou `KeyError: 'cliente_id'`), porque `repository.criar_optin_pendente` ainda não grava essa coluna.

- [ ] **Step 3: Atualizar `apps/optin/repository.py`**

Em `criar_optin_pendente`, adicione `cliente_id` ao `INSERT` e aos parâmetros:

```python
def criar_optin_pendente(financiador_id: str, dados: dict) -> dict:
    optin_id = f"opt_{ULID()}"
    referencia_externa = proxima_referencia_externa(financiador_id, "OPTIN", "optin_referencia_seq")

    with get_db(financiador_id)._engine.begin() as conn:
        conn.execute(sqlalchemy.text("""
            INSERT INTO optin (
                id, referencia_externa, origem, status, cnpj_solicitante, cnpj_financiador,
                cliente_id, documento_ufr, documento_ufr_tipo, documento_titular, data_assinatura,
                vigencia_inicio, vigencia_fim, carteira, evidencia_id
            ) VALUES (
                :id, :referencia_externa, 'OPTIN', 'PENDENTE', :cnpj_solicitante, :cnpj_financiador,
                :cliente_id, :documento_ufr, :documento_ufr_tipo, :documento_titular, :data_assinatura,
                :vigencia_inicio, :vigencia_fim, :carteira, :evidencia_id
            )
        """), {
            "id": optin_id,
            "referencia_externa": referencia_externa,
            "cnpj_solicitante": dados["cnpj_solicitante"],
            "cnpj_financiador": dados["cnpj_financiador"],
            "cliente_id": dados["cliente_id"],
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
```

Em `_com_filhas`, adicione o join com `cliente` para expor o nome:

```python
def _com_filhas(financiador_id: str, optin: dict) -> dict:
    optin_id = optin["id"]
    optin["credenciadoras"] = [
        r["cnpj"] for r in get_db(financiador_id).table("optin_credenciadora").select("cnpj").eq("optin_id", optin_id).execute().data
    ]
    optin["arranjos"] = [
        r["codigo"] for r in get_db(financiador_id).table("optin_arranjo").select("codigo").eq("optin_id", optin_id).execute().data
    ]
    cliente_rows = get_db(financiador_id).table("cliente").select("nome").eq("id", optin["cliente_id"]).execute().data
    optin["cliente_nome"] = cliente_rows[0]["nome"] if cliente_rows else None
    return optin
```

- [ ] **Step 4: Rodar os três arquivos de teste de novo**

Run: `python -m pytest apps/optin/tests/test_repository.py apps/optin/tests/test_views_listar_detalhar.py apps/optin/tests/test_views_atualizar_optin.py -v`
Expected: todos passam (esses arquivos não chamam a view HTTP `criar_optin`, então não são afetados pela Step 6 abaixo).

- [ ] **Step 5: Atualizar `apps/optin/tests/test_views_criar_optin.py`**

Substitua o topo do arquivo (imports e `CORPO_VALIDO`) por:

```python
import json

import httpx
import pytest
import respx
from dotenv import load_dotenv
load_dotenv()

from apps.cliente import repository as cliente_repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"

CORPO_VALIDO = {
    "credenciadoras": ["99T"],
    "arranjos": ["VCC"],
    "vigenciaInicio": "2026-08-11",
    "vigenciaFim": "2027-08-10",
    "dataAssinatura": "2026-08-10",
    "evidenciaAutorizacaoId": "doc_teste",
}


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _corpo_valido(**overrides):
    return {**CORPO_VALIDO, "clienteId": _cliente_id_teste(), **overrides}
```

Depois, substitua cada uso de `CORPO_VALIDO` no corpo das requisições HTTP por `_corpo_valido()` (mantendo os overrides que já existiam):

- `test_criar_optin_sucesso_retorna_201_ativo`: `data=json.dumps(CORPO_VALIDO)` → `data=json.dumps(_corpo_valido())`.
- `test_criar_optin_sem_jwt_retorna_401`: `data=json.dumps(CORPO_VALIDO)` → `data=json.dumps(_corpo_valido())`.
- `test_criar_optin_sem_idempotency_key_retorna_422`: `data=json.dumps(CORPO_VALIDO)` → `data=json.dumps(_corpo_valido())`.
- `test_criar_optin_vigencia_invalida_retorna_422`: troque `corpo = {**CORPO_VALIDO, "vigenciaFim": "2026-01-01"}` por `corpo = _corpo_valido(vigenciaFim="2026-01-01")`.
- `test_criar_optin_duplicado_retorna_409_sem_chamar_cerc`: as duas chamadas usam `data=json.dumps(CORPO_VALIDO)` — troque ambas por uma variável só, calculada uma vez: adicione `corpo = _corpo_valido()` logo depois do mock da rota CERC, e troque as duas ocorrências de `json.dumps(CORPO_VALIDO)` por `json.dumps(corpo)`.
- `test_criar_optin_falha_transporte_cerc_retorna_502_e_marca_falha_envio`: `data=json.dumps(CORPO_VALIDO)` → `data=json.dumps(_corpo_valido())`.
- `test_criar_optin_rejeitado_pela_cerc_retorna_422_e_marca_rejeitado`: `data=json.dumps(CORPO_VALIDO)` → `data=json.dumps(_corpo_valido())`.

Nenhuma asserção precisa mudar — `usuarioFinalRecebedor`, `referenciaExterna`, `status`, `protocoloCerc` continuam vindo da mesma forma na resposta.

- [ ] **Step 6: Rodar `test_views_criar_optin.py` e confirmar que falha**

Run: `python -m pytest apps/optin/tests/test_views_criar_optin.py -v`
Expected: `FAIL` em todos os testes que chegam à view — `criar_optin` ainda lê `payload.get("usuarioFinalRecebedor", "")`, que agora vem vazio (o payload não tem mais essa chave), causando `VAL001` (documento vazio) em vez do comportamento esperado por cada teste.

- [ ] **Step 7: Atualizar `apps/optin/views.py`**

Adicione o import no topo do arquivo:

```python
from apps.cliente import repository as cliente_repository
```

Substitua o início de `criar_optin` (da declaração da função até o fim do bloco de validação, antes de `if repository.existe_optin_ativo_equivalente(`):

```python
@jwt_required
@idempotente("optin_create")
def criar_optin(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _erro_json("JSON_INVALIDO", "corpo da requisição não é JSON válido", 400)

    cliente_id = payload.get("clienteId")
    if not cliente_id:
        return _erro_json("CLI002", "clienteId é obrigatório", 422)

    cliente = cliente_repository.buscar_por_id(request.financiador_id, cliente_id)
    if cliente is None:
        return _erro_json("CLIENTE_NAO_ENCONTRADO", "cliente não encontrado", 404)

    documento_ufr, tipo_ufr = cliente["documento"], cliente["documento_tipo"]

    try:
        titular_raw = payload.get("titular") or documento_ufr
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
```

E adicione `"cliente_id": cliente["id"],` como primeira chave do dict passado a `repository.criar_optin_pendente` (algumas linhas depois, sem mudar o resto):

```python
    optin = repository.criar_optin_pendente(request.financiador_id, {
        "cliente_id": cliente["id"],
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
```

Por fim, atualize `_serializar_optin` para incluir `clienteId`/`clienteNome`:

```python
def _serializar_optin(optin: dict) -> dict:
    return {
        "id": optin["id"],
        "referenciaExterna": optin["referencia_externa"],
        "protocoloCerc": optin.get("protocolo_cerc"),
        "origem": optin["origem"],
        "status": optin["status"],
        "clienteId": optin["cliente_id"],
        "clienteNome": optin.get("cliente_nome"),
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
```

- [ ] **Step 8: Rodar `test_views_criar_optin.py` de novo**

Run: `python -m pytest apps/optin/tests/test_views_criar_optin.py -v`
Expected: `7 passed`.

- [ ] **Step 9: Rodar a suíte inteira**

Run: `python -m pytest`
Expected: todos os testes passam — `apps/optin`, `apps/cliente`, `services/cerc`, `shared`, `config`.

- [ ] **Step 10: Commit**

```bash
git add apps/optin/repository.py apps/optin/views.py apps/optin/tests/test_repository.py apps/optin/tests/test_views_listar_detalhar.py apps/optin/tests/test_views_atualizar_optin.py apps/optin/tests/test_views_criar_optin.py
git commit -m "feat: POST /api/v1/optins passa a exigir clienteId; resposta ganha clienteId/clienteNome"
```
