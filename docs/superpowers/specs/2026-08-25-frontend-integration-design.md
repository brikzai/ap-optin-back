# Integração com o front (`ap-front`) — Design de implementação

> **Status:** aprovado em brainstorming, pronto para plano de implementação.
> **Fonte normativa:** `SPEC-01-optin-e-gestao.md` (§3-6) + endpoints já implementados em `apps/optin/views.py` (Plans 08/09). Este documento não redefine contrato de API — mapeia o que já existe para o consumo do front em `C:\DEV\ap\ap-front`.
> **Fora de escopo (decisão de brainstorming):** fluxo de assinatura digital do cliente (canvas de assinatura, `signature_token`, `document_url`) e autenticação real (login + emissão de JWT com `financiador_id`). Este documento assume um JWT de desenvolvimento fixo.

## 1. Contexto

`ap-front` é uma SPA React/Vite/TypeScript com módulos de gestão de contratos, clientes e opt-ins, hoje quase toda alimentada por dados mockados ou por uma instância própria do Supabase. O módulo de opt-in (`OptInModule.tsx`, `NewOptInModal.tsx`, `OptInDetailsModal.tsx`) modela um fluxo de **assinatura digital do cliente** (tabela Supabase `opt_in_requests`, status `pending_signature → pending_registry → signed/expired/cancelled`) que é conceitualmente diferente do que este backend implementa: aqui, "opt-in" é o **registro CERC de uma unidade recebível** (arranjos, credenciadoras, vigência), criado e confirmado **de forma síncrona** numa única chamada (`POST /api/v1/optins` já registra na CERC e devolve o status final).

`NewOptInModal.tsx` já monta boa parte dos campos certos (arranjos, credenciadoras, `tipoOperacao`, datas), mas grava direto no Supabase com nomes de campo que não correspondem ao contrato real da API interna — a integração não é só trocar a URL, é remapear o payload.

Adicionalmente, dois gaps de infraestrutura foram identificados e fazem parte deste plano:
- **CORS**: o backend não tem `django-cors-headers` nem qualquer middleware de CORS — uma SPA em outra origem não consegue chamar a API hoje.
- **`evidenciaAutorizacaoId`**: campo obrigatório na criação (`VAL008`), sem equivalente hoje no formulário do front (não há upload/storage de evidência dos dois lados — `validar_evidencia` só verifica presença, não formato). O front precisa de um campo novo para isso.

## 2. Contrato consumido (referência, já implementado no backend)

**`POST /api/v1/optins`** — corpo:
```json
{
  "usuarioFinalRecebedor": "12345678000199",
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
Headers: `Authorization: Bearer <jwt>`, `Idempotency-Key: <uuid>`. `referenciaExterna`, `cnpjSolicitante` e `cnpjFinanciador` são resolvidos pelo backend (tenant/JWT) — **não são enviados pelo front**. Resposta: o opt-in serializado (síncrono — já reflete `ATIVO`/`REJEITADO` após a chamada à CERC), ou erro 422/409/502 (ver §5).

**`GET /api/v1/optins?status=&usuarioFinalRecebedor=&origem=&carteira=&vigenteEm=&limit=`** → `{"dados": [ {...} ]}`.

**`GET /api/v1/optins/{id}`** → opt-in serializado, ou 404.

**`PATCH /api/v1/optins/{id}`** — corpo aceita `vigenciaFim`, `carteira`, `arranjos`, `credenciadoras`, `cnpjFinanciador` (todos opcionais, só os enviados mudam); `referenciaExterna`/`cnpjSolicitante` são imutáveis (422 se enviados). Exige `Idempotency-Key`. Só permitido quando o opt-in está `ATIVO` (409 caso contrário).

Formato de opt-in serializado (todas as respostas acima):
```json
{
  "id": "opt_...", "referenciaExterna": "...", "protocoloCerc": "P-1",
  "origem": "...", "status": "ATIVO",
  "cnpjSolicitante": "...", "cnpjFinanciador": "...",
  "usuarioFinalRecebedor": "...", "titular": "...",
  "dataAssinatura": "2026-08-10", "vigenciaInicio": "2026-08-11", "vigenciaFim": "2027-08-10",
  "carteira": null, "credenciadoras": ["99T"], "arranjos": ["VCC"],
  "criadoEm": "2026-08-25T10:00:00"
}
```
`status` ∈ `PENDENTE` (transitório, não deveria ser observado por uma criação síncrona bem-sucedida), `ATIVO`, `REJEITADO`, `FALHA_ENVIO`. Erros seguem `{"erro": "<codigo>", "mensagem": "<texto>"}`.

## 3. Backend — CORS

Adicionar `django-cors-headers` (novo pacote em `requirements.txt`). Em `config/settings.py`:
- `INSTALLED_APPS += ["corsheaders"]`.
- `MIDDLEWARE`: `"corsheaders.middleware.CorsMiddleware"` inserido **antes** de `CommonMiddleware`.
- `CORS_ALLOWED_ORIGINS = get_secret("CORS_ALLOWED_ORIGINS", "").split(",")` (reaproveita `shared/secrets.py`, mesma dualidade env-var/Secret-Manager já usada em todo o resto de `settings.py`); dev local: `CORS_ALLOWED_ORIGINS=http://localhost:5173` no `.env`. Sem `CORS_ALLOW_ALL_ORIGINS` — lista explícita mesmo em dev.

Teste novo em `config/tests/` (ou onde já existirem testes de settings/middleware): request com header `Origin: http://localhost:5173` para qualquer endpoint público (`health`) devolve `Access-Control-Allow-Origin: http://localhost:5173`.

## 4. Front — client HTTP (`src/services/optinApi.ts`, novo arquivo)

Módulo único, sem camada de hooks/cache (YAGNI — hoje só um componente lista, só um cria/edita; nenhuma necessidade de estado compartilhado).

- Lê `VITE_OPTIN_API_BASE_URL` e `VITE_OPTIN_DEV_JWT` de `import.meta.env` (novas entradas em `.env.example`, comentadas explicando que `VITE_OPTIN_DEV_JWT` é um token fixo de desenvolvimento, a ser substituído quando houver login real).
- Exporta:
  - `listOptins(filtros?: {status?, usuarioFinalRecebedor?, origem?, carteira?, vigenteEm?, limit?}): Promise<OptinDTO[]>`
  - `getOptin(id: string): Promise<OptinDTO>`
  - `createOptin(payload: CriarOptinPayload): Promise<OptinDTO>`
  - `updateOptin(id: string, payload: AtualizarOptinPayload): Promise<OptinDTO>`
- `createOptin`/`updateOptin` geram `Idempotency-Key` via `crypto.randomUUID()` e o enviam no header.
- Toda chamada injeta `Authorization: Bearer ${VITE_OPTIN_DEV_JWT}` e `Content-Type: application/json`.
- Em resposta não-2xx, lança `OptinApiError extends Error` com `codigo`, `mensagem` (do corpo `{erro, mensagem}` quando presente) e `status` HTTP — os componentes decidem o texto do toast a partir disso.
- Tipos `OptinDTO`, `CriarOptinPayload`, `AtualizarOptinPayload` ficam neste mesmo arquivo (ou `src/types/optin.ts` se preferir manter `types/` centralizado — decisão de implementação, não muda o contrato).

## 5. Front — componentes

**`NewOptInModal.tsx`**
- `handleSubmit` troca o `fetch` para Supabase por `optinApi.createOptin(payload)`, com o payload remapeado para os nomes reais (§2): `usuarioFinalRecebedor` (documento do cliente selecionado), `titular` (= `formData.documentoTitular` ou o próprio documento do cliente se vazio), `dataAssinatura`/`vigenciaInicio`/`vigenciaFim` (de `formData`), `carteira`, `credenciadoras`/`arranjos` (já computados hoje via `todasCredenciadoras`/`todosArranjos`), `evidenciaAutorizacaoId` (campo novo, ver abaixo). **Não envia** `tipoOperacao`, `referenciaExterna`, `cnpjSolicitante`, `cnpjFinanciador`, `definicaoUnidadeRecebivel` — esses deixam de existir no payload (o backend resolve ou o usuário não escolhe: `tipoOperacao` some da UI, já que toda criação é implicitamente "C").
- Novo campo de formulário: **"ID da Evidência de Autorização"** (texto livre, obrigatório) — não há storage de evidência disponível hoje nos dois lados; é só um identificador que a operação informa manualmente (ex.: número do protocolo interno, nome do arquivo). Validação: apenas não-vazio, refletindo `VAL008`.
- Sucesso deixa de gerar link de assinatura — mostra o resultado direto: se voltou `ATIVO`, toast de sucesso com `protocoloCerc`; se a chamada falhar (`OptinApiError`), mostra `mensagem` num toast de erro (não fecha o modal, para o usuário poder corrigir e reenviar).
- Bloco de seleção/criação/import de cliente (Supabase `clients`) **não muda** — é cadastro de cliente, não opt-in.

**`OptInModule.tsx`**
- `loadOptIns` chama `optinApi.listOptins()` no lugar do mock.
- `OptInClient` (interface local) é substituído por `OptinDTO` (importado de `optinApi.ts`).
- Abas "Ativos"/"Inativos": ativos = `status === 'ATIVO'`; inativos = `status === 'REJEITADO' || status === 'FALHA_ENVIO'`. Filtro de status no `<select>` passa a listar `ATIVO`, `REJEITADO`, `FALHA_ENVIO` (remove `pending_signature`, `pending_registry`, `signed`, `expired`, `cancelled`).
- "Vence em breve" continua calculável a partir de `vigenciaFim` para opt-ins `ATIVO`.
- Coluna de ações: remove "Copiar Link" e "Encaminhar p/ Registradora" (sem equivalente sem o fluxo de assinatura). Mantém "Dados do Opt-In"; adiciona "Editar" quando `status === 'ATIVO'`, abrindo `OptInDetailsModal` já em modo de edição (ou um modal de edição dedicado — decisão de implementação; a spec só exige que o PATCH exista na UI).

**`OptInDetailsModal.tsx`**
- Exibe os campos reais do `OptinDTO`: `protocoloCerc`, `status`, `usuarioFinalRecebedor`, `titular`, `cnpjFinanciador`, `vigenciaInicio`/`vigenciaFim`, `carteira`, `arranjos`, `credenciadoras`, `criadoEm`. Remove blocos de "Link de Assinatura" e "Encaminhar p/ Registradora".
- Modo de edição (só quando `status === 'ATIVO'`): campos editáveis `vigenciaFim`, `carteira`, `arranjos`, `credenciadoras`, `cnpjFinanciador`; ao salvar, chama `optinApi.updateOptin(id, payloadComSomenteOsCamposAlterados)`.

## 6. Tratamento de erro (todos os componentes acima)

Mapeamento de `OptinApiError.status` → toast:
- `404` → "opt-in não encontrado" (pode acontecer em edição se removido em outra sessão).
- `409` → mensagem do backend (`OPTIN_NAO_ATIVO` na edição, ou opt-in equivalente já ativo na criação).
- `422` → mensagem do backend (validação de campo ou rejeição pela CERC).
- `502` → "CERC indisponível, tente novamente" (`CERC_INDISPONIVEL`).
- outros/erro de rede → toast genérico de erro.

## 7. Testes

- **Backend**: teste de CORS (§3). Nenhuma outra lógica de backend muda neste plano.
- **Front**: sem framework de teste automatizado configurado em `ap-front` hoje — verificação manual, rodando `pnpm dev` (front) + `python manage.py runserver` (backend) com o tenant de dev configurado e um JWT de desenvolvimento fixo (gerado com a mesma chave/claims que os testes de backend usam). Roteiro: listar (vazio e com dados), criar com sucesso, criar com rejeição CERC (422), criar com evidência ausente, editar um opt-in ativo, editar um opt-in inexistente (404).

## 8. Riscos e pendências

- Token de desenvolvimento fixo no front (`VITE_OPTIN_DEV_JWT`) não expira de forma gerenciada — é um artefato temporário até existir login real; não deve ir para nenhum ambiente além de dev local.
- `evidenciaAutorizacaoId` como texto livre sem storage é uma solução mínima aceita para não bloquear esta integração — se a CERC ou auditoria exigir evidência real (documento anexado), isso é um projeto próprio (upload + storage), fora deste escopo.
- Nenhuma tela do front cobre opt-out (`POST /api/v1/optins/{id}/optout` ou equivalente, se existir) — fora de escopo, não investigado aqui.
