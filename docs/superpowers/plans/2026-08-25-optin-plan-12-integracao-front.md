# Integração com o Front (ap-front) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O módulo de opt-in do `ap-front` (`C:\DEV\ap\ap-front`) passa a falar com a API real do `ap-back-optin` (criar/listar/detalhar/editar opt-in, cadastrar/listar cliente) em vez de gravar direto no Supabase.

**Architecture:** Um client HTTP fino (`src/services/optinApi.ts`, sem camada de hooks/cache — YAGNI, só um componente lista, só um cria/edita) injeta `Authorization: Bearer <JWT de dev fixo>` e `Idempotency-Key` nas mutações de opt-in. Três componentes de UI passam a consumir esse client: `NewOptInModal.tsx` (criar), `OptInModule.tsx` (listar), e um novo `OptInRegistroModal.tsx` (detalhar/editar). Um novo `NewClienteOptinModal.tsx`, mínimo, substitui o modal genérico de cliente **só dentro do fluxo de opt-in**.

**Tech Stack:** React 18 + TypeScript + Vite. Sem framework de teste automatizado neste repo — verificação via `tsc --noEmit` (type-check) a cada task e um roteiro manual completo na Task 6.

**Spec:** `docs/superpowers/specs/2026-08-25-frontend-integration-design.md` (§5, §6)

**Depends on:** `2026-08-25-optin-plan-10-cliente-entidade.md` e `2026-08-25-optin-plan-11-optin-referencia-cliente.md` (endpoints e CORS do backend precisam estar no ar).

## Global Constraints

- Sem framework de teste automatizado no front — cada task verifica com `npx tsc --noEmit -p tsconfig.app.json` (type-check real, diferente de `pnpm build`, que usa esbuild e não valida tipos).
- Todo texto de UI em português, seguindo o padrão já usado nos componentes existentes.
- `import.meta.env.VITE_*` — variáveis novas vão em `.env.example` (documentado) e no `.env` local (gitignored, não versionar segredos).
- **Achado importante, não estava no design original:** `OptInDetailsModal.tsx` (o componente já existente) é usado por **dois** lugares — `OptInModule.tsx` (opt-in real) e `ScheduleView.tsx` (um preview decorativo, com dados mockados inline, de outro módulo, sem relação com a integração). Modificar `OptInDetailsModal.tsx` no lugar quebraria `ScheduleView.tsx`. Por isso este plano cria um componente novo, `OptInRegistroModal.tsx`, para o fluxo de opt-in real, e **não toca em `OptInDetailsModal.tsx`**.
- `NewOptInModalProps` (`{isOpen, onClose, onSuccess}`) não muda — `ScheduleView.tsx` também usa `NewOptInModal` e depende dessa assinatura continuar igual.

---

### Task 1: `src/services/optinApi.ts` + variáveis de ambiente

**Files:**
- Create: `src/services/optinApi.ts`
- Modify: `.env.example`
- Modify: `.env` (local, gitignored)

**Interfaces:**
- Consumes: nada (task fundacional).
- Produces (usado por todas as tasks seguintes):
  - `class OptinApiError extends Error { codigo: string; status: number }`
  - `interface ClienteDTO { id, documento, documentoTipo, nome, email?, telefone?, criadoEm }`
  - `interface OptinDTO { id, referenciaExterna, protocoloCerc, origem, status: 'PENDENTE'|'ATIVO'|'REJEITADO'|'FALHA_ENVIO', clienteId, clienteNome, cnpjSolicitante, cnpjFinanciador, usuarioFinalRecebedor, titular, dataAssinatura, vigenciaInicio, vigenciaFim, carteira, credenciadoras: string[], arranjos: string[], criadoEm }`
  - `listOptins(filtros?: {status?, origem?, carteira?, vigenteEm?, limit?}): Promise<OptinDTO[]>`
  - `getOptin(id: string): Promise<OptinDTO>`
  - `createOptin(payload: CriarOptinPayload): Promise<OptinDTO>`
  - `updateOptin(id: string, payload: AtualizarOptinPayload): Promise<OptinDTO>`
  - `listClientes(filtros?: {documento?, limit?}): Promise<ClienteDTO[]>`
  - `getCliente(id: string): Promise<ClienteDTO>`
  - `createCliente(payload: CriarClientePayload): Promise<ClienteDTO>`

- [ ] **Step 1: Adicionar as env vars novas**

Em `.env.example`, adicione (no fim do arquivo):

```
# API do ap-back-optin (registro CERC de opt-in). JWT de desenvolvimento
# fixo — placeholder até existir login real; nunca usar em produção.
VITE_OPTIN_API_BASE_URL=http://localhost:8000/api/v1
VITE_OPTIN_DEV_JWT=
```

No `.env` local (arquivo já existe, gitignored), adicione as mesmas duas linhas, com `VITE_OPTIN_DEV_JWT` preenchido com um JWT válido (assinado com a mesma chave privada/claims usadas nos testes do backend — `financiador_id: "12345678000199"`, `iss: "brikz-iam"` — gere um com o mesmo par de chaves configurado em `IAM_JWT_PUBLIC_KEY` no `.env` do backend).

- [ ] **Step 2: Escrever `src/services/optinApi.ts`**

```typescript
export class OptinApiError extends Error {
  codigo: string;
  status: number;

  constructor(codigo: string, mensagem: string, status: number) {
    super(mensagem);
    this.codigo = codigo;
    this.status = status;
  }
}

export interface ClienteDTO {
  id: string;
  documento: string;
  documentoTipo: string;
  nome: string;
  email?: string | null;
  telefone?: string | null;
  criadoEm: string;
}

export interface OptinDTO {
  id: string;
  referenciaExterna: string;
  protocoloCerc: string | null;
  origem: string;
  status: 'PENDENTE' | 'ATIVO' | 'REJEITADO' | 'FALHA_ENVIO';
  clienteId: string;
  clienteNome: string | null;
  cnpjSolicitante: string;
  cnpjFinanciador: string;
  usuarioFinalRecebedor: string;
  titular: string | null;
  dataAssinatura: string;
  vigenciaInicio: string;
  vigenciaFim: string;
  carteira: string | null;
  credenciadoras: string[];
  arranjos: string[];
  criadoEm: string;
}

export interface CriarOptinPayload {
  clienteId: string;
  titular?: string;
  dataAssinatura: string;
  vigenciaInicio: string;
  vigenciaFim: string;
  carteira?: string | null;
  evidenciaAutorizacaoId: string;
  credenciadoras: string[];
  arranjos: string[];
}

export interface AtualizarOptinPayload {
  vigenciaFim?: string;
  carteira?: string | null;
  arranjos?: string[];
  credenciadoras?: string[];
  cnpjFinanciador?: string;
}

export interface CriarClientePayload {
  documento: string;
  nome: string;
  email?: string;
  telefone?: string;
}

const BASE_URL = import.meta.env.VITE_OPTIN_API_BASE_URL as string;
const DEV_JWT = import.meta.env.VITE_OPTIN_DEV_JWT as string;

interface RequestOptions {
  body?: unknown;
  idempotent?: boolean;
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${DEV_JWT}`,
  };
  if (options.idempotent) {
    headers['Idempotency-Key'] = crypto.randomUUID();
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new OptinApiError(data?.erro ?? 'ERRO_DESCONHECIDO', data?.mensagem ?? 'erro desconhecido', response.status);
  }

  return data as T;
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

export function listOptins(filtros: { status?: string; origem?: string; carteira?: string; vigenteEm?: string; limit?: number } = {}): Promise<OptinDTO[]> {
  return request<{ dados: OptinDTO[] }>('GET', `/optins${buildQuery(filtros)}`).then(r => r.dados);
}

export function getOptin(id: string): Promise<OptinDTO> {
  return request<OptinDTO>('GET', `/optins/${id}`);
}

export function createOptin(payload: CriarOptinPayload): Promise<OptinDTO> {
  return request<OptinDTO>('POST', '/optins', { body: payload, idempotent: true });
}

export function updateOptin(id: string, payload: AtualizarOptinPayload): Promise<OptinDTO> {
  return request<OptinDTO>('PATCH', `/optins/${id}`, { body: payload, idempotent: true });
}

export function listClientes(filtros: { documento?: string; limit?: number } = {}): Promise<ClienteDTO[]> {
  return request<{ dados: ClienteDTO[] }>('GET', `/clientes${buildQuery(filtros)}`).then(r => r.dados);
}

export function getCliente(id: string): Promise<ClienteDTO> {
  return request<ClienteDTO>('GET', `/clientes/${id}`);
}

export function createCliente(payload: CriarClientePayload): Promise<ClienteDTO> {
  return request<ClienteDTO>('POST', '/clientes', { body: payload });
}
```

- [ ] **Step 3: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: nenhum erro relacionado a `src/services/optinApi.ts` (o arquivo ainda não é importado por ninguém, então não há erro possível fora dele mesmo).

- [ ] **Step 4: Commit**

```bash
git add src/services/optinApi.ts .env.example
git commit -m "feat: client HTTP para a API do ap-back-optin"
```

---

### Task 2: `NewClienteOptinModal.tsx` (novo)

**Files:**
- Create: `src/components/NewClienteOptinModal.tsx`

**Interfaces:**
- Consumes: `createCliente`, `OptinApiError` de `src/services/optinApi.ts` (Task 1); `CNPJInput` de `src/components/MaskedInput.tsx` (já existe); `showToast` de `src/hooks/useToast.tsx` (já existe).
- Produces: `<NewClienteOptinModal isOpen: boolean, onClose: () => void, onCreated: () => void />` — usado pela Task 4.

- [ ] **Step 1: Escrever o componente**

```tsx
import React, { useState } from 'react';
import { X, UserPlus, Loader2 } from 'lucide-react';
import { createCliente, OptinApiError } from '../services/optinApi';
import { CNPJInput } from './MaskedInput';
import { showToast } from '../hooks/useToast';

interface NewClienteOptinModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export const NewClienteOptinModal: React.FC<NewClienteOptinModalProps> = ({ isOpen, onClose, onCreated }) => {
  const [documento, setDocumento] = useState('');
  const [nome, setNome] = useState('');
  const [email, setEmail] = useState('');
  const [telefone, setTelefone] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const resetForm = () => {
    setDocumento('');
    setNome('');
    setEmail('');
    setTelefone('');
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError(null);

    try {
      const cliente = await createCliente({
        documento: documento.replace(/\D/g, ''),
        nome,
        email: email || undefined,
        telefone: telefone || undefined,
      });
      showToast('success', 'Cliente cadastrado!', cliente.nome);
      resetForm();
      onCreated();
      onClose();
    } catch (err) {
      if (err instanceof OptinApiError) {
        setError(err.message);
      } else {
        setError('Erro desconhecido ao cadastrar cliente');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-md w-full">
        <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="bg-blue-100 p-2 rounded-lg">
              <UserPlus className="w-5 h-5 text-blue-600" />
            </div>
            <h2 className="text-lg font-bold text-gray-900">Novo Cliente</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">CNPJ/CPF</label>
            <CNPJInput
              value={documento}
              onChange={(e) => setDocumento(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Nome</label>
            <input
              type="text"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              required
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Email <span className="text-gray-400 font-normal">(opcional)</span>
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Telefone <span className="text-gray-400 font-normal">(opcional)</span>
            </label>
            <input
              type="text"
              value={telefone}
              onChange={(e) => setTelefone(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          <div className="flex items-center justify-end space-x-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center space-x-2 disabled:opacity-50"
            >
              {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              <span>Cadastrar</span>
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
Expected: sem erros novos.

- [ ] **Step 3: Commit**

```bash
git add src/components/NewClienteOptinModal.tsx
git commit -m "feat: modal minimo de cadastro de cliente no fluxo de opt-in"
```

---

### Task 3: `OptInRegistroModal.tsx` (novo — substitui `OptInDetailsModal` só no fluxo de opt-in)

**Files:**
- Create: `src/components/OptInRegistroModal.tsx`

**Interfaces:**
- Consumes: `updateOptin`, `OptinApiError`, `OptinDTO` de `src/services/optinApi.ts` (Task 1); `showToast`.
- Produces: `<OptInRegistroModal isOpen: boolean, onClose: () => void, optin: OptinDTO | null, onUpdated: () => void />` — usado pela Task 6.

- [ ] **Step 1: Escrever o componente**

```tsx
import React, { useState } from 'react';
import { X, FileText, CheckCircle, Calendar, Hash, Building2, Edit2, Save, Loader2 } from 'lucide-react';
import { showToast } from '../hooks/useToast';
import { updateOptin, OptinApiError, type OptinDTO } from '../services/optinApi';

interface OptInRegistroModalProps {
  isOpen: boolean;
  onClose: () => void;
  optin: OptinDTO | null;
  onUpdated: () => void;
}

export const OptInRegistroModal: React.FC<OptInRegistroModalProps> = ({ isOpen, onClose, optin, onUpdated }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vigenciaFim, setVigenciaFim] = useState('');
  const [carteira, setCarteira] = useState('');

  if (!isOpen || !optin) return null;

  const formatDate = (dateString: string) => {
    return new Intl.DateTimeFormat('pt-BR', {
      day: '2-digit',
      month: 'long',
      year: 'numeric',
    }).format(new Date(dateString));
  };

  const getStatusColor = (status: OptinDTO['status']) => {
    switch (status) {
      case 'ATIVO': return 'bg-green-100 text-green-800 border-green-200';
      case 'REJEITADO': return 'bg-red-100 text-red-800 border-red-200';
      case 'FALHA_ENVIO': return 'bg-red-100 text-red-800 border-red-200';
      case 'PENDENTE': return 'bg-yellow-100 text-yellow-800 border-yellow-200';
      default: return 'bg-gray-100 text-gray-800 border-gray-200';
    }
  };

  const getStatusLabel = (status: OptinDTO['status']) => {
    switch (status) {
      case 'ATIVO': return 'Ativo';
      case 'REJEITADO': return 'Rejeitado pela CERC';
      case 'FALHA_ENVIO': return 'Falha no envio';
      case 'PENDENTE': return 'Pendente';
      default: return 'Desconhecido';
    }
  };

  const startEditing = () => {
    setVigenciaFim(optin.vigenciaFim);
    setCarteira(optin.carteira ?? '');
    setError(null);
    setIsEditing(true);
  };

  const handleSave = async () => {
    setIsSaving(true);
    setError(null);
    try {
      await updateOptin(optin.id, { vigenciaFim, carteira: carteira || null });
      showToast('success', 'Opt-in atualizado!');
      setIsEditing(false);
      onUpdated();
      onClose();
    } catch (err) {
      if (err instanceof OptinApiError) {
        setError(err.message);
      } else {
        setError('Erro desconhecido ao atualizar opt-in');
      }
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="bg-blue-100 p-2 rounded-lg">
              <FileText className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">Dados do Opt-In</h2>
              <p className="text-sm text-gray-600">Registro CERC da unidade recebível</p>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
            <X className="w-6 h-6" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
              {error}
            </div>
          )}

          <div className={`flex items-center justify-between p-4 rounded-lg border-2 ${getStatusColor(optin.status)}`}>
            <div className="flex items-center space-x-3">
              <CheckCircle className="w-6 h-6" />
              <div>
                <p className="font-semibold">Status do Opt-In</p>
                <p className="text-sm">{getStatusLabel(optin.status)}</p>
              </div>
            </div>
            {optin.protocoloCerc && (
              <div className="text-right text-sm">
                <p className="font-medium">Protocolo CERC:</p>
                <p>{optin.protocoloCerc}</p>
              </div>
            )}
          </div>

          <div className="bg-gray-50 rounded-lg p-6 space-y-4">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Cliente</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-start space-x-3">
                <Building2 className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Nome</p>
                  <p className="text-base text-gray-900">{optin.clienteNome ?? '—'}</p>
                </div>
              </div>
              <div className="flex items-start space-x-3">
                <Hash className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Usuário Final Recebedor</p>
                  <p className="text-base text-gray-900">{optin.usuarioFinalRecebedor}</p>
                </div>
              </div>
              {optin.titular && (
                <div className="flex items-start space-x-3">
                  <Hash className="w-5 h-5 text-gray-400 mt-0.5" />
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-500">Titular</p>
                    <p className="text-base text-gray-900">{optin.titular}</p>
                  </div>
                </div>
              )}
              <div className="flex items-start space-x-3">
                <Hash className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">CNPJ Financiador</p>
                  <p className="text-base text-gray-900">{optin.cnpjFinanciador}</p>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-6 space-y-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Unidade Recebível</h3>
              {optin.status === 'ATIVO' && !isEditing && (
                <button onClick={startEditing} className="text-sm text-blue-600 hover:text-blue-700 flex items-center space-x-1">
                  <Edit2 className="w-4 h-4" />
                  <span>Editar</span>
                </button>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-start space-x-3">
                <Calendar className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Início da Vigência</p>
                  <p className="text-base text-gray-900">{formatDate(optin.vigenciaInicio)}</p>
                </div>
              </div>
              <div className="flex items-start space-x-3">
                <Calendar className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Fim da Vigência</p>
                  {isEditing ? (
                    <input
                      type="date"
                      value={vigenciaFim}
                      onChange={(e) => setVigenciaFim(e.target.value)}
                      className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
                    />
                  ) : (
                    <p className="text-base text-gray-900">{formatDate(optin.vigenciaFim)}</p>
                  )}
                </div>
              </div>
              <div className="flex items-start space-x-3 md:col-span-2">
                <Hash className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Carteira</p>
                  {isEditing ? (
                    <input
                      type="text"
                      value={carteira}
                      onChange={(e) => setCarteira(e.target.value)}
                      className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
                    />
                  ) : (
                    <p className="text-base text-gray-900">{optin.carteira ?? '—'}</p>
                  )}
                </div>
              </div>
              <div className="flex items-start space-x-3">
                <Hash className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Credenciadoras</p>
                  <p className="text-base text-gray-900">{optin.credenciadoras.join(', ')}</p>
                </div>
              </div>
              <div className="flex items-start space-x-3">
                <Hash className="w-5 h-5 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-500">Arranjos</p>
                  <p className="text-base text-gray-900">{optin.arranjos.join(', ')}</p>
                </div>
              </div>
            </div>
          </div>

          <div className="flex items-center justify-end space-x-3 pt-4 border-t border-gray-200">
            {isEditing ? (
              <>
                <button
                  onClick={() => setIsEditing(false)}
                  disabled={isSaving}
                  className="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-50"
                >
                  Cancelar
                </button>
                <button
                  onClick={handleSave}
                  disabled={isSaving}
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center space-x-2 disabled:opacity-50"
                >
                  {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  <span>Salvar</span>
                </button>
              </>
            ) : (
              <button onClick={onClose} className="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors">
                Fechar
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: sem erros novos.

- [ ] **Step 3: Commit**

```bash
git add src/components/OptInRegistroModal.tsx
git commit -m "feat: modal de detalhe/edicao do registro de opt-in (CERC)"
```

---

### Task 4: Reescrever `NewOptInModal.tsx`

**Files:**
- Modify: `src/components/NewOptInModal.tsx`

**Interfaces:**
- Consumes: `createOptin`, `listClientes`, `OptinApiError`, `ClienteDTO` (Task 1); `NewClienteOptinModal` (Task 2).
- Produces: `NewOptInModalProps` continua `{isOpen: boolean, onClose: () => void, onSuccess: () => void}` — **assinatura não muda** (`ScheduleView.tsx` também usa este componente e depende disso).

- [ ] **Step 1: Substituir o conteúdo inteiro do arquivo**

Substitua todo o conteúdo de `src/components/NewOptInModal.tsx` por:

```tsx
import React, { useState, useEffect } from 'react';
import { X, FileText, Send, Loader2, Search, Plus, ChevronRight } from 'lucide-react';
import { NewClienteOptinModal } from './NewClienteOptinModal';
import { showToast } from '../hooks/useToast';
import { createOptin, listClientes, OptinApiError, type ClienteDTO } from '../services/optinApi';

interface NewOptInModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

// Credenciadoras e arranjos de pagamento disponíveis para a definição da unidade recebível (CERC-AP004).
// "99T" no envio real significa "todas" — ver handleSubmit.
const CREDENCIADORAS = ['Cielo', 'Rede', 'Stone', 'GetNet', 'Dock'];
const ARRANJOS_PAGAMENTO = ['VISA', 'MASTERCARD', 'ELO', 'HIPERCARD'];

export const NewOptInModal: React.FC<NewOptInModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [step, setStep] = useState<'select-client' | 'opt-in-details'>('select-client');
  const [selectedClient, setSelectedClient] = useState<ClienteDTO | null>(null);
  const [clients, setClients] = useState<ClienteDTO[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [isLoadingClients, setIsLoadingClients] = useState(false);
  const [showNewClienteModal, setShowNewClienteModal] = useState(false);
  const [formData, setFormData] = useState({
    vigenciaFim: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    dataAssinatura: new Date().toISOString().split('T')[0],
    vigenciaInicio: new Date().toISOString().split('T')[0],
    carteira: '',
    documentoTitular: '',
    evidenciaAutorizacaoId: '',
  });
  const [todasCredenciadoras, setTodasCredenciadoras] = useState(true);
  const [credenciadorasSelecionadas, setCredenciadorasSelecionadas] = useState<string[]>([]);
  const [todosArranjos, setTodosArranjos] = useState(true);
  const [arranjosSelecionados, setArranjosSelecionados] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && step === 'select-client') {
      loadClients();
    }
  }, [isOpen, step]);

  const loadClients = async () => {
    setIsLoadingClients(true);
    try {
      const dados = await listClientes();
      setClients(dados);
    } catch (err) {
      console.error('Error loading clients:', err);
      showToast('error', 'Erro ao carregar clientes');
    } finally {
      setIsLoadingClients(false);
    }
  };

  const handleClienteCriado = () => {
    loadClients();
    setShowNewClienteModal(false);
  };

  const handleSelectClient = (client: ClienteDTO) => {
    setSelectedClient(client);
    setStep('opt-in-details');
  };

  const handleBack = () => {
    setStep('select-client');
    setSelectedClient(null);
  };

  if (!isOpen) return null;

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const toggleCredenciadora = (acquirer: string) => {
    setCredenciadorasSelecionadas(prev =>
      prev.includes(acquirer) ? prev.filter(a => a !== acquirer) : [...prev, acquirer]
    );
  };

  const toggleArranjo = (arranjo: string) => {
    setArranjosSelecionados(prev =>
      prev.includes(arranjo) ? prev.filter(a => a !== arranjo) : [...prev, arranjo]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedClient) {
      setError('Por favor, selecione um cliente');
      return;
    }

    if (!todasCredenciadoras && credenciadorasSelecionadas.length === 0) {
      setError('Selecione ao menos uma credenciadora ou marque "Todas as credenciadoras"');
      return;
    }

    if (!todosArranjos && arranjosSelecionados.length === 0) {
      setError('Selecione ao menos um arranjo de pagamento ou marque "Todos os arranjos de pagamento"');
      return;
    }

    if (!formData.evidenciaAutorizacaoId.trim()) {
      setError('Informe o ID da evidência de autorização');
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      const credenciadoras = todasCredenciadoras ? ['99T'] : credenciadorasSelecionadas;
      const arranjos = todosArranjos ? ['99T'] : arranjosSelecionados;

      const optin = await createOptin({
        clienteId: selectedClient.id,
        titular: formData.documentoTitular || undefined,
        dataAssinatura: formData.dataAssinatura,
        vigenciaInicio: formData.vigenciaInicio,
        vigenciaFim: formData.vigenciaFim,
        carteira: formData.carteira || null,
        evidenciaAutorizacaoId: formData.evidenciaAutorizacaoId,
        credenciadoras,
        arranjos,
      });

      showToast('success', 'Opt-in criado com sucesso!', `Protocolo CERC: ${optin.protocoloCerc}`);

      setFormData({
        vigenciaFim: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
        dataAssinatura: new Date().toISOString().split('T')[0],
        vigenciaInicio: new Date().toISOString().split('T')[0],
        carteira: '',
        documentoTitular: '',
        evidenciaAutorizacaoId: '',
      });
      setTodasCredenciadoras(true);
      setCredenciadorasSelecionadas([]);
      setTodosArranjos(true);
      setArranjosSelecionados([]);
      setStep('select-client');
      setSelectedClient(null);

      onSuccess();
      onClose();
    } catch (err) {
      if (err instanceof OptinApiError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Erro desconhecido');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const filteredClients = clients.filter(client =>
    client.nome.toLowerCase().includes(searchTerm.toLowerCase()) ||
    client.documento.includes(searchTerm)
  );

  return (
    <>
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
        <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
          <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="bg-blue-100 p-2 rounded-lg">
                <FileText className="w-6 h-6 text-blue-600" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900">Novo Opt-In</h2>
                <p className="text-sm text-gray-600">
                  {step === 'select-client' ? 'Selecione um cliente' : 'Dados do opt-in'}
                </p>
              </div>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
              <X className="w-6 h-6" />
            </button>
          </div>

          {step === 'select-client' ? (
            <div className="p-6 space-y-6">
              <div className="flex items-center space-x-3">
                <div className="relative flex-1">
                  <Search className="w-5 h-5 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Buscar por nome ou CNPJ..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => setShowNewClienteModal(true)}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center space-x-2 whitespace-nowrap"
                >
                  <Plus className="w-4 h-4" />
                  <span>Novo Cliente</span>
                </button>
              </div>

              {isLoadingClients ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
                </div>
              ) : filteredClients.length === 0 ? (
                <div className="text-center py-12">
                  <FileText className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                  <p className="text-gray-500 mb-4">Nenhum cliente encontrado</p>
                  <button
                    type="button"
                    onClick={() => setShowNewClienteModal(true)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center space-x-2 mx-auto"
                  >
                    <Plus className="w-4 h-4" />
                    <span>Cadastrar Cliente</span>
                  </button>
                </div>
              ) : (
                <div className="max-h-96 overflow-y-auto space-y-2">
                  {filteredClients.map((client) => (
                    <button
                      key={client.id}
                      type="button"
                      onClick={() => handleSelectClient(client)}
                      className="w-full p-4 border border-gray-200 rounded-lg hover:border-blue-500 hover:bg-blue-50 transition-colors text-left group"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="font-medium text-gray-900">{client.nome}</div>
                          <div className="text-sm text-gray-500">{client.documento}</div>
                          {client.email && (
                            <div className="text-xs text-gray-400">{client.email}</div>
                          )}
                        </div>
                        <ChevronRight className="w-5 h-5 text-gray-400 group-hover:text-blue-600" />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="p-6 space-y-6">
              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
                  {error}
                </div>
              )}

              {selectedClient && (
                <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-medium text-gray-700">Cliente Selecionado</h3>
                    <button type="button" onClick={handleBack} className="text-sm text-blue-600 hover:text-blue-700">
                      Alterar
                    </button>
                  </div>
                  <div className="text-sm">
                    <div className="font-medium text-gray-900">{selectedClient.nome}</div>
                    <div className="text-gray-600">{selectedClient.documento}</div>
                    {selectedClient.email && <div className="text-gray-500">{selectedClient.email}</div>}
                  </div>
                </div>
              )}

              <div className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Data de Assinatura do Opt-In
                    </label>
                    <input
                      type="date"
                      name="dataAssinatura"
                      value={formData.dataAssinatura}
                      onChange={handleInputChange}
                      required
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Carteira <span className="text-gray-400 font-normal">(opcional)</span>
                    </label>
                    <input
                      type="text"
                      name="carteira"
                      value={formData.carteira}
                      onChange={handleInputChange}
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Documento do Titular <span className="text-gray-400 font-normal">(opcional)</span>
                    </label>
                    <input
                      type="text"
                      name="documentoTitular"
                      value={formData.documentoTitular}
                      onChange={handleInputChange}
                      placeholder="CPF/CNPJ do titular da conta"
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      ID da Evidência de Autorização
                    </label>
                    <input
                      type="text"
                      name="evidenciaAutorizacaoId"
                      value={formData.evidenciaAutorizacaoId}
                      onChange={handleInputChange}
                      required
                      placeholder="Ex.: número do protocolo interno"
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                </div>

                <div className="border-t border-gray-200 pt-4">
                  <h4 className="text-sm font-semibold text-gray-900 mb-3">Definição da Unidade Recebível</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Início da Vigência
                      </label>
                      <input
                        type="date"
                        name="vigenciaInicio"
                        value={formData.vigenciaInicio}
                        onChange={handleInputChange}
                        required
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Fim da Vigência (Vencimento do Opt-In)
                      </label>
                      <input
                        type="date"
                        name="vigenciaFim"
                        value={formData.vigenciaFim}
                        onChange={handleInputChange}
                        required
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      />
                    </div>
                  </div>

                  <div className="mb-4">
                    <label className="flex items-center space-x-2 mb-2">
                      <input
                        type="checkbox"
                        checked={todasCredenciadoras}
                        onChange={(e) => setTodasCredenciadoras(e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="text-sm font-medium text-gray-700">Todas as credenciadoras (99T)</span>
                    </label>
                    {!todasCredenciadoras && (
                      <div className="flex flex-wrap gap-2 pl-6">
                        {CREDENCIADORAS.map((acquirer) => (
                          <label
                            key={acquirer}
                            className={`px-3 py-1.5 rounded-lg text-sm border cursor-pointer transition-colors ${
                              credenciadorasSelecionadas.includes(acquirer)
                                ? 'bg-blue-600 text-white border-blue-600'
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={credenciadorasSelecionadas.includes(acquirer)}
                              onChange={() => toggleCredenciadora(acquirer)}
                              className="sr-only"
                            />
                            {acquirer}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>

                  <div>
                    <label className="flex items-center space-x-2 mb-2">
                      <input
                        type="checkbox"
                        checked={todosArranjos}
                        onChange={(e) => setTodosArranjos(e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="text-sm font-medium text-gray-700">Todos os arranjos de pagamento (99T)</span>
                    </label>
                    {!todosArranjos && (
                      <div className="flex flex-wrap gap-2 pl-6">
                        {ARRANJOS_PAGAMENTO.map((arranjo) => (
                          <label
                            key={arranjo}
                            className={`px-3 py-1.5 rounded-lg text-sm border cursor-pointer transition-colors ${
                              arranjosSelecionados.includes(arranjo)
                                ? 'bg-blue-600 text-white border-blue-600'
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={arranjosSelecionados.includes(arranjo)}
                              onChange={() => toggleArranjo(arranjo)}
                              className="sr-only"
                            />
                            {arranjo}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <div className="flex items-start space-x-3">
                  <FileText className="w-5 h-5 text-blue-600 mt-0.5" />
                  <div className="flex-1">
                    <h4 className="text-sm font-semibold text-blue-900 mb-1">
                      Como funciona?
                    </h4>
                    <p className="text-sm text-blue-700">
                      Ao criar o opt-in, o registro é enviado imediatamente para a CERC. O resultado
                      (ativo ou rejeitado) aparece na tela em seguida.
                    </p>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-end space-x-3 pt-4 border-t border-gray-200">
                <button
                  type="button"
                  onClick={handleBack}
                  disabled={isSubmitting}
                  className="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-50"
                >
                  Voltar
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" />
                      <span>Criando...</span>
                    </>
                  ) : (
                    <>
                      <Send className="w-5 h-5" />
                      <span>Criar Opt-In</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>

      <NewClienteOptinModal
        isOpen={showNewClienteModal}
        onClose={() => setShowNewClienteModal(false)}
        onCreated={handleClienteCriado}
      />
    </>
  );
};
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: sem erros.

- [ ] **Step 3: Commit**

```bash
git add src/components/NewOptInModal.tsx
git commit -m "feat: NewOptInModal consome o ap-back-optin (clienteId, evidenciaAutorizacaoId)"
```

---

### Task 5: Reescrever `OptInModule.tsx`

**Files:**
- Modify: `src/components/OptInModule.tsx`

**Interfaces:**
- Consumes: `listOptins`, `OptinDTO` (Task 1); `NewOptInModal` (Task 4, props inalteradas); `OptInRegistroModal` (Task 3).
- Produces: `<OptInModule />` — sem props, igual a antes (`PartnerRegistrationModule.tsx`/`App.tsx` continuam chamando sem alteração).

- [ ] **Step 1: Substituir o conteúdo inteiro do arquivo**

```tsx
import React, { useState, useEffect } from 'react';
import {
  Search,
  FileText,
  CheckCircle,
  XCircle,
  Clock,
  AlertTriangle,
  Users,
  Calendar,
  Plus,
} from 'lucide-react';
import { NewOptInModal } from './NewOptInModal';
import { showToast } from '../hooks/useToast';
import { OptInRegistroModal } from './OptInRegistroModal';
import { listOptins, type OptinDTO } from '../services/optinApi';

export const OptInModule: React.FC = () => {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedOptin, setSelectedOptin] = useState<OptinDTO | null>(null);
  const [isNewOptInModalOpen, setIsNewOptInModalOpen] = useState(false);
  const [isDetailsModalOpen, setIsDetailsModalOpen] = useState(false);
  const [optins, setOptins] = useState<OptinDTO[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');

  useEffect(() => {
    loadOptIns();
  }, []);

  const loadOptIns = async () => {
    setIsLoading(true);
    try {
      const dados = await listOptins();
      setOptins(dados);
    } catch (err) {
      console.error('Error loading opt-ins:', err);
      showToast('error', 'Erro ao carregar opt-ins');
    } finally {
      setIsLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    return new Intl.DateTimeFormat('pt-BR', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric'
    }).format(new Date(dateString));
  };

  const getStatusColor = (status: OptinDTO['status']) => {
    switch (status) {
      case 'ATIVO': return 'bg-green-100 text-green-800';
      case 'REJEITADO': return 'bg-red-100 text-red-800';
      case 'FALHA_ENVIO': return 'bg-red-100 text-red-800';
      case 'PENDENTE': return 'bg-yellow-100 text-yellow-800';
      default: return 'bg-gray-100 text-gray-800';
    }
  };

  const getStatusLabel = (status: OptinDTO['status']) => {
    switch (status) {
      case 'ATIVO': return 'Ativo';
      case 'REJEITADO': return 'Rejeitado pela CERC';
      case 'FALHA_ENVIO': return 'Falha no envio';
      case 'PENDENTE': return 'Pendente';
      default: return 'Desconhecido';
    }
  };

  const getStatusIcon = (status: OptinDTO['status']) => {
    switch (status) {
      case 'ATIVO': return <CheckCircle className="w-4 h-4" />;
      case 'REJEITADO': return <XCircle className="w-4 h-4" />;
      case 'FALHA_ENVIO': return <XCircle className="w-4 h-4" />;
      case 'PENDENTE': return <Clock className="w-4 h-4" />;
      default: return <Clock className="w-4 h-4" />;
    }
  };

  const isExpiringSoon = (vigenciaFim: string) => {
    const now = new Date();
    const fim = new Date(vigenciaFim);
    const daysUntilExpiry = Math.ceil((fim.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
    return daysUntilExpiry <= 30 && daysUntilExpiry > 0;
  };

  const activeOptins = optins.filter(o => o.status === 'ATIVO');
  const inactiveOptins = optins.filter(o => o.status !== 'ATIVO');

  const currentOptins = activeTab === 'active' ? activeOptins : inactiveOptins;

  const filteredOptins = currentOptins.filter(optin => {
    const nomeCliente = optin.clienteNome ?? '';
    const matchesSearch = nomeCliente.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         optin.usuarioFinalRecebedor.includes(searchTerm);

    const matchesStatus = statusFilter === 'all' || optin.status === statusFilter;

    return matchesSearch && matchesStatus;
  });

  const handleViewDetails = (optin: OptinDTO) => {
    setSelectedOptin(optin);
    setIsDetailsModalOpen(true);
  };

  return (
    <div className="space-y-6">
      <NewOptInModal
        isOpen={isNewOptInModalOpen}
        onClose={() => setIsNewOptInModalOpen(false)}
        onSuccess={loadOptIns}
      />

      <OptInRegistroModal
        isOpen={isDetailsModalOpen}
        onClose={() => setIsDetailsModalOpen(false)}
        optin={selectedOptin}
        onUpdated={loadOptIns}
      />

      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        <div className="border-b border-gray-200">
          <div className="flex items-center justify-between px-5 pt-4">
            <div className="flex items-center space-x-1">
              <button
                onClick={() => setActiveTab('active')}
                className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === 'active'
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                Opt-Ins Ativos
                <span className={`ml-2 px-2 py-0.5 text-xs rounded-full ${
                  activeTab === 'active' ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-600'
                }`}>
                  {activeOptins.length}
                </span>
              </button>
              <button
                onClick={() => setActiveTab('inactive')}
                className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === 'inactive'
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                Opt-Ins Inativos
                <span className={`ml-2 px-2 py-0.5 text-xs rounded-full ${
                  activeTab === 'inactive' ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-600'
                }`}>
                  {inactiveOptins.length}
                </span>
              </button>
            </div>
            <div className="flex items-center space-x-3">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Buscar clientes..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-9 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent w-60 h-9 text-sm"
                />
              </div>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent h-9 text-sm"
              >
                <option value="all">Todos os status</option>
                <option value="ATIVO">Ativo</option>
                <option value="REJEITADO">Rejeitado pela CERC</option>
                <option value="FALHA_ENVIO">Falha no envio</option>
                <option value="PENDENTE">Pendente</option>
              </select>
              <button
                onClick={() => setIsNewOptInModalOpen(true)}
                className="px-5 py-2 h-9 bg-blue-600 text-white rounded-lg hover:bg-blue-700 active:bg-blue-800 transition-all shadow-sm hover:shadow-md flex items-center space-x-2 text-sm font-medium whitespace-nowrap"
              >
                <Plus className="w-4 h-4" />
                <span>Novo Opt-In</span>
              </button>
            </div>
          </div>
        </div>

        <div className="overflow-x-auto p-4">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Cliente
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Data do Cadastro
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Vencimento do OPTIN
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Ações
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {filteredOptins.map((optin) => {
                const expiringSoon = optin.status === 'ATIVO' && isExpiringSoon(optin.vigenciaFim);

                return (
                  <tr key={optin.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div>
                        <div className="text-sm font-medium text-gray-900">{optin.clienteNome ?? '—'}</div>
                        <div className="text-sm text-gray-500">{optin.usuarioFinalRecebedor}</div>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex items-center px-2 py-1 text-xs font-semibold rounded-full ${getStatusColor(optin.status)}`}>
                        {getStatusIcon(optin.status)}
                        <span className="ml-1">{getStatusLabel(optin.status)}</span>
                      </span>
                      {expiringSoon && (
                        <div className="mt-1">
                          <span className="inline-flex items-center px-2 py-1 text-xs font-semibold rounded-full bg-orange-100 text-orange-800">
                            <AlertTriangle className="w-3 h-3 mr-1" />
                            Vence em breve
                          </span>
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center text-sm text-gray-900">
                        <Calendar className="w-4 h-4 mr-2 text-gray-400" />
                        {formatDate(optin.criadoEm)}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center text-sm text-gray-900">
                        <Calendar className="w-4 h-4 mr-2 text-gray-400" />
                        {formatDate(optin.vigenciaFim)}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <button
                        onClick={() => handleViewDetails(optin)}
                        className="inline-flex items-center px-3 py-1 rounded-lg text-sm font-medium transition-colors bg-blue-600 text-white hover:bg-blue-700"
                      >
                        <FileText className="w-4 h-4 mr-1" />
                        Dados do Opt-In
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {!isLoading && filteredOptins.length === 0 && (
            <div className="text-center py-12">
              <Users className="w-12 h-12 text-gray-400 mx-auto mb-4" />
              <div className="text-gray-500 mb-2">Nenhum opt-in encontrado</div>
              <div className="text-sm text-gray-400">
                Tente ajustar os filtros ou termos de busca
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: sem erros.

- [ ] **Step 3: Commit**

```bash
git add src/components/OptInModule.tsx
git commit -m "feat: OptInModule lista opt-ins reais do ap-back-optin"
```

---

### Task 6: Verificação manual ponta a ponta

**Files:** nenhum (task de verificação, sem código novo).

**Interfaces:** nenhuma nova.

- [ ] **Step 1: Subir o backend**

No diretório `C:\DEV\ap\ap-back-optin\optin`, confirme que `.env` tem `CORS_ALLOWED_ORIGINS=http://localhost:5173` (Plan 10) e que os Plans 10 e 11 já foram aplicados (tabelas `cliente`/`optin.cliente_id` no Cloud SQL de dev). Rode:

```bash
python manage.py runserver
```

Expected: serve em `http://localhost:8000`.

- [ ] **Step 2: Subir o front**

No diretório `C:\DEV\ap\ap-front`, confirme que `.env` tem `VITE_OPTIN_API_BASE_URL=http://localhost:8000/api/v1` e `VITE_OPTIN_DEV_JWT` preenchido (Task 1). Rode:

```bash
pnpm dev
```

Expected: serve em `http://localhost:5173`.

- [ ] **Step 3: Roteiro manual**

Abra `http://localhost:5173`, navegue até o módulo de Opt-In (`PartnerRegistrationModule` → aba "opt-in", ou a rota `optin-control`/`opt-in` do `App.tsx`) e verifique, nesta ordem:

1. **Cadastrar cliente novo**: clique "Novo Opt-In" → "Novo Cliente", preencha CNPJ/nome, salve. Espera: toast de sucesso, cliente aparece na lista de seleção.
2. **Cadastrar cliente com documento duplicado**: repita o cadastro com o mesmo CNPJ. Espera: erro (`já existe cliente cadastrado com esse documento`) exibido no formulário, sem fechar o modal.
3. **Criar opt-in com sucesso**: selecione o cliente criado, preencha os campos (deixe "Todas as credenciadoras"/"Todos os arranjos" marcados — os arrays `CREDENCIADORAS`/`ARRANJOS_PAGAMENTO` da UI têm nomes de bandeira/marca, não códigos CERC reais, então valores individuais quebrariam a validação `VAL005` no backend; isso é uma limitação pré-existente do formulário, fora do escopo deste plano), preencha "ID da Evidência de Autorização" com qualquer texto, envie. Espera: toast de sucesso com o protocolo CERC, modal fecha, opt-in aparece na aba "Ativos".
4. **Criar opt-in com evidência ausente**: repita, deixando "ID da Evidência de Autorização" vazio. Espera: mensagem de erro no formulário, sem submeter.
5. **Editar um opt-in ativo**: clique "Dados do Opt-In" num opt-in `ATIVO`, clique "Editar", mude a "Fim da Vigência", salve. Espera: toast de sucesso, dado atualizado reaparece ao reabrir o modal.
6. **Listar/filtrar**: use a busca e o filtro de status na tabela; confirme que ambos restringem a lista corretamente.

- [ ] **Step 4: Confirmar que `ScheduleView.tsx` não quebrou**

Navegue até a tela que usa `ScheduleView` (schedule/liquidação) e abra o preview de opt-in que existe lá (mockado). Espera: continua funcionando como antes — nenhuma mudança deste plano deveria ter afetado esse fluxo (Task 3 criou `OptInRegistroModal.tsx` em vez de tocar em `OptInDetailsModal.tsx` exatamente para isso).

- [ ] **Step 5: Rodar a suíte de backend uma última vez**

No diretório do backend:

```bash
python -m pytest
```

Expected: todos os testes passam (nenhuma mudança de front deveria afetar isso, mas confirma que os Plans 10/11 continuam íntegros).
