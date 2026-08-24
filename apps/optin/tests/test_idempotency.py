# optin/apps/optin/tests/test_idempotency.py
from dotenv import load_dotenv
load_dotenv()

import json

from django.http import JsonResponse
from django.test import RequestFactory

from shared.cloudsql_client import get_db

FINANCIADOR_TESTE = "12345678000199"


def _limpar(financiador_id, chave):
    get_db(financiador_id).table("idempotency_key").delete().eq("chave", chave).execute()


def test_idempotente_retorna_422_sem_header():
    from apps.optin.idempotency import idempotente

    @idempotente("teste_recurso")
    def view(request):
        return JsonResponse({"ok": True}, status=201)

    request = RequestFactory().post("/x")
    request.financiador_id = FINANCIADOR_TESTE
    response = view(request)
    assert response.status_code == 422
    assert json.loads(response.content)["erro"] == "VAL011"


def test_idempotente_executa_view_e_guarda_resposta():
    from apps.optin.idempotency import idempotente

    _limpar(FINANCIADOR_TESTE, "chave-1")
    chamadas = []

    @idempotente("teste_recurso")
    def view(request):
        chamadas.append(1)
        return JsonResponse({"id": "abc"}, status=201)

    request = RequestFactory().post("/x", HTTP_IDEMPOTENCY_KEY="chave-1")
    request.financiador_id = FINANCIADOR_TESTE
    response = view(request)

    assert response.status_code == 201
    assert len(chamadas) == 1

    cache = get_db(FINANCIADOR_TESTE).table("idempotency_key").select("*").eq("chave", "chave-1").execute().data
    assert cache[0]["response_body"] == {"id": "abc"}
    _limpar(FINANCIADOR_TESTE, "chave-1")


def test_idempotente_retorna_resposta_cacheada_sem_chamar_view_de_novo():
    from apps.optin.idempotency import idempotente

    _limpar(FINANCIADOR_TESTE, "chave-2")
    chamadas = []

    @idempotente("teste_recurso")
    def view(request):
        chamadas.append(1)
        return JsonResponse({"id": "abc"}, status=201)

    request = RequestFactory().post("/x", HTTP_IDEMPOTENCY_KEY="chave-2")
    request.financiador_id = FINANCIADOR_TESTE
    view(request)
    segunda = view(request)

    assert len(chamadas) == 1
    assert segunda.status_code == 201
    assert json.loads(segunda.content) == {"id": "abc"}
    _limpar(FINANCIADOR_TESTE, "chave-2")
