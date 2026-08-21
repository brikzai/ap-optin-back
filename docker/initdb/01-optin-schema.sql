CREATE TABLE optin (
  id                    TEXT PRIMARY KEY,
  referencia_externa    TEXT UNIQUE NOT NULL,
  protocolo_cerc        TEXT UNIQUE,
  origem                TEXT NOT NULL,
  status                TEXT NOT NULL,
  cnpj_solicitante      TEXT NOT NULL,
  cnpj_financiador      TEXT NOT NULL,
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

CREATE TABLE optin_credenciadora (
  optin_id TEXT REFERENCES optin(id),
  cnpj TEXT,
  PRIMARY KEY (optin_id, cnpj)
);

CREATE TABLE optin_arranjo (
  optin_id TEXT REFERENCES optin(id),
  codigo TEXT,
  PRIMARY KEY (optin_id, codigo)
);

CREATE TABLE optout (
  id                 TEXT PRIMARY KEY,
  optin_id           TEXT NOT NULL REFERENCES optin(id),
  referencia_externa TEXT UNIQUE NOT NULL,
  protocolo_cerc     TEXT,
  status             TEXT NOT NULL,
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cerc_requisicao (
  id                 TEXT PRIMARY KEY,
  recurso            TEXT NOT NULL,
  correlacao_id      TEXT NOT NULL,
  http_status        INT,
  request_body       JSONB NOT NULL,
  response_body      JSONB,
  tentativa          INT NOT NULL DEFAULT 1,
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE webhook_inbox (
  id               TEXT PRIMARY KEY,
  tipo_evento      TEXT NOT NULL,
  data_hora_evento TIMESTAMPTZ NOT NULL,
  payload          JSONB NOT NULL,
  hash_dedupe      TEXT NOT NULL UNIQUE,
  processado_em    TIMESTAMPTZ,
  erro             TEXT,
  recebido_em      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dominio_arranjo (
  codigo        TEXT PRIMARY KEY,
  descricao     TEXT,
  ativo         BOOLEAN NOT NULL DEFAULT true,
  atualizado_em TIMESTAMPTZ NOT NULL
);
