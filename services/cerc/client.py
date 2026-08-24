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
do 207 (correlação por `referenciaExterna`, nunca tratar o HTTP 207 como
sucesso global) é responsabilidade de quem consome o retorno desta
camada de transporte, não deste módulo.
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


def _log_attempt(recurso: str, correlacao_id: str, request_body: dict, response, tentativa: int) -> None:
    get_db().table("cerc_requisicao").insert({
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


def _send(method: str, path: str, payload: dict, correlacao_id: str, token: str) -> httpx.Response:
    url = os.environ["CERC_API_BASE_URL"] + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": correlacao_id,
    }
    return httpx.request(method, url, json=payload, headers=headers, timeout=15.0)


def _request(method: str, path: str, batch: list, correlacao_id: str) -> list:
    token = get_cerc_token()
    try:
        response = _send(method, path, batch, correlacao_id, token)
    except httpx.HTTPError:
        _log_attempt(path, correlacao_id, batch, None, tentativa=1)
        raise
    _log_attempt(path, correlacao_id, batch, response, tentativa=1)

    if response.status_code == 401:
        invalidate_token()
        token = get_cerc_token()
        try:
            response = _send(method, path, batch, correlacao_id, token)
        except httpx.HTTPError:
            _log_attempt(path, correlacao_id, batch, None, tentativa=2)
            raise
        _log_attempt(path, correlacao_id, batch, response, tentativa=2)

    if response.status_code >= 400:
        raise CercApiError(response.status_code, _safe_json(response))

    return response.json()


def registrar_optin(payload: dict, correlacao_id: str) -> list:
    """POST /opt_in, tipoOperacao=C (SPEC-01 §4.1). Retorna o array 207 cru."""
    item = {**payload, "tipoOperacao": "C"}
    return _request("POST", "/opt_in", [item], correlacao_id)


def atualizar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> list:
    """POST /opt_in, tipoOperacao=A com o protocolo original (SPEC-01 §4.1).

    Mesmo recurso de registrar_optin — a CERC não tem endpoint de update
    dedicado; a diferença é inteiramente no corpo do item.
    """
    item = {**payload, "tipoOperacao": "A", "protocolo": protocolo_cerc}
    return _request("POST", "/opt_in", [item], correlacao_id)


def encerrar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> list:
    """POST /opt_out (SPEC-01 §4.2). Retorna o array 207 cru."""
    item = {**payload, "protocoloOptIn": protocolo_cerc}
    return _request("POST", "/opt_out", [item], correlacao_id)
