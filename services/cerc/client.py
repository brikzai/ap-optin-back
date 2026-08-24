"""Cliente REST da CERC — registrar/atualizar/encerrar opt-in.

Toda chamada grava uma linha em cerc_requisicao ANTES de decidir se levanta
CercApiError (design §4) — a trilha de auditoria existe mesmo quando a
chamada termina em erro. Em 401, invalida o token (Plan 06) e repete a
mesma chamada uma única vez, com uma segunda linha de log (tentativa=2).

Confirmado contra SPEC-01 §4.1/§4.2: `/opt_in` é o único recurso para
criar E atualizar opt-in (diferenciado por `tipoOperacao`: "C" ou "A",
com `protocolo` obrigatório na atualização) — não existe `PUT
/opt_in/{protocolo}`. Ambos os recursos (`/opt_in` e `/opt_out`) recebem
sempre um array (lote), mesmo para um único item, e respondem 207
multi-status (array, um item por entrada enviada). O parsing item-a-item
do 207 é responsabilidade de quem consome o retorno desta camada de
transporte, não deste módulo.

Multi-tenancy: toda função pública recebe financiador_id como primeiro
parâmetro — usado para buscar o token do tenant certo
(services/cerc/token_provider.py) e gravar a auditoria em cerc_requisicao
do banco do tenant certo (shared/cloudsql_client.py). Ver
docs/superpowers/specs/2026-08-24-multitenancy-design.md §5.
"""

import os
import uuid

import httpx

from services.cerc.token_provider import get_cerc_token, invalidate_token
from shared.cloudsql_client import get_db


class CercApiError(Exception):
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"CERC API respondeu {status_code}: {body}")


def _log_attempt(financiador_id: str, recurso: str, correlacao_id: str, request_body, response, tentativa: int) -> None:
    get_db(financiador_id).table("cerc_requisicao").insert({
        "id": str(uuid.uuid4()),
        "recurso": recurso,
        "correlacao_id": correlacao_id,
        "http_status": response.status_code if response is not None else None,
        "request_body": request_body,
        "response_body": _safe_json(response),
        "tentativa": tentativa,
    }).execute()


def _safe_json(response):
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _send(method: str, path: str, batch, correlacao_id: str, token: str) -> httpx.Response:
    url = os.environ["CERC_API_BASE_URL"] + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": correlacao_id,
    }
    return httpx.request(method, url, json=batch, headers=headers, timeout=15.0)


def _request(financiador_id: str, method: str, path: str, batch: list, correlacao_id: str) -> list:
    token = get_cerc_token(financiador_id)
    try:
        response = _send(method, path, batch, correlacao_id, token)
    except httpx.HTTPError:
        _log_attempt(financiador_id, path, correlacao_id, batch, None, tentativa=1)
        raise
    _log_attempt(financiador_id, path, correlacao_id, batch, response, tentativa=1)

    if response.status_code == 401:
        invalidate_token(financiador_id)
        token = get_cerc_token(financiador_id)
        try:
            response = _send(method, path, batch, correlacao_id, token)
        except httpx.HTTPError:
            _log_attempt(financiador_id, path, correlacao_id, batch, None, tentativa=2)
            raise
        _log_attempt(financiador_id, path, correlacao_id, batch, response, tentativa=2)

    if response.status_code >= 400:
        raise CercApiError(response.status_code, _safe_json(response))

    return response.json()


def registrar_optin(financiador_id: str, payload: dict, correlacao_id: str) -> list:
    item = {**payload, "tipoOperacao": "C"}
    return _request(financiador_id, "POST", "/opt_in", [item], correlacao_id)


def atualizar_optin(financiador_id: str, protocolo_cerc: str, payload: dict, correlacao_id: str) -> list:
    item = {**payload, "tipoOperacao": "A", "protocolo": protocolo_cerc}
    return _request(financiador_id, "POST", "/opt_in", [item], correlacao_id)


def encerrar_optin(financiador_id: str, protocolo_cerc: str, payload: dict, correlacao_id: str) -> list:
    item = {**payload, "protocoloOptIn": protocolo_cerc}
    return _request(financiador_id, "POST", "/opt_out", [item], correlacao_id)
