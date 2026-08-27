import datetime
import json

import httpx
import respx
from dotenv import load_dotenv
load_dotenv()

from apps.cliente import repository as cliente_repository
from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optout").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _limpar_idempotencia(chave: str):
    get_db(FINANCIADOR_TESTE).table("idempotency_key").delete().eq("recurso", "optin_cancel").eq("chave", chave).execute()


def _criar_ativo():
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cliente_id": _cliente_id_teste(),
        "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"], "arranjos": ["VCC"],
    })
    return repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-ATIVO-1")


@respx.mock
def test_cancelar_optin_sucesso_marca_encerrado(client, auth_headers):
    _limpar()
    chave = "key-cancelar-1"
    _limpar_idempotencia(chave)
    try:
        optin = _criar_ativo()

        respx.post("https://api.int.cerc.com/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )

        def _resposta(request):
            enviado = json.loads(request.content)[0]
            return httpx.Response(207, json=[{
                "protocolo": "P-OPTOUT-1", "referenciaExterna": enviado["referenciaExterna"], "status": "0", "erros": [],
            }])

        respx.post("https://ap-homolog.cerc.inf.br/opt_out").mock(side_effect=_resposta)

        response = client.post(
            f"/api/v1/optins/{optin['id']}/cancelar", content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave, **auth_headers,
        )

        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["status"] == "ENCERRADO"
    finally:
        _limpar_idempotencia(chave)
        _limpar()


def test_cancelar_optin_pendente_retorna_409(client, auth_headers):
    _limpar()
    try:
        optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, {
            "cliente_id": _cliente_id_teste(),
            "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
            "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
            "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
            "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
            "credenciadoras": ["99T"], "arranjos": ["VCC"],
        })

        response = client.post(
            f"/api/v1/optins/{optin['id']}/cancelar", content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-cancelar-pendente", **auth_headers,
        )

        assert response.status_code == 409
        assert json.loads(response.content)["erro"] == "OPTIN_NAO_ATIVO"
    finally:
        _limpar()


def test_cancelar_optin_404_quando_nao_existe(client, auth_headers):
    response = client.post(
        "/api/v1/optins/opt_inexistente/cancelar", content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-cancelar-404", **auth_headers,
    )
    assert response.status_code == 404
    assert json.loads(response.content)["erro"] == "OPTIN_NAO_ENCONTRADO"
