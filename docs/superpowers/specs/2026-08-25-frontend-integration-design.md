# Integração com o front (`ap-front`) — Design de implementação

> **Status:** aprovado em brainstorming, pronto para plano de implementação.
> **Fonte normativa:** `SPEC-01-optin-e-gestao.md` (§3-6) + endpoints já implementados em `apps/optin/views.py` (Plans 08/09). Este documento não redefine o contrato de opt-in — mapeia o que já existe para o consumo do front em `C:\DEV\ap\ap-front`, e **adiciona** a entidade `cliente` (nova, decidida em brainstorming) como pré-requisito para gerir opt-ins.
> **Fora de escopo (decisão de brainstorming):** fluxo de assinatura digital do cliente (canvas de assinatura, `signature_token`, `document_url`), autenticação real (login + emissão de JWT com `financiador_id`) e o cadastro de cliente "completo" (limites de crédito, garantia, endereço) — isso continua sendo domínio de outro sistema (provavelmente `ap-back-contratos`). Este documento assume um JWT de desenvolvimento fixo.

## 1. Contexto

`ap-front` é uma SPA React/Vite/TypeScript com módulos de gestão de contratos, clientes e opt-ins, hoje quase toda alimentada por dados mockados ou por uma instância própria do Supabase. O módulo de opt-in (`OptInModule.tsx`, `NewOptInModal.tsx`, `OptInDetailsModal.tsx`) modela um fluxo de **assinatura digital do cliente** (tabela Supabase `opt_in_requests`, status `pending_signature → pending_registry → signed/expired/cancelled`) que é conceitualmente diferente do que este backend implementa: aqui, "opt-in" é o **registro CERC de uma unidade recebível** (arranjos, credenciadoras, vigência), criado e confirmado **de forma síncrona** numa única chamada (`POST /api/v1/optins` já registra na CERC e devolve o status final).

`NewOptInModal.tsx` já monta boa parte dos campos certos (arranjos, credenciadoras, datas), mas grava direto no Supabase com nomes de campo que não correspondem ao contrato real da API interna — a integração não é só trocar a URL, é remapear o payload.

Um segundo problema, levantado depois da primeira rodada de brainstorming: **o backend não tem nenhum conceito de "cliente"** — a tabela `optin` guarda só documentos crus (`documento_ufr`/`documento_titular`), sem entidade própria. Quem tem isso hoje é o front, via Supabase (tabela `clients`), mas o modal que a UI de opt-in reaproveita para criar cliente (`NewClientModal.tsx`) é o modal genérico do app inteiro, com campos de crédito (`totalLimit`, `usedLimit`, `collateralValue`) que não pertencem ao opt-in. Decisão: o `ap-back-optin` ganha uma entidade `cliente` **mínima**, própria, restrita ao que o opt-in precisa — não uma réplica do cadastro completo de crédito.

Gaps de infraestrutura identificados, que também fazem parte deste plano:
- **CORS**: o backend não tem `django-cors-headers` nem qualquer middleware de CORS — uma SPA em outra origem não consegue chamar a API hoje.
- **`evidenciaAutorizacaoId`**: campo obrigatório na criação de opt-in (`VAL008`), sem equivalente hoje no formulário do front (não há upload/storage de evidência dos dois lados — `validar_evidencia` só verifica presença, não formato). O front precisa de um campo novo para isso.
- **Sem ferramenta de migração de schema**: mudanças de schema são só edições em `docker/initdb/*.sql`, que só rodam automaticamente num banco novo. A tabela `cliente` (nova) e a coluna `optin.cliente_id` (nova) precisam ser aplicadas manualmente (`CREATE TABLE`/`ALTER TABLE` via psql) no Cloud SQL de dev já existente — não há dado real em produção ainda, então não há backfill a fazer.

## 2. Entidade `cliente` (nova)

Escopo mínimo: só o suficiente para (a) o gate "cliente precisa estar cadastrado antes do opt-in" e (b) mostrar nome do cliente na listagem de opt-ins. Sem limites de crédito, garantia, endereço, status — isso é de outro domínio.

### 2.1 Schema (`docker/initdb/03-cliente.sql`, novo arquivo)

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
`id` gerado como `f"cli_{ULID()}"`, mesmo padrão de `f"opt_{ULID()}"` já usado em `repository.criar_optin_pendente`. `documento`/`documento_tipo` vêm de `validar_documento()` (já existe em `apps/optin/validation.py`, reaproveitado sem alteração — mesma normalização e checagem de dígito verificador do CPF/CNPJ usada no opt-in).

`optin` ganha uma coluna nova:
```sql
ALTER TABLE optin ADD COLUMN cliente_id TEXT NOT NULL REFERENCES cliente(id);
```
`documento_ufr`/`documento_titular` **continuam existindo** na tabela `optin` (são o que vai no payload CERC e no que a checagem de anti-duplicidade já opera) — `cliente_id` não os substitui, é a referência que liga o opt-in ao cadastro do cliente para exibição/gestão no front.

### 2.2 Endpoints novos (`apps/cliente/`, novo app Django — mesmo padrão de `apps/optin/`: sem ORM, `views.py`/`repository.py`/`validation.py` próprios)

- **`POST /api/v1/clientes`** — corpo `{"documento": "...", "nome": "...", "email": "...", "telefone": "..."}` (`nome`/`documento` obrigatórios; `email`/`telefone` opcionais). Valida documento (VAL002, reaproveitado), checa duplicidade por documento → `409 CLIENTE_JA_CADASTRADO` se já existir. `@jwt_required`, escopado por `financiador_id` como todo o resto. Retorna o cliente serializado.
- **`GET /api/v1/clientes?documento=&nome=`** → `{"dados": [...]}`, mesmo padrão de `listar_optins`.
- **`GET /api/v1/clientes/{id}`** → cliente serializado, ou 404 `CLIENTE_NAO_ENCONTRADO`.
- Sem `PATCH`/`DELETE` (YAGNI — nada no fluxo de opt-in exige editar ou remover cliente agora).

Serialização:
```json
{"id": "cli_...", "documento": "12345678000199", "documentoTipo": "CNPJ", "nome": "...", "email": "...", "telefone": "...", "criadoEm": "..."}
```

### 2.3 Impacto no contrato de opt-in existente

`POST /api/v1/optins` **muda**: o corpo passa a exigir `clienteId` em vez de `usuarioFinalRecebedor`. O backend busca o `cliente` (404 `CLIENTE_NAO_ENCONTRADO` se `clienteId` não existir nesse tenant), usa `cliente.documento` como `usuarioFinalRecebedor`. `titular` continua opcional no payload (se ausente, usa o mesmo documento do cliente — comportamento já existente em `criar_optin`, sem mudança de lógica, só a origem do valor default).

`_serializar_optin` ganha `clienteId`, `clienteNome` (via join com `cliente` em `repository.buscar_por_id`/`listar`) — assim o front mostra nome do cliente na listagem sem precisar de uma segunda chamada por linha.

Como esse contrato ainda não tem nenhum consumidor em produção (é a própria integração deste documento), essa é uma mudança direta no formato existente, não uma migração com compatibilidade retroativa.

## 3. Contrato de opt-in consumido (atualizado com `clienteId`)

**`POST /api/v1/optins`** — corpo:
```json
{
  "clienteId": "cli_01JB...",
  "titular": "12345678000199",
  "dataAssinatura": "2026-08-10",
  "vigenciaInicio": "2026-08-11",
  "vigenciaFim": "2027-08-10",
  "carteira": null,
  "evidenciaAutorizacaoId": "doc-123",
  "credenciadoras": ["99T"],
  "arranjos": ["VCC"]
}
```
Headers: `Authorization: Bearer <jwt>`, `Idempotency-Key: <uuid>`. `referenciaExterna`, `cnpjSolicitante` e `cnpjFinanciador` continuam resolvidos pelo backend — não são enviados pelo front. Resposta: o opt-in serializado (síncrono — já reflete `ATIVO`/`REJEITADO` após a chamada à CERC), ou erro 404/422/409/502 (ver §7).

**`GET /api/v1/optins?status=&clienteId=&origem=&carteira=&vigenteEm=&limit=`** → `{"dados": [ {...} ]}` (filtro por documento continua existindo como `usuarioFinalRecebedor`, mas na prática o front vai filtrar por `clienteId` quando aplicável).

**`GET /api/v1/optins/{id}`** → opt-in serializado, ou 404.

**`PATCH /api/v1/optins/{id}`** — inalterado: aceita `vigenciaFim`, `carteira`, `arranjos`, `credenciadoras`, `cnpjFinanciador`; `referenciaExterna`/`cnpjSolicitante` são imutáveis (422 se enviados); exige `Idempotency-Key`; só permitido com opt-in `ATIVO` (409 caso contrário). `clienteId` não é editável (um opt-in não troca de cliente).

Formato de opt-in serializado:
```json
{
  "id": "opt_...", "referenciaExterna": "...", "protocoloCerc": "P-1",
  "origem": "...", "status": "ATIVO",
  "clienteId": "cli_...", "clienteNome": "...",
  "cnpjSolicitante": "...", "cnpjFinanciador": "...",
  "usuarioFinalRecebedor": "...", "titular": "...",
  "dataAssinatura": "2026-08-10", "vigenciaInicio": "2026-08-11", "vigenciaFim": "2027-08-10",
  "carteira": null, "credenciadoras": ["99T"], "arranjos": ["VCC"],
  "criadoEm": "2026-08-25T10:00:00"
}
```
`status` ∈ `PENDENTE` (transitório, não deveria ser observado por uma criação síncrona bem-sucedida), `ATIVO`, `REJEITADO`, `FALHA_ENVIO`. Erros seguem `{"erro": "<codigo>", "mensagem": "<texto>"}`.

## 4. Backend — CORS

Adicionar `django-cors-headers` (novo pacote em `requirements.txt`). Em `config/settings.py`:
- `INSTALLED_APPS += ["corsheaders"]`.
- `MIDDLEWARE`: `"corsheaders.middleware.CorsMiddleware"` inserido **antes** de `CommonMiddleware`.
- `CORS_ALLOWED_ORIGINS = get_secret("CORS_ALLOWED_ORIGINS", "").split(",")` (reaproveita `shared/secrets.py`, mesma dualidade env-var/Secret-Manager já usada em todo o resto de `settings.py`); dev local: `CORS_ALLOWED_ORIGINS=http://localhost:5173` no `.env`. Sem `CORS_ALLOW_ALL_ORIGINS` — lista explícita mesmo em dev.

Teste novo: request com header `Origin: http://localhost:5173` para um endpoint público (`health`) devolve `Access-Control-Allow-Origin: http://localhost:5173`.

## 5. Front — client HTTP (`src/services/optinApi.ts`, novo arquivo)

Módulo único, sem camada de hooks/cache (YAGNI — hoje só um componente lista, só um cria/edita; nenhuma necessidade de estado compartilhado).

- Lê `VITE_OPTIN_API_BASE_URL` e `VITE_OPTIN_DEV_JWT` de `import.meta.env` (novas entradas em `.env.example`, comentadas explicando que `VITE_OPTIN_DEV_JWT` é um token fixo de desenvolvimento, a ser substituído quando houver login real).
- Exporta, para opt-in: `listOptins(filtros?)`, `getOptin(id)`, `createOptin(payload)`, `updateOptin(id, payload)`.
- Exporta, para cliente: `listClientes(filtros?)`, `getCliente(id)`, `createCliente(payload)`.
- `createOptin`/`updateOptin` geram `Idempotency-Key` via `crypto.randomUUID()` e o enviam no header — mecanismo específico do opt-in (`apps/optin/idempotency.py`, tabela `idempotency_key`). `createCliente` **não** envia esse header: o endpoint de cliente não usa o decorator `idempotente`, a proteção contra duplicidade é só a constraint `UNIQUE (documento)` (§2.2) — reenviar o mesmo cadastro duas vezes por engano dá 409, não duplica.
- Toda chamada injeta `Authorization: Bearer ${VITE_OPTIN_DEV_JWT}` e `Content-Type: application/json`.
- Em resposta não-2xx, lança `OptinApiError extends Error` com `codigo`, `mensagem` (do corpo `{erro, mensagem}` quando presente) e `status` HTTP.
- Tipos `OptinDTO`, `ClienteDTO`, `CriarOptinPayload`, `AtualizarOptinPayload`, `CriarClientePayload` ficam neste arquivo (ou `src/types/optin.ts`, decisão de implementação).

## 6. Front — componentes

**Novo: modal mínimo de criação de cliente para o fluxo de opt-in** (ex.: `NewClienteOptinModal.tsx`) — substitui o uso do `NewClientModal.tsx` genérico **dentro do fluxo de opt-in apenas**. Campos: documento (CNPJ/CPF), nome, email, telefone. Chama `optinApi.createCliente`. O `NewClientModal.tsx` genérico e a tabela Supabase `clients` continuam existindo para o resto do app (fora de escopo).

**`NewOptInModal.tsx`**
- Passo "selecionar cliente" passa a chamar `optinApi.listClientes()` (em vez do Supabase `clients`) e abre o novo modal mínimo (em vez do `NewClientModal` genérico) para cadastrar um cliente novo.
- `handleSubmit` troca o `fetch` para Supabase por `optinApi.createOptin(payload)`: `clienteId` (do cliente selecionado), `titular` (= `formData.documentoTitular` ou vazio, deixando o backend usar o documento do cliente), `dataAssinatura`/`vigenciaInicio`/`vigenciaFim`, `carteira`, `credenciadoras`/`arranjos` (já computados hoje), `evidenciaAutorizacaoId` (campo novo — texto livre, obrigatório, sem storage real, refletindo `VAL008`). **Não envia** `tipoOperacao`, `referenciaExterna`, `cnpjSolicitante`, `cnpjFinanciador`, `definicaoUnidadeRecebivel`.
- Sucesso deixa de gerar link de assinatura — mostra o resultado direto: se voltou `ATIVO`, toast de sucesso com `protocoloCerc`; se falhar (`OptinApiError`), toast de erro com a `mensagem`, sem fechar o modal.

**`OptInModule.tsx`**
- `loadOptIns` chama `optinApi.listOptins()` no lugar do mock.
- `OptInClient` (interface local) é substituído por `OptinDTO` — a coluna "Cliente" passa a usar `clienteNome`/`clienteDocumento`/`usuarioFinalRecebedor` já vindos prontos na resposta (sem join no front).
- Abas "Ativos"/"Inativos": ativos = `status === 'ATIVO'`; inativos = `status === 'REJEITADO' || status === 'FALHA_ENVIO'`. Filtro de status no `<select>` passa a listar `ATIVO`, `REJEITADO`, `FALHA_ENVIO`.
- "Vence em breve" continua calculável a partir de `vigenciaFim` para opt-ins `ATIVO`.
- Coluna de ações: remove "Copiar Link" e "Encaminhar p/ Registradora". Mantém "Dados do Opt-In"; adiciona "Editar" quando `status === 'ATIVO'`.

**`OptInDetailsModal.tsx`**
- Exibe os campos reais do `OptinDTO` (`clienteNome`, `protocoloCerc`, `status`, `titular`, `cnpjFinanciador`, `vigenciaInicio`/`vigenciaFim`, `carteira`, `arranjos`, `credenciadoras`, `criadoEm`). Remove blocos de assinatura/encaminhamento.
- Modo de edição (só quando `status === 'ATIVO'`): campos editáveis `vigenciaFim`, `carteira`, `arranjos`, `credenciadoras`, `cnpjFinanciador`; ao salvar, chama `optinApi.updateOptin(id, payloadComSomenteOsCamposAlterados)`.

## 7. Tratamento de erro (todos os componentes acima)

Mapeamento de `OptinApiError.status` → toast:
- `404` → "cliente não encontrado" (criação de opt-in) ou "opt-in não encontrado" (edição/detalhe).
- `409` → mensagem do backend (`CLIENTE_JA_CADASTRADO` no cadastro de cliente; `OPTIN_NAO_ATIVO` na edição; opt-in equivalente já ativo na criação).
- `422` → mensagem do backend (validação de campo ou rejeição pela CERC).
- `502` → "CERC indisponível, tente novamente" (`CERC_INDISPONIVEL`).
- outros/erro de rede → toast genérico de erro.

## 8. Testes

- **Backend**: teste de CORS (§4). Testes novos para `apps/cliente/` (criar, listar, buscar, duplicidade de documento) seguindo o padrão já usado em `apps/optin/tests/` (banco real do tenant dev, `try/finally` de limpeza). Testes de opt-in existentes que hoje montam payload com `usuarioFinalRecebedor` direto precisam ser adaptados para criar um `cliente` primeiro e enviar `clienteId`.
- **Front**: sem framework de teste automatizado configurado em `ap-front` hoje — verificação manual, rodando `pnpm dev` (front) + `python manage.py runserver` (backend) com o tenant de dev configurado e um JWT de desenvolvimento fixo. Roteiro: cadastrar cliente novo, cadastrar cliente com documento duplicado (409), listar opt-ins (vazio e com dados), criar opt-in para um cliente existente (sucesso e rejeição CERC), criar opt-in com evidência ausente, editar um opt-in ativo, editar um opt-in inexistente (404).

## 9. Riscos e pendências

- Token de desenvolvimento fixo no front (`VITE_OPTIN_DEV_JWT`) não expira de forma gerenciada — artefato temporário até existir login real; não deve ir para nenhum ambiente além de dev local.
- `evidenciaAutorizacaoId` como texto livre sem storage é uma solução mínima aceita para não bloquear esta integração — se a CERC ou auditoria exigir evidência real (documento anexado), isso é um projeto próprio (upload + storage), fora deste escopo.
- Aplicar a nova tabela `cliente` e a coluna `optin.cliente_id` no Cloud SQL de dev já existente exige rodar o DDL manualmente (sem ferramenta de migração) — passo operacional a não esquecer antes de testar a integração ponta a ponta.
- Se o domínio de cliente completo (crédito/garantia) vier a ser construído separadamente (`ap-back-contratos` ou outro), vai existir uma segunda noção de "cliente" no ecossistema — reconciliar as duas (mesmo id? sincronização?) é um problema aberto, não resolvido aqui.
- Nenhuma tela do front cobre opt-out — fora de escopo, não investigado aqui.
