CREATE TABLE idempotency_key (
  recurso        TEXT NOT NULL,
  chave          TEXT NOT NULL,
  http_status    INT NOT NULL,
  response_body  JSONB NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (recurso, chave)
);

CREATE SEQUENCE optin_referencia_seq START 1;
CREATE SEQUENCE optout_referencia_seq START 1;
