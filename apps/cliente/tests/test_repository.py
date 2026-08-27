from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOCUMENTO_TESTE = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"


def _limpar():
    get_db(FINANCIADOR_TESTE).table("cliente").delete().eq("documento", DOCUMENTO_TESTE).execute()


def test_criar_grava_cliente():
    from apps.cliente import repository

    _limpar()
    try:
        cliente = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": "teste@example.com", "telefone": "11999999999",
        })
        assert cliente["nome"] == "Cliente Teste"
        assert cliente["documento"] == DOCUMENTO_TESTE
        assert cliente["id"].startswith("cli_")
    finally:
        _limpar()


def test_buscar_por_documento_retorna_none_quando_nao_existe():
    from apps.cliente import repository

    _limpar()
    assert repository.buscar_por_documento(FINANCIADOR_TESTE, DOCUMENTO_TESTE) is None


def test_buscar_por_documento_encontra_cliente_criado():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        encontrado = repository.buscar_por_documento(FINANCIADOR_TESTE, DOCUMENTO_TESTE)
        assert encontrado["id"] == criado["id"]
    finally:
        _limpar()


def test_buscar_por_id_retorna_none_quando_nao_existe():
    from apps.cliente import repository

    assert repository.buscar_por_id(FINANCIADOR_TESTE, "cli_inexistente") is None


def test_listar_filtra_por_documento():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        resultado = repository.listar(FINANCIADOR_TESTE, {"documento": DOCUMENTO_TESTE}, limit=50)
        assert any(c["id"] == criado["id"] for c in resultado)

        vazio = repository.listar(FINANCIADOR_TESTE, {"documento": "00000000000000"}, limit=50)
        assert vazio == []
    finally:
        _limpar()


def test_criar_grava_status_default_pending():
    from apps.cliente import repository

    _limpar()
    try:
        cliente = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        assert cliente["status"] == "pending"
    finally:
        _limpar()


def test_criar_aceita_status_explicito():
    from apps.cliente import repository

    _limpar()
    try:
        cliente = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None, "status": "active",
        })
        assert cliente["status"] == "active"
    finally:
        _limpar()


def test_atualizar_altera_campos_e_marca_atualizado_em():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": None, "telefone": None,
        })
        atualizado = repository.atualizar(
            FINANCIADOR_TESTE, criado["id"], {"nome": "Nome Novo", "email": "novo@example.com"}
        )
        assert atualizado["nome"] == "Nome Novo"
        assert atualizado["email"] == "novo@example.com"
        assert atualizado["telefone"] is None
        assert atualizado["atualizado_em"] > criado["criado_em"]
    finally:
        _limpar()


def test_atualizar_so_grava_campos_presentes_no_dict():
    from apps.cliente import repository

    _limpar()
    try:
        criado = repository.criar(FINANCIADOR_TESTE, {
            "documento": DOCUMENTO_TESTE, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
            "email": "original@example.com", "telefone": "11888888888",
        })
        atualizado = repository.atualizar(FINANCIADOR_TESTE, criado["id"], {"status": "active"})
        assert atualizado["status"] == "active"
        assert atualizado["email"] == "original@example.com"
        assert atualizado["telefone"] == "11888888888"
        assert atualizado["nome"] == "Cliente Teste"
    finally:
        _limpar()
