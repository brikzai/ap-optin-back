"""OAuth2 client-credentials — obtém e cacheia o access token da CERC.

Cache em memória por processo. Renovação proativa a 80% de expires_in (uma
chamada depois desse ponto sempre busca um token novo, nunca devolve um
perto de vencer). Single-flight via threading.Lock com double-checked
locking: o caminho comum (token em cache, ainda válido) nunca bloqueia; só
quem chega com o cache frio/vencido disputa o lock, e só um deles de fato
faz a chamada HTTP — os demais reaproveitam o resultado.

Em 401 numa chamada à API da CERC, quem fez a chamada (services/cerc/client.py,
Plano 07) invalida o cache com invalidate_token() e tenta de novo uma única
vez — o retry em si não é responsabilidade deste módulo.
"""

import os
import threading
import time

import httpx

from shared.secrets import get_secret

_lock = threading.Lock()
_cache = {"access_token": None, "expires_at": 0.0}


def _fetch_token() -> dict:
    response = httpx.post(
        os.environ["CERC_AUTH_URL"],
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["CERC_CLIENT_ID"],
            "client_secret": get_secret("CERC_CLIENT_SECRET"),
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def get_cerc_token() -> str:
    now = time.time()
    if _cache["access_token"] and now < _cache["expires_at"]:
        return _cache["access_token"]

    with _lock:
        now = time.time()
        if _cache["access_token"] and now < _cache["expires_at"]:
            return _cache["access_token"]

        payload = _fetch_token()
        _cache["access_token"] = payload["access_token"]
        _cache["expires_at"] = now + 0.8 * payload["expires_in"]
        return _cache["access_token"]


def invalidate_token() -> None:
    with _lock:
        _cache["access_token"] = None
        _cache["expires_at"] = 0.0
