# Cadastro Unificado de Cliente — Design

**Contexto:** O Plan 12 (`2026-08-25-optin-plan-12-integracao-front.md`) conectou o fluxo de opt-in do `ap-front` ao `apps.cliente` do `ap-back-optin`, mas deliberadamente manteve a aba "Clientes" principal do front (`ClientTable`, `NewClientModal`, `EditClientModal`) intocada — ela roda hoje sobre `DataContext`, cujos dados de cliente vêm de CSV local ou mock (`mockClients`), sem nenhuma persistência real. Este design conecta essa aba ao mesmo `apps.cliente`, tornando-o o cadastro único de cliente para o front inteiro (opt-in e o resto do app).

**Fora de escopo:** contratos, recebíveis, garantias e conta-corrente continuam vindo de CSV/mock — só a entidade `cliente` muda de fonte. Campos financeiros do `Client` (`totalLimit`, `usedLimit`, `availableLimit`, `collateralValue`) não entram no cadastro — pertencem a um domínio de crédito/garantias que ainda não tem backend próprio; ficam sempre `0` para clientes vindos da API. `ImportClientsModal.tsx`, se fizer criação em lote própria, fica pendente de um design separado.

**Decomposição de implementação:** segue o mesmo padrão dos Plans 10-12 — um plano por repositório. Seção 1-2 (schema + `apps.cliente`) viram um plano em `ap-back-optin`; seção 3 (frontend) vira um plano em `ap-front`, dependente do primeiro (mesma relação que o Plan 12 já tem com os Plans 10/11).

---

## 1. Schema — `ap-back-optin`, tabela `cliente`

A tabela hoje (`docker/initdb/00-cliente.sql`, Plan 10):

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

Ganha duas colunas:

```sql
ALTER TABLE cliente ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE cliente ADD COLUMN atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now();
```

`status` aceita `'active' | 'inactive' | 'pending'` (mesmos valores do `Client.status` do front) — validado na camada de aplicação (`apps.cliente.views`), não via `CHECK` no banco (mesmo padrão do resto do projeto: validação em Python, não em constraint SQL, exceto `NOT NULL`/`UNIQUE`/`REFERENCES`).

`nome`, `documento`, `email`, `telefone`, `criado_em` já cobrem o resto do `Client` (endereço/cidade/estado/CEP não existem no tipo `Client` real do front — só num tipo morto não utilizado, `NewClientData`; não entram no schema).

---

## 2. Backend — `apps.cliente`

**`repository.py`:**
- `criar(financiador_id, dados)`: passa a gravar `status` (default `"pending"` se `dados` não trouxer) além dos campos já existentes.
- Novo `atualizar(financiador_id, cliente_id, dados)`: `UPDATE cliente SET nome=..., email=..., telefone=..., status=..., atualizado_em=now() WHERE id=...` — só os campos presentes em `dados` são atualizados (partial update); retorna `None` se `cliente_id` não existir (view traduz pra 404).

**`views.py`:**
- `POST /clientes`: aceita `status` opcional no payload (default `pending` se ausente; mesma validação de enum do PATCH — `422 CLI003` se vier um valor fora de `active`/`inactive`/`pending`); resposta serializada ganha `status`/`atualizadoEm`.
- **Novo** `PATCH /clientes/{id}`: aceita `nome`/`email`/`telefone`/`status` (todos opcionais, só atualiza o que vier); `documento`/`documentoTipo`/`id` no payload são ignorados silenciosamente se vierem (não é erro — mesmo padrão de campos não-editáveis que `apps.optin` já usa pra `referenciaExterna`/`cnpjSolicitante`, mas aqui sem retornar 422, já que o front nunca envia esses campos no PATCH — `EditClientModal` não lê `id`/`documento` do form). `status`, se vier, precisa ser um de `active`/`inactive`/`pending` — outro valor retorna `422 CLI003` (`"status inválido"`, próximo código livre depois do `CLI002` que o Plan 11 já usa pra `clienteId` ausente). 404 `CLIENTE_NAO_ENCONTRADO` se o id não existir nesse tenant.

**Testes:** casos novos em `apps/cliente/tests/` — `PATCH` com sucesso (parcial e completo), `PATCH` 404, `POST` grava `status` default e explícito, `GET` retorna `status`/`atualizadoEm`.

---

## 3. Frontend — `ap-front`

**`src/services/optinApi.ts`** (client já existente do Plan 12):
- `ClienteDTO` ganha `status: 'active' | 'inactive' | 'pending'` e `atualizadoEm: string`.
- Novo `updateCliente(id: string, payload: {nome?: string; email?: string; telefone?: string; status?: string}): Promise<ClienteDTO>` → `PATCH /clientes/{id}`.

**`src/context/DataContext.tsx`:**
- `clients` deixa de vir de `mockClients`/CSV. `load()` chama `listClientes()` e mapeia `ClienteDTO → Client`:

  | `ClienteDTO` | `Client` |
  |---|---|
  | `id` | `id` |
  | `nome` | `name` |
  | `documento` | `document` |
  | `email` (null→`''`) | `email` |
  | `telefone` (null→`''`) | `phone` |
  | `status` | `status` |
  | `criadoEm` | `createdAt` (`new Date(...)`) |
  | `atualizadoEm` | `updatedAt` (`new Date(...)`) |
  | — | `totalLimit`/`usedLimit`/`availableLimit`/`collateralValue` = `0` |

- Falha em `listClientes()`: cai no mesmo `catch` de `load()`, mas **sem** fallback para `mockClients` nos clientes (misturar dado real com mock quebraria a premissa de cadastro único) — `clients` fica `[]`, `error` é setado, igual ao comportamento de erro que já existe pra CSV hoje.
- `receivables`/`contracts`/`contaCorrenteEntries`/`liquidationProblems` continuam exatamente como estão (CSV com fallback pra mock) — não mudam.
- `updateClient(clientId, data: Partial<Client>)`: **não** manda tudo pra API — `data` pode conter tanto campos de cadastro (`name`/`email`/`phone`/`status`) quanto financeiros (`totalLimit`/`usedLimit`/`availableLimit`/`collateralValue`, usados hoje por `FinancialModule.tsx` em `updateClient(limitEditingClient, { totalLimit, usedLimit, availableLimit })` — só campos financeiros, sem nenhum campo de cadastro). Lógica:
  1. Monta um payload só com as chaves de cadastro presentes em `data` (`name→nome`, `email→email`, `phone→telefone`, `status→status`).
  2. Se esse payload não for vazio, chama `updateCliente(clientId, payload)` e usa a resposta (`ClienteDTO`) pra atualizar os campos de cadastro do cliente em `clients`.
  3. Campos financeiros presentes em `data`, se houver, são sempre aplicados direto no estado local (`setClients`), independente do passo 2 — não existe persistência real pra eles ainda (igual já é hoje).
  4. Se `data` só tiver campos financeiros (caso do `FinancialModule.tsx`), o passo 2 é pulado inteiro — nenhuma chamada à API, só atualização local, exatamente como já funciona hoje pra esse caller.
  - Precisa virar `async` (hoje é síncrona) — `App.tsx` (linha ~541, dentro do `onSave` do `EditClientModal`) e `FinancialModule.tsx` (linha ~362) ajustam seus chamadores pra `await`/`.then()`; para o segundo, como não há chamada de rede nesse caminho, o `await` resolve no mesmo tick, sem mudança de comportamento perceptível.

**`src/components/NewClientModal.tsx`:**
- **Blast radius:** este modal é reusado em mais 8 lugares além da aba Clientes (`AntecipationJourney`, `CreditRecoveryJourney`, `GuaranteesJourney`, `NewFormalizationModal`, `OwnershipTransferJourney`, `PreContractedAntecipationJourney`, `PrepaymentJourney`, `App.tsx`), cada um com seu próprio `onSave` local (alguns já com mismatches de tipo pré-existentes, fora de escopo). A prop `onSave?: (clientData: NewClientData | NewClientData[]) => void` **não muda de assinatura** — só `NewClientModal.tsx` e `App.tsx` são tocados; os outros 8 call sites continuam exatamente como estão.
- Cada linha do lote (`cnpjList`) ganha um campo **Nome** ao lado do CNPJ (novo state, ex. `nameList: string[]`, mesmo índice do `cnpjList`). Nome obrigatório, mesma validação simples de campo-não-vazio que já existe pro CNPJ.
- `handleSubmit`: antes de chamar `onSave` (como já faz hoje), chama `createCliente({documento: cnpjLimpo, nome, email: undefined, telefone: undefined})` uma vez por linha, **sequencialmente** (`for...of` com `await`, não `Promise.all` — evita corrida em cima da constraint `UNIQUE(documento)` se o mesmo CNPJ aparecer duas vezes no lote por engano, e permite reportar erro por linha). O `id`/`createdAt`/`status` etc. do `clientsData` passado a `onSave` vêm da resposta real da API (`ClienteDTO`), não mais gerados localmente (`client-${Date.now()}-${index}`).
- Se uma linha falhar (ex. `409 CLIENTE_JA_CADASTRADO`), as anteriores já foram persistidas (não há rollback) — mostra toast de erro identificando qual CNPJ falhou e por quê, mantém as linhas restantes preenchidas no form pra o usuário corrigir e reenviar só o que faltou, e **não** chama `onSave` (nada foi concluído com sucesso). Só fecha o modal e chama `onSave` se todas as linhas foram criadas.
- Em `App.tsx` especificamente, o `onSave` da instância usada pela aba Clientes (linha ~520) troca de "acrescentar ao estado local" para "disparar `retry()` do `DataContext`" — já que a lista real já teria sido persistida pela chamada à API dentro do modal; os outros 8 call sites (que usam `NewClientModal` para adicionar um cliente local dentro da própria jornada, não para a lista global) continuam recebendo os dados criados via `onSave` e decidem o que fazer com eles como já fazem hoje.

**`src/components/EditClientModal.tsx`:** sem mudança de UI — continua editando só `name`/`email`/`phone`. `onSave` (em `App.tsx`) passa a ser `async` e aguardar `updateClient` antes de fechar o modal e mostrar o toast de sucesso; em caso de erro, mantém o modal aberto e mostra o erro (mesmo padrão dos outros modais do Plan 12).

---

## 4. Erros e casos de borda

- Todos os `fetch` novos reaproveitam `OptinApiError` (já existe em `optinApi.ts`) — mesmo padrão de tratamento (`err.message` no toast/form) usado no `NewClienteOptinModal.tsx`.
- `PATCH /clientes/{id}` com corpo vazio (`{}`): não é erro — não muda nada, retorna o cliente como está (comportamento igual a "nenhum campo enviado" já ser um no-op válido).
- Import de clientes em lote (`ImportClientsModal.tsx`, se existir fluxo similar): **fora de escopo deste design** — só `NewClientModal`/`EditClientModal`/`ClientTable`/`DataContext` mudam. Se `ImportClientsModal` também cria clientes hoje, fica pendente para um design separado.

## 5. Testes

- Backend: novos casos em `apps/cliente/tests/test_views.py` e `test_repository.py` pro `PATCH`, `status` default/explícito no `POST`, e `status`/`atualizadoEm` no `GET`/listagem — suíte roda contra o Cloud SQL real do tenant dev, mesmo padrão do resto do projeto.
- Frontend: sem framework automatizado (mesma limitação do Plan 12) — verificação via `npx tsc --noEmit -p tsconfig.app.json` a cada task e roteiro manual (criar cliente em lote com nome, editar cliente, listar, cliente duplicado no lote) ao final.
