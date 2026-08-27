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
