# Cadastro Unificado de Cliente no Front Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A aba "Clientes" do `ap-front` (`ClientTable`/`NewClientModal`/`EditClientModal`) passa a ler e gravar no `apps.cliente` do `ap-back-optin` via `DataContext`, em vez de CSV/mock — tornando-o o cadastro único de cliente usado pelo app inteiro (opt-in e o resto).

**Architecture:** `DataContext.clients` passa a ser carregado via `listClientes()` (não mais CSV/mock). `NewClientModal` (reusado em 9 lugares) mantém sua prop `onSave` com a mesma assinatura de sempre — só que agora persiste de verdade via `createCliente` antes de chamar `onSave`, então os outros 8 call sites continuam funcionando sem mudança. `DataContext.updateClient` separa campos de cadastro (vão pro `PATCH /clientes/{id}`) de campos financeiros (`totalLimit`/`usedLimit`/`availableLimit`/`collateralValue`, que ficam só locais — não existem no backend).

**Tech Stack:** React 18 + TypeScript + Vite. Sem framework de teste automatizado neste repo — verificação via `tsc --noEmit` (type-check) a cada task e um roteiro manual completo na última task.

**Spec:** `docs/superpowers/specs/2026-08-26-cadastro-unificado-cliente-design.md` (§3, §4)

**Depends on:** `2026-08-26-optin-plan-13-cliente-cadastro-completo.md` (endpoint `PATCH /clientes/{id}` e campos `status`/`atualizadoEm` precisam existir no backend).

## Global Constraints

- Sem framework de teste automatizado no front — cada task verifica com `npx tsc --noEmit -p tsconfig.app.json` (type-check real).
- Todo texto de UI em português, seguindo o padrão já usado nos componentes existentes.
- `receivables`/`contracts`/`contaCorrenteEntries`/`liquidationProblems` continuam vindo de CSV/mock — só `clients` muda de fonte.
- Campos financeiros do `Client` (`totalLimit`/`usedLimit`/`availableLimit`/`collateralValue`) nunca são enviados pro backend — ficam só em estado local.
- `NewClientModal.tsx` é usado em 9 lugares (`App.tsx` + 8 jornadas) — sua prop `onSave?: (clientData: NewClientData | NewClientData[]) => void` não muda de assinatura; só o comportamento interno do `handleSubmit` muda.

---

### Task 1: `optinApi.ts` — `status`/`atualizadoEm` em `ClienteDTO`, novo `updateCliente`

**Files:**
- Modify: `src/services/optinApi.ts`

**Interfaces:**
- Consumes: `PATCH /api/v1/clientes/{id}` (Plan 13).
- Produces: `ClienteDTO` ganha `status`/`atualizadoEm`; novo `updateCliente(id: string, payload: {nome?: string; email?: string; telefone?: string; status?: string}): Promise<ClienteDTO>` — usado pela Task 2.

- [ ] **Step 1: Atualizar `ClienteDTO` e adicionar `updateCliente`**

Em `src/services/optinApi.ts`, substitua a interface `ClienteDTO`:

```typescript
export interface ClienteDTO {
  id: string;
  documento: string;
  documentoTipo: string;
  nome: string;
  email?: string | null;
  telefone?: string | null;
  status: 'active' | 'inactive' | 'pending';
  criadoEm: string;
  atualizadoEm: string;
}
```

Adicione, logo depois de `createCliente`:

```typescript
export interface AtualizarClientePayload {
  nome?: string;
  email?: string;
  telefone?: string;
  status?: string;
}

export function updateCliente(id: string, payload: AtualizarClientePayload): Promise<ClienteDTO> {
  return request<ClienteDTO>('PATCH', `/clientes/${id}`, { body: payload });
}
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: nenhum erro relacionado a `src/services/optinApi.ts` (`updateCliente` ainda não é importado por ninguém).

- [ ] **Step 3: Commit**

```bash
git add src/services/optinApi.ts
git commit -m "feat: status/atualizadoEm em ClienteDTO; updateCliente no client HTTP"
```

---

### Task 2: `DataContext.tsx` — `clients` via API, `updateClient` com split cadastro/financeiro

**Files:**
- Modify: `src/context/DataContext.tsx`

**Interfaces:**
- Consumes: `listClientes`, `updateCliente`, `ClienteDTO` (Task 1).
- Produces: `DataContextValue.clients` agora vem da API; `updateClient(clientId: string, data: Partial<Client>): Promise<void>` (antes era síncrona) — usado pela Task 4 (`App.tsx`).

- [ ] **Step 1: Escrever a função de mapeamento `ClienteDTO → Client`**

Em `src/context/DataContext.tsx`, adicione o import e a função de mapeamento logo abaixo dos imports existentes:

```typescript
import type { Receivable, Contract, Client } from '../types';
import type { ContaCorrenteEntry } from '../types/contaCorrente';
import type { LiquidationProblemUr } from '../data/csvLoader';
import { loadDataFromCsv } from '../data/csvLoader';
import {
  mockReceivables,
  mockContracts,
} from '../data/mockData';
import { mockContaCorrenteEntries } from '../data/contaCorrenteMockData';
import { listClientes, updateCliente, type ClienteDTO } from '../services/optinApi';

function clienteDtoParaClient(dto: ClienteDTO): Client {
  return {
    id: dto.id,
    name: dto.nome,
    document: dto.documento,
    email: dto.email ?? '',
    phone: dto.telefone ?? '',
    status: dto.status,
    totalLimit: 0,
    usedLimit: 0,
    availableLimit: 0,
    collateralValue: 0,
    createdAt: new Date(dto.criadoEm),
    updatedAt: new Date(dto.atualizadoEm),
  };
}
```

Note que `mockClients` sai do import — não é mais usado neste arquivo.

- [ ] **Step 2: Trocar a fonte de `clients` em `load()`**

Substitua a função `load` inteira:

```typescript
const load = useCallback(async () => {
  setIsLoading(true);
  setError(null);
  try {
    const [data, clientesApi] = await Promise.all([
      loadDataFromCsv(),
      listClientes(),
    ]);
    setReceivables(data.receivables);
    setContracts(normalizeContracts(data.contracts));
    setClients(clientesApi.map(clienteDtoParaClient));
    setContaCorrenteEntries(data.contaCorrenteEntries);
    setLiquidationProblems(data.liquidationProblems);
    setUseCsv(true);
  } catch (e) {
    setError(e instanceof Error ? e.message : 'Erro ao carregar dados');
    setUseCsv(false);
    setReceivables(mockReceivables);
    setContracts(normalizeContracts(mockContracts));
    try {
      setClients((await listClientes()).map(clienteDtoParaClient));
    } catch {
      setClients([]);
    }
    setContaCorrenteEntries(mockContaCorrenteEntries);
    setLiquidationProblems([]);
  } finally {
    setIsLoading(false);
  }
}, []);
```

O `catch` externo (hoje só cobre falha de CSV) ganha um `try/catch` interno específico pra `listClientes()` — se o CSV falhar mas a API de clientes estiver de pé, `clients` ainda carrega certo; se a API também falhar, `clients` fica `[]` (sem fallback pra `mockClients`, que não é mais importado).

- [ ] **Step 3: Trocar a implementação de `updateClient`**

Substitua a declaração de `useState<Client[]>` (que hoje inicializa com `mockClients`):

```typescript
const [clients, setClients] = useState<Client[]>([]);
```

Substitua `updateClient` e a assinatura em `DataContextValue`:

```typescript
export interface DataContextValue {
  receivables: Receivable[];
  contracts: Contract[];
  clients: Client[];
  contaCorrenteEntries: ContaCorrenteEntry[];
  liquidationProblems: LiquidationProblemUr[];
  isLoading: boolean;
  error: string | null;
  useCsv: boolean;
  retry: () => void;
  updateClient: (clientId: string, data: Partial<Client>) => Promise<void>;
}
```

```typescript
const updateClient = useCallback(async (clientId: string, data: Partial<Client>) => {
  const payloadCadastro: { nome?: string; email?: string; telefone?: string; status?: string } = {};
  if (data.name !== undefined) payloadCadastro.nome = data.name;
  if (data.email !== undefined) payloadCadastro.email = data.email;
  if (data.phone !== undefined) payloadCadastro.telefone = data.phone;
  if (data.status !== undefined) payloadCadastro.status = data.status;

  let atualizadoViaApi: Partial<Client> = {};
  if (Object.keys(payloadCadastro).length > 0) {
    const dto = await updateCliente(clientId, payloadCadastro);
    atualizadoViaApi = clienteDtoParaClient(dto);
  }

  setClients(prev => prev.map(c => {
    if (c.id !== clientId) return c;
    return { ...c, ...atualizadoViaApi, ...data, updatedAt: new Date() };
  }));
}, []);
```

`{...atualizadoViaApi, ...data}` — nessa ordem, os campos financeiros de `data` (que `atualizadoViaApi` sempre zera, via `clienteDtoParaClient`) prevalecem por último, então nada financeiro é perdido; os campos de cadastro (`name`/`email`/`phone`/`status`) acabam com o mesmo valor de qualquer forma — `data` é a origem do que foi enviado no `PATCH`, `atualizadoViaApi` é a resposta do servidor ecoando exatamente isso de volta, então não há divergência entre eles pros campos que efetivamente mudaram. Quando `data` só tem campos financeiros (caso do `FinancialModule.tsx`, que nunca dispara o `PATCH`), `atualizadoViaApi` fica `{}` e o merge equivale ao comportamento local puro de hoje.

- [ ] **Step 4: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: sem erros novos em `DataContext.tsx`. `App.tsx` e `FinancialModule.tsx` vão ter um novo tipo de erro aqui (`updateClient` agora retorna `Promise<void>`, chamadores que tratavam como síncrono) — **normal, resolvido na Task 4**; não pare nesta task por causa disso, só confirme que o erro é exatamente esse e não outro.

- [ ] **Step 5: Commit**

```bash
git add src/context/DataContext.tsx
git commit -m "feat: DataContext.clients vem do apps.cliente; updateClient persiste cadastro via API"
```

---

### Task 3: `NewClientModal.tsx` — campo Nome por CNPJ, persistência real

**Files:**
- Modify: `src/components/NewClientModal.tsx`

**Interfaces:**
- Consumes: `createCliente`, `OptinApiError` (Task 1, já existentes desde o Plan 12).
- Produces: `NewClientModalProps` **não muda** (`onSave?: (clientData: NewClientData | NewClientData[]) => void`) — os outros 8 usos deste componente continuam funcionando sem alteração.

- [ ] **Step 1: Substituir o conteúdo inteiro do arquivo**

```tsx
import React, { useState } from 'react';
import { X, Plus } from 'lucide-react';
import { CNPJInput } from './MaskedInput';
import { useEscapeKey } from '../hooks/useKeyboardShortcuts';
import { createCliente, OptinApiError } from '../services/optinApi';

interface NewClientModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave?: (clientData: NewClientData | NewClientData[]) => void;
}

interface NewClientData {
  id: string;
  name: string;
  email: string;
  document: string;
  phone: string;
  address: string;
  city: string;
  state: string;
  zipCode: string;
  totalLimit: number;
  usedLimit: number;
  availableLimit: number;
  collateralValue: number;
  status: 'pending';
}

export const NewClientModal: React.FC<NewClientModalProps> = ({ isOpen, onClose, onSave }) => {
  const [cnpjList, setCnpjList] = useState<string[]>(['']);
  const [nameList, setNameList] = useState<string[]>(['']);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEscapeKey(() => {
    if (isOpen) onClose();
  });

  if (!isOpen) return null;

  const handleCnpjChange = (index: number, value: string) => {
    const newList = [...cnpjList];
    newList[index] = value;
    setCnpjList(newList);
    if (errors[`cnpj-${index}`]) {
      const newErrors = { ...errors };
      delete newErrors[`cnpj-${index}`];
      setErrors(newErrors);
    }
  };

  const handleNameChange = (index: number, value: string) => {
    const newList = [...nameList];
    newList[index] = value;
    setNameList(newList);
    if (errors[`nome-${index}`]) {
      const newErrors = { ...errors };
      delete newErrors[`nome-${index}`];
      setErrors(newErrors);
    }
  };

  const addCnpjField = () => {
    setCnpjList([...cnpjList, '']);
    setNameList([...nameList, '']);
  };

  const removeCnpjField = (index: number) => {
    if (cnpjList.length > 1) {
      setCnpjList(cnpjList.filter((_, i) => i !== index));
      setNameList(nameList.filter((_, i) => i !== index));
    }
  };

  const validateCNPJ = (cnpj: string): boolean => {
    const cleaned = cnpj.replace(/\D/g, '');
    if (cleaned.length !== 14) return false;
    if (/^(\d)\1{13}$/.test(cleaned)) return false;

    const weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
    const weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];

    let sum = 0;
    for (let i = 0; i < 12; i++) sum += parseInt(cleaned[i]) * weights1[i];
    let remainder = sum % 11;
    const digit1 = remainder < 2 ? 0 : 11 - remainder;
    if (parseInt(cleaned[12]) !== digit1) return false;

    sum = 0;
    for (let i = 0; i < 13; i++) sum += parseInt(cleaned[i]) * weights2[i];
    remainder = sum % 11;
    const digit2 = remainder < 2 ? 0 : 11 - remainder;
    if (parseInt(cleaned[13]) !== digit2) return false;

    return true;
  };

  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    cnpjList.forEach((cnpj, index) => {
      if (!cnpj.trim()) {
        newErrors[`cnpj-${index}`] = 'CNPJ é obrigatório';
      } else {
        const cleanDoc = cnpj.replace(/\D/g, '');
        if (cleanDoc.length !== 14) {
          newErrors[`cnpj-${index}`] = 'CNPJ deve ter 14 dígitos';
        } else if (!validateCNPJ(cleanDoc)) {
          newErrors[`cnpj-${index}`] = 'CNPJ inválido. Verifique os dígitos informados.';
        }
      }
    });

    nameList.forEach((nome, index) => {
      if (!nome.trim()) {
        newErrors[`nome-${index}`] = 'Nome é obrigatório';
      }
    });

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateForm()) {
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);

    const criados: NewClientData[] = [];
    let indiceFalha = -1;

    for (let i = 0; i < cnpjList.length; i++) {
      try {
        const cliente = await createCliente({
          documento: cnpjList[i].replace(/\D/g, ''),
          nome: nameList[i],
        });
        criados.push({
          id: cliente.id,
          name: cliente.nome,
          email: cliente.email ?? '',
          document: cliente.documento,
          phone: cliente.telefone ?? '',
          address: '',
          city: '',
          state: '',
          zipCode: '',
          totalLimit: 0,
          usedLimit: 0,
          availableLimit: 0,
          collateralValue: 0,
          status: 'pending',
        });
      } catch (err) {
        indiceFalha = i;
        const mensagem = err instanceof OptinApiError ? err.message : 'Erro desconhecido ao cadastrar cliente';
        setErrors(prev => ({ ...prev, [`cnpj-${i}`]: mensagem }));
        setSubmitError(`Falha ao cadastrar CNPJ ${i + 1}: ${mensagem}`);
        break;
      }
    }

    setIsSubmitting(false);

    if (indiceFalha === -1) {
      if (onSave) {
        onSave(criados.length === 1 ? criados[0] : criados);
      }
      handleClose();
      return;
    }

    setCnpjList(prev => prev.slice(indiceFalha));
    setNameList(prev => prev.slice(indiceFalha));
    setErrors(prev => {
      const reindexado: Record<string, string> = {};
      Object.entries(prev).forEach(([chave, valor]) => {
        const match = chave.match(/^(cnpj|nome)-(\d+)$/);
        if (!match) return;
        const indiceAntigo = Number(match[2]);
        if (indiceAntigo < indiceFalha) return;
        reindexado[`${match[1]}-${indiceAntigo - indiceFalha}`] = valor;
      });
      return reindexado;
    });
  };

  const handleClose = () => {
    setCnpjList(['']);
    setNameList(['']);
    setErrors({});
    setSubmitError(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <h2 className="text-xl font-bold text-gray-900">Novo Cliente</h2>
          <button
            onClick={handleClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-6">
          {submitError && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
              {submitError}
            </div>
          )}

          <div className="space-y-4">
            {cnpjList.map((cnpj, index) => (
              <div key={index} className="flex items-start space-x-2">
                <div className="flex-1">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    CNPJ {index + 1} <span className="text-red-500">*</span>
                  </label>
                  <CNPJInput
                    value={cnpj}
                    onChange={(e) => {
                      handleCnpjChange(index, e.target.value);
                    }}
                    placeholder="00.000.000/0000-00"
                    className={`w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent ${
                      errors[`cnpj-${index}`] ? 'border-red-500' : 'border-gray-300'
                    }`}
                    name={`cnpj-${index}`}
                  />
                  {errors[`cnpj-${index}`] && (
                    <p className="text-red-500 text-xs mt-1">{errors[`cnpj-${index}`]}</p>
                  )}
                </div>
                <div className="flex-1">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Nome {index + 1} <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={nameList[index] ?? ''}
                    onChange={(e) => handleNameChange(index, e.target.value)}
                    placeholder="Razão social"
                    className={`w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent ${
                      errors[`nome-${index}`] ? 'border-red-500' : 'border-gray-300'
                    }`}
                  />
                  {errors[`nome-${index}`] && (
                    <p className="text-red-500 text-xs mt-1">{errors[`nome-${index}`]}</p>
                  )}
                </div>
                {cnpjList.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeCnpjField(index)}
                    className="mt-7 text-red-600 hover:text-red-700 transition-colors"
                  >
                    <X className="w-5 h-5" />
                  </button>
                )}
              </div>
            ))}

            <button
              type="button"
              onClick={addCnpjField}
              className="flex items-center space-x-2 text-blue-600 hover:text-blue-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
              <span className="text-sm font-medium">Adicionar outro CNPJ</span>
            </button>
          </div>

          <div className="flex items-center justify-end space-x-3 pt-4 border-t border-gray-200">
            <button
              type="button"
              onClick={handleClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {isSubmitting ? 'Cadastrando...' : `Criar ${cnpjList.length > 1 ? `${cnpjList.length} Clientes` : 'Cliente'}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: sem erros novos em `NewClientModal.tsx`.

- [ ] **Step 3: Commit**

```bash
git add src/components/NewClientModal.tsx
git commit -m "feat: NewClientModal persiste clientes via apps.cliente (nome + CNPJ por linha)"
```

---

### Task 4: `App.tsx` — conectar `onSave`/`onCreated` ao reload e ao `updateClient` assíncrono

**Files:**
- Modify: `src/App.tsx`

**Interfaces:**
- Consumes: `DataContextValue.retry`, `DataContextValue.updateClient` (agora `Promise<void>`) (Task 2); `NewClientModal` persistindo de verdade (Task 3).
- Produces: nenhuma nova — só fecha o ciclo de UI.

- [ ] **Step 1: Expor `retry` do `useData()`**

Em `src/App.tsx`, localize (por volta da linha 79):

```typescript
  const { clients: appClients, contracts: appContractsData, isLoading: _dataLoading, updateClient } = useData();
```

Substitua por:

```typescript
  const { clients: appClients, contracts: appContractsData, isLoading: _dataLoading, updateClient, retry: reloadClients } = useData();
```

- [ ] **Step 2: Atualizar o `onSave` do `NewClientModal` da aba Clientes**

Localize (por volta da linha 520-527):

```typescript
        <NewClientModal
          isOpen={showNewClientModal}
          onClose={() => setShowNewClientModal(false)}
          onSave={(clientData) => {
            addToast('success', 'Cliente criado!', `${clientData.name} foi adicionado com sucesso`);
            setShowNewClientModal(false);
          }}
        />
```

Substitua por:

```typescript
        <NewClientModal
          isOpen={showNewClientModal}
          onClose={() => setShowNewClientModal(false)}
          onSave={(clientData) => {
            const nomes = Array.isArray(clientData) ? clientData.map(c => c.name).join(', ') : clientData.name;
            addToast('success', 'Cliente criado!', `${nomes} foi adicionado com sucesso`);
            reloadClients();
            setShowNewClientModal(false);
          }}
        />
```

(A persistência já aconteceu dentro do `NewClientModal` — Task 3 — antes de `onSave` ser chamado; aqui só recarregamos a lista e avisamos o usuário. Isso também corrige um erro de tipo pré-existente: `clientData.name` não existia quando `clientData` podia ser `NewClientData[]`.)

- [ ] **Step 3: Atualizar o `onSave` do `EditClientModal`**

Localize (por volta da linha 535-546):

```typescript
        {editingClient && (
          <EditClientModal
            isOpen={!!editingClient}
            onClose={() => setEditingClient(null)}
            client={editingClient}
            onSave={(clientId, data) => {
              updateClient(clientId, data);
              addToast('success', 'Cliente atualizado com sucesso!');
              setEditingClient(null);
            }}
          />
        )}
```

Substitua por:

```typescript
        {editingClient && (
          <EditClientModal
            isOpen={!!editingClient}
            onClose={() => setEditingClient(null)}
            client={editingClient}
            onSave={(clientId, data) => {
              updateClient(clientId, data)
                .then(() => {
                  addToast('success', 'Cliente atualizado com sucesso!');
                  setEditingClient(null);
                })
                .catch((err) => {
                  addToast('error', 'Erro ao atualizar cliente', err instanceof Error ? err.message : undefined);
                });
            }}
          />
        )}
```

- [ ] **Step 4: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: os erros de `updateClient`/`clientData.name` que apareceram na Task 2 (Step 4) desaparecem. `FinancialModule.tsx` não precisa de nenhuma mudança — ele chama `updateClient(...)` sem usar o retorno, o que continua válido com `Promise<void>` (a chamada de rede é pulada nesse caso, já que só manda campos financeiros — ver Task 2, Step 3).

- [ ] **Step 5: Commit**

```bash
git add src/App.tsx
git commit -m "feat: App.tsx recarrega clientes apos criar; EditClientModal aguarda persistencia"
```

---

### Task 5: Verificação manual ponta a ponta

**Files:** nenhum (task de verificação, sem código novo).

**Interfaces:** nenhuma nova.

- [ ] **Step 1: Subir backend e front**

Backend (`C:\DEV\ap\ap-back-optin\optin`): `python manage.py runserver`, confirme Plan 13 aplicado (colunas `status`/`atualizado_em` em `cliente`, `PATCH /clientes/{id}` respondendo).

Front (`C:\DEV\ap\ap-front`): `pnpm dev`, confirme `VITE_OPTIN_API_BASE_URL`/`VITE_OPTIN_DEV_JWT` no `.env` local.

- [ ] **Step 2: Roteiro manual**

Abra o front, vá na aba "Clientes" e verifique, nesta ordem:

1. **Listagem inicial**: a tabela carrega os clientes que já existirem no `apps.cliente` (pode estar vazia se o tenant dev não tiver nenhum ainda — nesse caso siga pro próximo passo).
2. **Criar 1 cliente**: clique em novo cliente, preencha um CNPJ válido + nome, salve. Espera: toast de sucesso, cliente aparece na tabela com o nome preenchido (não mais `= CNPJ`).
3. **Criar 2 clientes em lote**: adicione uma segunda linha (CNPJ + nome diferentes), salve os dois de uma vez. Espera: toast de sucesso citando os dois nomes, ambos aparecem na tabela.
4. **CNPJ duplicado no meio do lote**: tente cadastrar 2 CNPJs onde o segundo já existe (reuse um dos criados no passo 2/3). Espera: erro no formulário identificando qual CNPJ falhou (`já existe cliente cadastrado com esse documento`), a linha do CNPJ que **não** falhou desaparece do form (já foi criada), a que falhou continua editável.
5. **Editar cliente**: clique em editar um cliente existente, mude nome/email/telefone, salve. Espera: toast de sucesso, mudança reflete na tabela.
6. **Editar limite financeiro** (se a tela de edição de limite do `FinancialModule` estiver acessível): confirme que ainda funciona local (sem erro no console), já que não persiste no backend.

- [ ] **Step 3: Rodar a suíte de backend uma última vez**

No diretório do backend:

```bash
python -m pytest
```

Expected: todos os testes passam.
