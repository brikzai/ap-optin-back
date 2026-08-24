"""Idempotência de POSTs mutantes (SPEC-01 §5: Idempotency-Key obrigatório).

Sem tabela dedicada por recurso na SPEC-01 §6 — usa a tabela genérica
`idempotency_key` (recurso, chave) -> resposta gravada, criada no Plan 08
(docker/initdb/02-idempotency-e-referencia.sql). Reentrega com a mesma
chave devolve a resposta original sem repetir o efeito colateral.
"""
import functools
import json

from django.http import JsonResponse

from shared.cloudsql_client import get_db


def buscar_resposta_em_cache(financiador_id: str, recurso: str, chave: str) -> dict:
    rows = (
        get_db(financiador_id).table("idempotency_key").select("*")
        .eq("recurso", recurso).eq("chave", chave).execute().data
    )
    return rows[0] if rows else None


def guardar_resposta(financiador_id: str, recurso: str, chave: str, http_status: int, response_body) -> None:
    get_db(financiador_id).table("idempotency_key").insert({
        "recurso": recurso,
        "chave": chave,
        "http_status": http_status,
        "response_body": response_body,
    }).execute()


def idempotente(recurso: str):
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            chave = request.headers.get("Idempotency-Key")
            if not chave:
                return JsonResponse(
                    {"erro": "VAL011", "mensagem": "header Idempotency-Key é obrigatório"}, status=422
                )

            cache = buscar_resposta_em_cache(request.financiador_id, recurso, chave)
            if cache:
                return JsonResponse(cache["response_body"], status=cache["http_status"])

            response = view_func(request, *args, **kwargs)
            guardar_resposta(request.financiador_id, recurso, chave, response.status_code, json.loads(response.content))
            return response

        return wrapper

    return decorator
