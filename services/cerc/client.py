"""Cliente REST da CERC — registrar/atualizar/encerrar opt-in.

Toda chamada grava uma linha em cerc_requisicao ANTES de decidir se levanta
CercApiError (design §4) — a trilha de auditoria existe mesmo quando a
chamada termina em erro. Em 401, invalida o token (Plan 06) e repete a
mesma chamada uma única vez, com uma segunda linha de log (tentativa=2).

Paths de atualizar_optin (PUT /opt_in/{protocolo}) e encerrar_optin
(POST /opt_out) são convenção REST assumida, não confirmada contra a
SPEC-01 §4 — ver "Riscos e pendências" no plano.
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


def _request(method: str, path: str, payload: dict, correlacao_id: str) -> dict:
    token = get_cerc_token()
    response = _send(method, path, payload, correlacao_id, token)
    _log_attempt(path, correlacao_id, payload, response, tentativa=1)

    if response.status_code == 401:
        invalidate_token()
        token = get_cerc_token()
        response = _send(method, path, payload, correlacao_id, token)
        _log_attempt(path, correlacao_id, payload, response, tentativa=2)

    if response.status_code >= 400:
        raise CercApiError(response.status_code, _safe_json(response))

    return response.json()


def registrar_optin(payload: dict, correlacao_id: str) -> dict:
    return _request("POST", "/opt_in", payload, correlacao_id)


def atualizar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> dict:
    return _request("PUT", f"/opt_in/{protocolo_cerc}", payload, correlacao_id)


def encerrar_optin(protocolo_cerc: str, payload: dict, correlacao_id: str) -> dict:
    return _request("POST", "/opt_out", {**payload, "protocoloOptIn": protocolo_cerc}, correlacao_id)
