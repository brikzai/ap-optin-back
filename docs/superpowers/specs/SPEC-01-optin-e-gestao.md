# SPEC 01 — Serviço de Opt-in e Gestão de Autorizações (CERC / Arranjos de Pagamento)

> **Status:** pronta para implementação
> **Público-alvo:** agente de código / squad de engenharia
> **Papel na cadeia CERC:** Financiador (IF ou Não Financeira)
> **Canal:** API REST síncrona CERC + Webhook assíncrono
> **Versão da API CERC:** 1.5 (`v15`)
> **Fonte normativa:** docs.cerc.com — Jornada de Arranjos de Pagamento › Financiador › Consulta de agenda

---

## 0. Como usar esta especificação

Esta spec descreve **um microserviço** (`optin-service`) que encapsula toda a jornada de autorização de acesso a agendas de recebíveis na CERC. Ela é auto-contida: contém contratos de API internos, mapeamento campo-a-campo para a CERC, máquina de estados, modelo de dados, catálogo de erros e critérios de aceite.

Ordem sugerida de implementação:

1. Camada de autenticação/token (§3)
2. Modelo de dados (§6)
3. Cliente CERC `POST /opt_in` e `POST /opt_out` (§4.1, §4.2)
4. Endpoints internos (§5)
5. Consulta de agenda + receptor de webhook (§4.3, §4.4)
6. Reconciliação e observabilidade (§9, §10)
7. Testes (§11)

---

## 1. Escopo

### 1.1 Dentro do escopo

| Capacidade | Interface CERC |
|---|---|
| Registrar opt-in (autorização de acesso à agenda) | `POST /opt_in` (CERC-AP004) |
| Atualizar opt-in existente | `POST /opt_in` com `tipoOperacao = A` |
| Encerrar opt-in (opt-out) | `POST /opt_out` (CERC-AP006) |
| Consultar agenda batch (dados já disponíveis) | `POST /v15/agenda/consultar?online=false` (CERC-AP005) |
| Consultar agenda online (assíncrona, interoperabilidade) | `POST /v15/agenda/consultar?online=true` + webhook `tipoEvento=agenda` |
| Gestão de ciclo de vida, vigência e reconciliação | interno |

### 1.2 Fora do escopo

- Registro de contratos e efeitos (→ **SPEC 02**).
- Opt-in *por força de contrato*: é criado automaticamente pela CERC ao registrar o contrato e **não pode** ser encerrado via opt-out — só com a baixa/inativação do contrato. Este serviço apenas **reflete** esses opt-ins como registros somente-leitura (`origem = CONTRATO`).
- Portal Único de Contestações (apenas persistir/expor eventos recebidos).
- Integração por arquivo (SFTP/AP004/AP005/AP006). A arquitetura deve manter o *port* `CercOptInGateway` para permitir um adaptador de arquivo no futuro, mas apenas o adaptador REST é implementado agora.

---

## 2. Conceitos e regras de negócio (fonte: CERC)

### 2.1 Tipos de opt-in

| Tipo | Como nasce | Como termina | Gerido por este serviço |
|---|---|---|---|
| **Por força de opt-in** | Registro voluntário do financiador (`POST /opt_in`) | `POST /opt_out` a qualquer momento, ou fim de vigência | **Sim** |
| **Por força de contrato** | Automático com o registro do contrato | Somente com baixa/inativação do contrato | Não (somente leitura) |

### 2.2 Regras normativas obrigatórias

- **R1 — Vigência.** `dataInicio`/`dataFim` definem o intervalo em que o financiador recebe **todos os recebíveis ativos** das agendas selecionadas — **não** apenas as URs geradas entre essas datas. Não interpretar como filtro de emissão.
- **R2 — Data de assinatura.** `dataInicio` **não pode** ser menor que `dataAssinaturaOptIn` (erro `104806`).
- **R3 — Ordem de datas.** `dataFim >= dataInicio` (erro `104022`).
- **R4 — CNPJ raiz.** É permitido informar CNPJ **raiz** (8 dígitos) em `documentoUsuarioFinalRecebedor` / `documentoTitular`, o que estende a autorização à matriz **e a todas as filiais**. Ao usar raiz, o sistema deve registrar a declaração de que o financiador possui autorização para todo o grupo (trilha de auditoria — §8).
- **R5 — Notificação e contestação.** Após o registro, a credenciadora é notificada e informa o EC. O EC pode abrir contestação. O serviço deve manter o opt-in em estado consultável e registrar contestações recebidas.
- **R6 — Opt-out restrito.** `POST /opt_out` só é válido para opt-ins por força de opt-in. Tentativa sobre opt-in por força de contrato → rejeitar **antes** de chamar a CERC (regra local), evitando `106802`.
- **R7 — Retenção.** Evidência da autorização do UFR (documento/termo assinado) e metadados devem ser retidos por, no mínimo, **5 anos**.
- **R8 — Carteira.** Se a empresa for do tipo "Prestador de Serviço", `carteira` é **obrigatória** e atualizável. Caso contrário, quando omitida, a CERC usa a carteira padrão do participante.

### 2.3 Curingas

- `listaCnpjCredenciadora = ["99T"]` → todas as credenciadoras/subcredenciadoras.
- `listaCodigoArranjoPagamento = ["99T"]` → todos os arranjos.
- Domínio de arranjos aceito (v1.5): `ACC, BCC, BCD, CBC, ECC, ECD, GCC, HCC, JCC, MCC, MCD, OCD, SCC, SCD, VCC, VCD, VDC, HCD, SIC, BRS, MAC, CUP, CZC, FRC, MXC, SFC, TKC, BNC, CCD, BRC, SPC, CSC, DAC, DCC, AGC, AUC, RCC, AVC, DBC, 99T`.
  A lista é **versionada pela CERC**; carregar de tabela de domínio persistida e sincronizável, nunca hardcode em enum de compilação.

---

## 3. Autenticação e ambientes

OAuth 2.0 **Client Credentials**.

| Ambiente | Host de token | Host de API AP |
|---|---|---|
| Homologação | `https://api.int.cerc.com` | conforme credencial (ex.: `https://ap-homolog.cerc.inf.br`) |
| Produção | `https://api.prd.cerc.com` | conforme credencial |

```
POST {tokenHost}/oauth/token
Authorization: Basic base64(clientId:clientSecret)
Content-Type: multipart/form-data
grant_type=client_credentials
```

Resposta:

```json
{ "access_token": "...", "token_type": "bearer", "expires_in": 35081, "scope": "resource-server-read resource-server-write" }
```

**Requisitos de implementação**

- `TokenProvider` com cache em memória + renovação proativa a **80 % de `expires_in`**.
- Renovação com *single-flight* (uma única requisição concorrente por tenant/ambiente).
- Em `401`, invalidar o cache e repetir a chamada **uma única vez**.
- `client_secret` em secret manager; **nunca** em log, banco ou variável de ambiente em texto plano no repositório.
- Todas as chamadas: `Authorization: Bearer {access_token}`.

---

## 4. Contratos com a CERC (upstream)

### 4.1 `POST /opt_in` — registrar/atualizar opt-in (CERC-AP004)

**Body:** array de objetos (lote). Recomendado: **lotes de até 500 itens**, configurável.

```jsonc
[
  {
    "tipoOperacao": "C",                    // C = Criar, A = Atualizar   [obrigatório]
    "referenciaExterna": "OPTIN-2026-000001", // único, não atualizável   [obrigatório]
    "cnpjSolicitante": "12345678000199",    // não atualizável            [obrigatório]
    "cnpjFinanciador": "12345678000199",    //                            [obrigatório]
    "dataAssinaturaOptIn": "2026-08-10",    // AAAA-MM-DD                 [obrigatório]
    "carteira": "CARTEIRA-01",              // opcional (obrigatório p/ Prestador de Serviço)
    "protocolo": "a0439fea-...",            // obrigatório quando tipoOperacao = A
    "definicaoUnidadeRecebivel": {
      "listaCnpjCredenciadora": ["99T"],           // [obrigatório]
      "listaCodigoArranjoPagamento": ["VCC","MCC"],// [obrigatório]
      "documentoUsuarioFinalRecebedor": "22751826000125", // completo ou raiz
      "documentoTitular": "22751826000125",              // completo ou raiz
      "dataInicio": "2026-08-11",                  // [obrigatório]
      "dataFim": "2027-08-10"                      // [obrigatório]
    }
  }
]
```

**Resposta 207** (array, um item por entrada enviada):

```jsonc
[
  {
    "protocolo": "a0439fea-ac6e-4f03-a72e-1167999dcec5",
    "referenciaExterna": "OPTIN-2026-000001",
    "dataHoraProcessamento": "2026-08-17T12:00:00.000Z",
    "status": "0",          // 0 = sucesso, 1 = erro
    "erros": []
  }
]
```

> **Atenção:** `207` é *multi-status*. **Nunca** tratar o HTTP 207 como sucesso global — iterar item a item por `status` e correlacionar por `referenciaExterna`.

**Resposta 400:** `RespostaPadrao` com `erros[]` (catálogo em §7.1).

**Regras de mapeamento**

- Documentos: **sem formatação**, zero-padding à esquerda — 14 dígitos (CNPJ), 11 dígitos (CPF), 8 dígitos (CNPJ raiz).
- `protocolo` retornado é a **chave da CERC** para o opt-in; é o único identificador aceito no opt-out. Persistir de forma obrigatória e indexada.
- `referenciaExterna` é gerada por nós, imutável, única no participante. Formato recomendado: `OPTIN-{YYYY}-{seq:09d}`.

### 4.2 `POST /opt_out` — encerrar opt-in (CERC-AP006)

```jsonc
[
  {
    "referenciaExterna": "OPTOUT-2026-000001",  // único, nosso           [obrigatório]
    "protocoloOptIn": "a0439fea-ac6e-...",      // protocolo do opt-in    [obrigatório]
    "cnpjSolicitante": "12345678000199",        //                        [obrigatório]
    "carteira": "CARTEIRA-01"                   // opcional
  }
]
```

Resposta idêntica em forma a §4.1 (`207` multi-status / `400`). Erros em §7.2.

### 4.3 `POST /v15/agenda/consultar` — consulta de agenda (CERC-AP005)

> **Movido para a SPEC 03.** Esta seção permanece como resumo; a especificação completa da consulta de agenda (batch, online, webhook, arquivo AP005/A/B, compliance de opt-in instantâneo) está em **SPEC 03 — Consulta de Agenda**.

Query param `online` (boolean, opcional):

- `online=false` (ou ausente) → **batch**: retorna sincronamente o último snapshot conhecido de cada agenda.
- `online=true` → dispara pedido de agenda online a cada registradora envolvida; URs da CERC voltam no *response* síncrono, URs da **interoperabilidade** chegam via **webhook** (`tipoEvento = agenda`), **uma UR por requisição**.

**Request:**

```jsonc
{
  "listaCnpjCredenciadora": ["99T"],                 // [obrigatório]
  "documentoUsuarioFinalRecebedor": "22751826000125",// [obrigatório]
  "documentoTitular": "22751826000125",              // opcional
  "listaCodigoArranjoPagamento": ["99T"],            // [obrigatório]
  "dataInicio": "2026-08-17",                        // [obrigatório]
  "dataFim": "2026-11-17",                           // [obrigatório]
  "tipoAvaliacao": "avaliacao_agenda_basica_ap",     // opcional
  "participante": "12345678000199",                  // opcional
  "carteira": "CARTEIRA-01"                          // opcional
}
```

**Response 200:** array de agendas com `unidadesRecebiveis[]`, cada UR contendo:
`dataLiquidacao`, `constituicao` (1=Constituída, 2=A constituir), `valorConstituidoTotal`, `valorConstituidoAntecipacaoPre`, `valorBloqueado`, `valorLivre`, `valorTotalUR`, `dataHoraUltimaAtualizacao`, `pagamentos[]`, `titulares[]` (fração por titular).

> **Regra de cálculo (crítica):** `valorTotalUR` é a **base para cálculo de efeitos de contrato** e equivale à soma das frações de UR do mesmo usuário final recebedor, **independente do titular**. Não usar `valorConstituidoTotal` do titular como base de oneração.

`tipoAvaliacao` aceito nesta rota: `avaliacao_agenda_basica_ap`, `avaliacao_agenda_completa_ap`.

Erros: `105001` (nenhum registro para os filtros do opt-in/contrato), `105002` (UFR/titular não encontrado), `105003` (falha de comunicação com registradora), `105999`.

### 4.4 Webhook de entrada (CERC → nós)

Envelope comum a todos os eventos:

```jsonc
{
  "tipoEvento": "agenda",                    // ver tabela abaixo
  "dataHoraEvento": "2026-08-17T18:58:36.087Z", // RFC3339
  "evento": { /* payload específico do tipo */ }
}
```

Tipos relevantes: `agenda` (consulta online), `notificacao` (notificações ao participante), `testeCerc` (teste de conectividade). `contrato` e `efeitoContrato` são tratados na SPEC 02.

**Requisitos do endpoint receptor**

| Requisito | Valor |
|---|---|
| Método | `HTTPS POST` |
| Autenticação suportada pela CERC | OAuth 2.0 **ou** Basic (webhook v2.0) |
| Resposta esperada | HTTP **200–299** |
| Retentativas da CERC | até **5**; depois **não há mais tentativas** |
| Throughput mínimo suportado | **500 req/s** (salvo TPS negociado com a CERC) |

**Implementação obrigatória:** o handler deve ser **fino** — validar autenticação, gravar o evento cru em tabela/fila (`webhook_inbox`) e responder `202` **em < 200 ms**. Todo processamento é assíncrono. Perder um evento é irreversível (sem 6ª tentativa), portanto persistir **antes** de processar.

**Idempotência:** deduplicar por `(tipoEvento, hash canônico do evento, dataHoraEvento)`. Reentrega deve ser inofensiva.

---

## 5. API interna do serviço (downstream)

Base: `/api/v1`. Autenticação interna: Bearer JWT do IdP corporativo. Todos os endpoints aceitam `Idempotency-Key` (obrigatório nos `POST` mutantes).

### 5.1 `POST /api/v1/optins`

Cria um opt-in por força de opt-in.

```jsonc
// request
{
  "usuarioFinalRecebedor": "22751826000125",   // CPF/CNPJ completo ou CNPJ raiz
  "titular": "22751826000125",                 // opcional; default = usuarioFinalRecebedor
  "credenciadoras": ["99T"],
  "arranjos": ["VCC", "MCC"],
  "vigenciaInicio": "2026-08-11",
  "vigenciaFim": "2027-08-10",
  "dataAssinatura": "2026-08-10",
  "carteira": "CARTEIRA-01",                   // opcional
  "evidenciaAutorizacaoId": "doc_01H...",       // id no storage de evidências [obrigatório]
  "metadata": { "clienteId": "..." }            // livre, indexável
}
```

```jsonc
// 201 Created
{
  "id": "opt_01J8ZK...",
  "referenciaExterna": "OPTIN-2026-000001",
  "status": "PENDENTE",
  "protocoloCerc": null,
  "criadoEm": "2026-08-17T09:00:00-03:00"
}
```

Erros: `422` (validação local, §7.3), `409` (opt-in ativo equivalente já existente — ver §5.6), `502` (CERC indisponível após retries).

### 5.2 `PATCH /api/v1/optins/{id}`

Atualiza um opt-in **ativo**. Campos atualizáveis: `cnpjFinanciador`, `dataAssinaturaOptIn`, `carteira` (quando aplicável) e o objeto `definicaoUnidadeRecebivel` inteiro.
**Não atualizáveis** (rejeitar `422` localmente, sem chamar a CERC): `referenciaExterna`, `cnpjSolicitante`.
Envia `tipoOperacao = "A"` com o `protocolo` original.

### 5.3 `POST /api/v1/optins/{id}/optout`

Encerra o opt-in. Pré-condições locais:

- `origem == "OPTIN"` (senão `409 OPT_OUT_NAO_APLICAVEL` — R6);
- `status in ("ATIVO", "ERRO_PARCIAL")`;
- `protocoloCerc != null`.

### 5.4 `GET /api/v1/optins` / `GET /api/v1/optins/{id}`

Filtros: `status`, `usuarioFinalRecebedor`, `credenciadora`, `arranjo`, `vigenteEm` (data), `origem`, `carteira`. Paginação por cursor (`limit` ≤ 200).

### 5.5 `POST /api/v1/agendas/consultas` *(implementado na SPEC 03)*

```jsonc
{ "modo": "ONLINE" | "BATCH", "usuarioFinalRecebedor": "...", "titular": "...",
  "credenciadoras": ["99T"], "arranjos": ["99T"],
  "dataInicio": "2026-08-17", "dataFim": "2026-11-17",
  "tipoAvaliacao": "avaliacao_agenda_basica_ap" }
```

- `BATCH` → `200` com o resultado completo.
- `ONLINE` → `202` com `{ "consultaId": "...", "status": "AGUARDANDO_WEBHOOK" }`; URs chegam por webhook e são agregadas em `consulta_agenda_ur`. `GET /api/v1/agendas/consultas/{consultaId}` devolve o consolidado e `completude` (parcial/ final por *quiet period* — §9.3).

### 5.6 Regra anti-duplicidade

Antes de chamar a CERC, rejeitar (`409`) quando existir opt-in `ATIVO` com **mesmo** `usuarioFinalRecebedor` + `titular` + conjunto de credenciadoras + conjunto de arranjos + **interseção de vigência**. Evita `104803 OPT-IN JA INFORMADO` e custo de chamada. A verificação de sobreposição deve considerar `99T` como conjunto universal.

---

## 6. Modelo de dados

```sql
-- Opt-in (agregado raiz)
CREATE TABLE optin (
  id                    TEXT PRIMARY KEY,                -- ULID
  referencia_externa    TEXT UNIQUE NOT NULL,            -- enviada à CERC, imutável
  protocolo_cerc        TEXT UNIQUE,                     -- GUID CERC; NULL até confirmação
  origem                TEXT NOT NULL,                   -- OPTIN | CONTRATO
  status                TEXT NOT NULL,                   -- ver §9.1
  cnpj_solicitante      TEXT NOT NULL,
  cnpj_financiador      TEXT NOT NULL,
  documento_ufr         TEXT NOT NULL,                   -- 8, 11 ou 14 dígitos
  documento_ufr_tipo    TEXT NOT NULL,                   -- CPF | CNPJ | CNPJ_RAIZ
  documento_titular     TEXT,
  data_assinatura       DATE NOT NULL,
  vigencia_inicio       DATE NOT NULL,
  vigencia_fim          DATE NOT NULL,
  carteira              TEXT,
  evidencia_id          TEXT NOT NULL,                   -- retenção 5 anos (R7)
  contrato_id           TEXT,                            -- preenchido se origem = CONTRATO
  criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (vigencia_fim >= vigencia_inicio),
  CHECK (vigencia_inicio >= data_assinatura)
);
CREATE INDEX ON optin (documento_ufr, status);
CREATE INDEX ON optin (vigencia_inicio, vigencia_fim);

CREATE TABLE optin_credenciadora (optin_id TEXT REFERENCES optin(id), cnpj TEXT,
  PRIMARY KEY (optin_id, cnpj));
CREATE TABLE optin_arranjo (optin_id TEXT REFERENCES optin(id), codigo TEXT,
  PRIMARY KEY (optin_id, codigo));

-- Opt-out
CREATE TABLE optout (
  id                 TEXT PRIMARY KEY,
  optin_id           TEXT NOT NULL REFERENCES optin(id),
  referencia_externa TEXT UNIQUE NOT NULL,
  protocolo_cerc     TEXT,
  status             TEXT NOT NULL,                      -- PENDENTE|CONFIRMADO|REJEITADO
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trilha completa de chamadas à CERC (auditoria e replay)
CREATE TABLE cerc_requisicao (
  id                 TEXT PRIMARY KEY,
  recurso            TEXT NOT NULL,                      -- opt_in | opt_out | agenda_consultar
  correlacao_id      TEXT NOT NULL,
  http_status        INT,
  request_body       JSONB NOT NULL,                     -- documentos mascarados em log, íntegros aqui
  response_body      JSONB,
  tentativa          INT NOT NULL DEFAULT 1,
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Inbox de webhooks (grava antes de processar)
CREATE TABLE webhook_inbox (
  id             TEXT PRIMARY KEY,
  tipo_evento    TEXT NOT NULL,
  data_hora_evento TIMESTAMPTZ NOT NULL,
  payload        JSONB NOT NULL,
  hash_dedupe    TEXT NOT NULL UNIQUE,
  processado_em  TIMESTAMPTZ,
  erro           TEXT,
  recebido_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Consulta de agenda online
CREATE TABLE consulta_agenda (
  id TEXT PRIMARY KEY, modo TEXT NOT NULL, filtros JSONB NOT NULL,
  status TEXT NOT NULL, iniciada_em TIMESTAMPTZ NOT NULL, encerrada_em TIMESTAMPTZ);
CREATE TABLE consulta_agenda_ur (
  consulta_id TEXT REFERENCES consulta_agenda(id),
  entidade_registradora TEXT, cnpj_credenciadora TEXT, documento_ufr TEXT,
  documento_titular TEXT, codigo_arranjo TEXT, data_liquidacao DATE,
  constituicao TEXT, valor_constituido_total NUMERIC(18,2),
  valor_constituido_antecipacao_pre NUMERIC(18,2), valor_bloqueado NUMERIC(18,2),
  valor_livre NUMERIC(18,2), valor_total_ur NUMERIC(18,2),
  data_hora_ultima_atualizacao TIMESTAMPTZ, pagamentos JSONB,
  PRIMARY KEY (consulta_id, cnpj_credenciadora, documento_ufr, documento_titular,
               codigo_arranjo, data_liquidacao));

-- Tabela de domínio sincronizável (arranjos vigentes)
CREATE TABLE dominio_arranjo (codigo TEXT PRIMARY KEY, descricao TEXT,
  ativo BOOLEAN NOT NULL DEFAULT true, atualizado_em TIMESTAMPTZ NOT NULL);
```

**Tipos monetários:** `NUMERIC(18,2)` no banco e `BigDecimal`/`decimal` na aplicação. **Proibido** `float`/`double` em qualquer ponto do fluxo de valores.

---

## 7. Catálogo de erros

### 7.1 `POST /opt_in` (prefixo 104)

| Código | Descrição | Tratamento |
|---|---|---|
| 104001 / 104002 | Tipo de operação obrigatório / inválido | Bug interno → alertar, não retentar |
| 104003 / 104005 | Referência externa obrigatória / inválida | Bug interno → alertar |
| 104004 | Referência externa já existe | Verificar idempotência: consultar registro local; se já confirmado, reconciliar |
| 104006–104009 | Solicitante / CNPJ financiador obrigatório ou inválido / não encontrado | Configuração → `502` + alerta operacional |
| 104010–104012 | Credenciadora obrigatória / inválida / não encontrada | `422` ao chamador |
| 104013 / 104014 | UFR obrigatório / documento obrigatório | `422` |
| 104015 / 104016 | Arranjo obrigatório / inválido | `422`; disparar sincronização da tabela de domínio |
| 104017 / 104018 | Data de assinatura obrigatória / inválida | `422` |
| 104019–104023 | Datas de início/fim obrigatórias, inválidas ou fora de ordem | `422` |
| 104024 | CPF/CNPJ do titular inválido | `422` |
| 104025 / 104026 | Protocolo obrigatório para o tipo de operação / não encontrado | Falha de atualização → reconciliar antes de reenviar |
| 104027 / 104028 | Carteira inválida / não encontrada | `422` + verificar cadastro |
| 104801 | Operação não permitida — acesso negado | Alerta de credencial/permissão; **não** retentar |
| 104802 | Operação inválida para o registro atual | Reconciliar estado local com a CERC |
| 104803 | Opt-in já informado | Tratar como sucesso idempotente após reconciliação (§5.6) |
| 104804 | Atualização de campos não permitidos / UFR não encontrado | `422` |
| 104805 | Titular não encontrado | `422` |
| 104806 | Data de início menor que data de assinatura | `422` (deve ser barrado localmente — R2) |
| 104807 | Falha no registro no ambiente de interoperabilidade | **Retentável** com backoff |
| 104808 | Erro genérico de validação | `422` + registrar payload para análise |
| 104901–104904 | Layout / grade horária / nome de arquivo | Só se aplica ao canal arquivo |
| 104999 | Erro inesperado | **Retentável** |

### 7.2 `POST /opt_out` (prefixo 106)

| Código | Descrição | Tratamento |
|---|---|---|
| 106001–106003 | Referência externa obrigatória / já existe / inválida | Bug interno ou idempotência |
| 106004–106006 | Protocolo do opt-in obrigatório / inválido / não encontrado | Reconciliar `protocolo_cerc` local |
| 106007 / 106008 | Solicitante obrigatório / CNPJ inválido | Configuração |
| 106801 | Acesso negado | Alerta; não retentar |
| 106802 | Operação inválida para o registro atual | Provável opt-in por força de contrato (R6) ou já encerrado |
| 106803 | Opt-out já informado | Sucesso idempotente |
| 106804 | Falha na interoperabilidade | **Retentável** |
| 106805 | Erro genérico de validação | `422` |
| 106999 | Erro inesperado | **Retentável** |

### 7.3 Validações locais (pré-CERC) — retornam `422`

`VAL001` documento com tamanho ≠ 8/11/14 dígitos · `VAL002` dígito verificador de CPF/CNPJ inválido · `VAL003` `vigenciaFim < vigenciaInicio` · `VAL004` `vigenciaInicio < dataAssinatura` · `VAL005` arranjo fora do domínio vigente · `VAL006` lista de credenciadoras vazia · `VAL007` mistura de `99T` com CNPJs específicos na mesma lista · `VAL008` `evidenciaAutorizacaoId` ausente ou inacessível · `VAL009` carteira ausente para participante do tipo Prestador de Serviço · `VAL010` opt-in equivalente já ativo (§5.6).

---

## 8. Auditoria, privacidade e retenção

- **Retenção 5 anos** (R7) para: evidência de autorização do UFR, `optin`, `optout`, `cerc_requisicao`.
- Log estruturado com **documentos mascarados** (`12345678****99`); documentos íntegros somente em colunas de banco com acesso controlado.
- Todo registro/alteração/encerramento de opt-in gera evento de auditoria com: ator, timestamp, IP/serviço de origem, `referenciaExterna`, `protocoloCerc`, diff de campos.
- Uso de **CNPJ raiz** (R4) grava evento de auditoria dedicado com a declaração de abrangência matriz + filiais.
- Contestações recebidas (via `notificacao`) são anexadas ao opt-in correspondente e expostas em `GET /api/v1/optins/{id}`.

---

## 9. Máquinas de estado e processos

### 9.1 Estados do opt-in

```
                 (POST /optins)
      ┌──────────────────────────────────┐
      ▼                                  │
  PENDENTE ──207 status=0──► ATIVO ──vigenciaFim < hoje──► EXPIRADO
      │                        │
      │                        ├── optout confirmado ──► ENCERRADO
      │                        └── PATCH ──► ATIVO (nova versão)
      │
      ├── 207 status=1 (erro definitivo) ──► REJEITADO
      └── falha de transporte após retries ──► FALHA_ENVIO ──(retry job)──► PENDENTE
```

- `PENDENTE`: enviado, aguardando confirmação/leitura do item no 207.
- `ATIVO`: `protocoloCerc` recebido, dentro da vigência.
- `EXPIRADO`: transição automática por job diário (00:15 America/Sao_Paulo).
- `ENCERRADO`: opt-out confirmado.
- `REJEITADO`: erro de negócio definitivo; requer nova solicitação (nova `referenciaExterna`).
- `FALHA_ENVIO`: erro de transporte/5xx/timeouts; elegível a reenvio automático.

### 9.2 Política de retentativa (chamadas a CERC)

- Retentável: timeout, erro de conexão, HTTP `5xx`, `429`, e os códigos marcados "Retentável" em §7.
- Backoff exponencial com jitter: `1s, 2s, 4s, 8s, 16s` (máx. 5 tentativas), depois `FALHA_ENVIO` + alerta.
- **Não retentável:** `400` com erros de validação, `104801`/`106801`.
- Toda retentativa reusa a **mesma** `referenciaExterna` (idempotência na CERC).

### 9.3 Consulta de agenda online — critério de completude

O webhook entrega **uma UR por requisição** e não há sinal de "fim". Regra:

- Marcar a consulta como `PARCIAL` assim que a primeira UR chegar.
- Fechar como `COMPLETA` após um *quiet period* configurável (default **90 s**) sem novas URs para o `consultaId`.
- *Hard timeout* de **15 min** → `COMPLETA_COM_TIMEOUT` + alerta.
- Sempre expor `dataHoraUltimaAtualizacao` por UR para o consumidor decidir frescor.

### 9.4 Reconciliação diária

Job diário deve:

1. Listar opt-ins `PENDENTE` há mais de 1 h e reconsultar/reenviar.
2. Expirar opt-ins vencidos.
3. Comparar a base local com os arquivos/relatórios de conciliação de opt-in da CERC (**AP022/AP023**, quando o canal de arquivo estiver habilitado) e registrar divergências em `divergencia_optin`.
4. Sincronizar `dominio_arranjo`.

---

## 10. Observabilidade

**Métricas (Prometheus):**

- `optin_requests_total{operacao,resultado}`
- `optin_cerc_latency_seconds{recurso}` (histograma; SLO p95 < 2 s)
- `optin_status_total{status}` (gauge por estado)
- `webhook_received_total{tipoEvento}` / `webhook_processing_lag_seconds`
- `cerc_token_refresh_total{resultado}`

**Alertas:**

| Condição | Severidade |
|---|---|
| `104801`/`106801` em qualquer volume | **crítico** (credencial/permissão) |
| Taxa de erro CERC > 5 % em 5 min | alto |
| Qualquer webhook respondido com status fora de 2xx | **crítico** (só há 5 tentativas) |
| `FALHA_ENVIO` > 0 por mais de 15 min | alto |
| Latência de token > 3 s | médio |

**Tracing:** propagar `correlacao_id` do request interno até a chamada CERC e até o processamento do webhook.

---

## 11. Critérios de aceite e testes

### 11.1 Testes unitários (obrigatórios)

- Normalização de documento: `"12.345.678/0001-99"` → `"12345678000199"`; CPF `"1234567890"` → `"01234567890"`; raiz `"12345678"` preservada com 8 dígitos.
- `VAL003`–`VAL004`: rejeição de `vigenciaFim < vigenciaInicio` e `vigenciaInicio < dataAssinatura`.
- Detecção de sobreposição com `99T` tratado como conjunto universal (§5.6).
- Parser de resposta 207 com itens mistos (`status` 0 e 1) → apenas os itens `1` viram erro.
- `TokenProvider`: renova a 80 % de `expires_in`; single-flight sob 50 chamadas concorrentes.

### 11.2 Testes de integração (contra mock/homologação)

| # | Cenário | Resultado esperado |
|---|---|---|
| IT-01 | Criar opt-in válido | `201`, status `ATIVO`, `protocolo_cerc` persistido |
| IT-02 | Criar opt-in duplicado | `409` local, **sem** chamada à CERC |
| IT-03 | CERC retorna `104803` | opt-in reconciliado para `ATIVO`, sem erro ao chamador |
| IT-04 | CERC retorna `104806` | `422`, opt-in `REJEITADO` |
| IT-05 | Atualizar opt-in (`tipoOperacao=A`) sem protocolo | erro local, nunca chega à CERC |
| IT-06 | Opt-out em opt-in `origem=CONTRATO` | `409 OPT_OUT_NAO_APLICAVEL` (R6) |
| IT-07 | Opt-out válido | `ENCERRADO`, `optout.status=CONFIRMADO` |
| IT-08 | Consulta agenda BATCH | `200` com URs; `valorTotalUR` preservado |
| IT-09 | Consulta agenda ONLINE | `202`; URs de webhook agregadas; consulta fecha após quiet period |
| IT-10 | Webhook duplicado (mesmo hash) | processado uma única vez |
| IT-11 | Webhook com falha de processamento | responde 2xx mesmo assim; erro fica em `webhook_inbox.erro` |
| IT-12 | CERC 500 em 3 tentativas, sucesso na 4ª | opt-in `ATIVO`, 4 linhas em `cerc_requisicao` |
| IT-13 | Token expirado (401) | renova e repete uma vez; sucesso |

### 11.3 Teste de carga

- Webhook receptor sustentando **500 req/s** por 5 min com p99 < 200 ms e zero respostas fora de 2xx.

### 11.4 Definição de pronto

- [ ] Todos os testes de §11.1–11.3 verdes
- [ ] Catálogo de erros §7 mapeado 1:1 no código (teste que percorre o enum)
- [ ] Nenhum `float`/`double` em campo monetário (verificação estática)
- [ ] Segredos fora do repositório; documentos mascarados em 100 % dos logs
- [ ] Runbook de reconciliação (§9.4) documentado
- [ ] Homologação/certificação CERC concluída no ambiente `api.int.cerc.com`

---

## 12. Riscos e pontos a confirmar com a CERC

1. **Rate limits** das rotas `/opt_in`, `/opt_out` e `/v15/agenda/consultar` e tamanho máximo de lote — não publicados; confirmar antes de dimensionar o *batcher*.
2. **Grade horária operacional** aplicável ao canal API (os erros `1049xx`/`1069xx` de grade horária são descritos para o canal arquivo).
3. Existência de endpoint de **consulta de opt-in** por protocolo (não localizado na documentação pública); sem ele, a reconciliação depende de AP022/AP023 (canal arquivo).
4. Semântica exata de `104804`, que aparece duplicado no catálogo da CERC com duas descrições distintas ("atualização de campos não permitidos" e "UFR não encontrado").
5. Confirmar se `documentoTitular` é obrigatório quando `documentoUsuarioFinalRecebedor` é CNPJ raiz (na SPEC 02 há regra análoga: `107814`).
