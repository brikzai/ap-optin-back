import json

import httpx
import respx
from dotenv import load_dotenv
load_dotenv()

from apps.optin import repository
from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    ids = [r["id"] for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _limpar_idempotencia(chave: str):
    # Este banco é o Cloud SQL real do tenant dev, não efêmero: sem limpar a
    # chave de idempotência antes E depois de cada teste, uma reexecução da
    # suíte (mesmo em sessões diferentes) encontraria a resposta da rodada
    # anterior já cacheada em `idempotency_key` e o decorator `idempotente`
    # devolveria essa resposta velha sem executar a view de novo — os testes
    # passariam "verdes" sem exercitar o código atual (ou, pior, comparando o
    # optin recém-criado nesta rodada com uma resposta cacheada de um optin
    # de uma rodada anterior). Chamada antes (para garantir estado limpo,
    # independente de rodadas passadas) e depois (para não vazar para a
    # próxima rodada) em cada teste deste arquivo.
    get_db(FINANCIADOR_TESTE).table("idempotency_key").delete().eq("recurso", "optin_update").eq("chave", chave).execute()


def _criar_pendente():
    import datetime

    return repository.criar_optin_pendente(FINANCIADOR_TESTE, {
        "cnpj_solicitante": "12345678000199", "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR, "documento_ufr_tipo": "CNPJ", "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10), "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10), "carteira": None, "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"], "arranjos": ["VCC"],
    })


def _criar_ativo():
    optin = _criar_pendente()
    return repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")


@respx.mock
def test_atualizar_optin_sucesso(client, auth_headers):
    chave = "key-update-1"
    _limpar()
    _limpar_idempotencia(chave)
    try:
        optin = _criar_ativo()
        respx.post("https://api.int.cerc.com/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
            return_value=httpx.Response(207, json=[{"protocolo": "P-1", "referenciaExterna": optin["referencia_externa"], "status": "0", "erros": []}])
        )

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({"vigenciaFim": "2028-01-01"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave,
            **auth_headers,
        )

        assert response.status_code == 200
        assert json.loads(response.content)["vigenciaFim"] == "2028-01-01"
    finally:
        _limpar()
        _limpar_idempotencia(chave)


@respx.mock
def test_atualizar_optin_rejeita_campo_nao_atualizavel_sem_chamar_cerc(client, auth_headers):
    # @respx.mock com NENHUMA rota registrada: se a checagem de campos imutáveis
    # regredir e o código tentar mesmo assim chamar a CERC, respx levanta
    # "no matching route" imediatamente e de forma determinística — sem depender
    # de alcançabilidade de rede real nem de um token OAuth cacheado de um teste
    # anterior no mesmo módulo (services/cerc/token_provider.py cacheia por
    # financiador_id pela vida do token, o que deixaria uma regressão silenciosa
    # pular direto o passo de OAuth).
    chave = "key-update-2"
    _limpar()
    _limpar_idempotencia(chave)
    try:
        optin = _criar_ativo()

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({"referenciaExterna": "OUTRA"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave,
            **auth_headers,
        )
        assert response.status_code == 422
    finally:
        _limpar()
        _limpar_idempotencia(chave)


def test_atualizar_optin_404_quando_nao_existe(client, auth_headers):
    chave = "key-update-3"
    _limpar_idempotencia(chave)
    try:
        response = client.patch(
            "/api/v1/optins/opt_inexistente",
            data=json.dumps({"vigenciaFim": "2028-01-01"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave,
            **auth_headers,
        )
        assert response.status_code == 404
    finally:
        _limpar_idempotencia(chave)


def test_atualizar_optin_409_quando_nao_ativo(client, auth_headers):
    chave = "key-update-409"
    _limpar()
    _limpar_idempotencia(chave)
    try:
        optin = _criar_pendente()

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({"vigenciaFim": "2028-01-01"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave,
            **auth_headers,
        )
        assert response.status_code == 409
    finally:
        _limpar()
        _limpar_idempotencia(chave)


@respx.mock
def test_atualizar_optin_422_quando_sem_protocolo_cerc(client, auth_headers):
    # Mesmo raciocínio do teste de campo imutável: @respx.mock sem rotas
    # registradas garante que, se a checagem de protocolo_cerc ausente
    # regredir, a tentativa de chamar a CERC falha de forma determinística.
    chave = "key-update-422-protocolo"
    _limpar()
    _limpar_idempotencia(chave)
    try:
        optin = _criar_pendente()
        # Sem passar protocolo_cerc: fica ATIVO mas sem protocolo, como um optin
        # que nunca recebeu confirmação da CERC (cenário raro, mas o guard deve
        # cobri-lo).
        optin = repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO")

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({"vigenciaFim": "2028-01-01"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave,
            **auth_headers,
        )
        assert response.status_code == 422
    finally:
        _limpar()
        _limpar_idempotencia(chave)


@respx.mock
def test_atualizar_optin_persiste_arranjos_credenciadoras_cnpj_financiador(client, auth_headers):
    chave = "key-update-persist"
    _limpar()
    _limpar_idempotencia(chave)
    try:
        optin = _criar_ativo()
        respx.post("https://api.int.cerc.com/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://ap-homolog.cerc.inf.br/opt_in").mock(
            return_value=httpx.Response(207, json=[{"protocolo": "P-1", "referenciaExterna": optin["referencia_externa"], "status": "0", "erros": []}])
        )

        response = client.patch(
            f"/api/v1/optins/{optin['id']}",
            data=json.dumps({
                "arranjos": ["99T"],
                "credenciadoras": ["11444777000161"],
                "cnpjFinanciador": "98765432000199",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=chave,
            **auth_headers,
        )

        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["arranjos"] == ["99T"]
        assert body["credenciadoras"] == ["11444777000161"]
        assert body["cnpjFinanciador"] == "98765432000199"

        # Não basta conferir o eco da resposta HTTP — o bug original deixava a
        # resposta parecer correta enquanto as tabelas filhas (optin_arranjo/
        # optin_credenciadora) e o campo cnpj_financiador continuavam com os
        # valores antigos no banco. Recarrega do zero para confirmar persistência.
        recarregado = repository.buscar_por_id(FINANCIADOR_TESTE, optin["id"])
        assert recarregado["arranjos"] == ["99T"]
        assert recarregado["credenciadoras"] == ["11444777000161"]
        assert recarregado["cnpj_financiador"] == "98765432000199"
    finally:
        _limpar()
        _limpar_idempotencia(chave)
