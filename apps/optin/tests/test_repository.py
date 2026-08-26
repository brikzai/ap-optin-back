import datetime

from dotenv import load_dotenv
load_dotenv()

from shared.cloudsql_client import get_db

DOC_UFR = "22751826000125"
FINANCIADOR_TESTE = "12345678000199"

from apps.cliente import repository as cliente_repository


def _cliente_id_teste():
    existente = cliente_repository.buscar_por_documento(FINANCIADOR_TESTE, DOC_UFR)
    if existente:
        return existente["id"]
    return cliente_repository.criar(FINANCIADOR_TESTE, {
        "documento": DOC_UFR, "documento_tipo": "CNPJ", "nome": "Cliente Teste",
        "email": None, "telefone": None,
    })["id"]


def _limpar():
    ids = [
        r["id"]
        for r in get_db(FINANCIADOR_TESTE).table("optin").select("id").eq("documento_ufr", DOC_UFR).execute().data
    ]
    for optin_id in ids:
        get_db(FINANCIADOR_TESTE).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optout").delete().eq("optin_id", optin_id).execute()
        get_db(FINANCIADOR_TESTE).table("optin").delete().eq("id", optin_id).execute()


def _dados_base(**overrides):
    dados = {
        "cliente_id": _cliente_id_teste(),
        "cnpj_solicitante": "12345678000199",
        "cnpj_financiador": "12345678000199",
        "documento_ufr": DOC_UFR,
        "documento_ufr_tipo": "CNPJ",
        "documento_titular": DOC_UFR,
        "data_assinatura": datetime.date(2026, 8, 10),
        "vigencia_inicio": datetime.date(2026, 8, 11),
        "vigencia_fim": datetime.date(2027, 8, 10),
        "carteira": None,
        "evidencia_id": "doc_teste",
        "credenciadoras": ["99T"],
        "arranjos": ["VCC"],
    }
    dados.update(overrides)
    return dados


def test_criar_optin_pendente_grava_optin_e_filhas():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())

    assert optin["status"] == "PENDENTE"
    assert optin["credenciadoras"] == ["99T"]
    assert optin["arranjos"] == ["VCC"]
    assert optin["referencia_externa"].startswith("OPTIN-")
    _limpar()


def test_buscar_por_id_retorna_none_quando_nao_existe():
    from apps.optin import repository

    assert repository.buscar_por_id(FINANCIADOR_TESTE, "opt_inexistente") is None


def test_atualizar_status_muda_status_e_protocolo():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    atualizado = repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-123")

    assert atualizado["status"] == "ATIVO"
    assert atualizado["protocolo_cerc"] == "P-123"
    _limpar()


def test_existe_optin_ativo_equivalente_detecta_sobreposicao():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")

    conflito = repository.existe_optin_ativo_equivalente(
        FINANCIADOR_TESTE,
        documento_ufr=DOC_UFR,
        documento_titular=DOC_UFR,
        credenciadoras={"99T"},
        arranjos={"VCC"},
        vigencia_inicio=datetime.date(2027, 1, 1),
        vigencia_fim=datetime.date(2027, 12, 31),
    )
    assert conflito is True
    _limpar()


def test_existe_optin_ativo_equivalente_falso_quando_sem_ativos():
    from apps.optin import repository

    _limpar()
    conflito = repository.existe_optin_ativo_equivalente(
        FINANCIADOR_TESTE,
        documento_ufr=DOC_UFR,
        documento_titular=DOC_UFR,
        credenciadoras={"VCC"},
        arranjos={"VCC"},
        vigencia_inicio=datetime.date(2026, 1, 1),
        vigencia_fim=datetime.date(2026, 12, 31),
    )
    assert conflito is False


def test_existe_optin_ativo_equivalente_falso_quando_so_credenciadora_e_vigencia_sobrepoem():
    """Guarda contra um E lógico quebrado: credenciadora do ativo é curinga
    (sobrepõe qualquer coisa) e a vigência sobrepõe, mas o arranjo não
    sobrepõe — a função precisa exigir as três dimensões simultaneamente,
    não bastar duas delas."""
    from apps.optin import repository

    _limpar()
    try:
        optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base(
            data_assinatura=datetime.date(2025, 12, 1),
            credenciadoras=["99T"],
            arranjos=["ELO"],
            vigencia_inicio=datetime.date(2026, 1, 1),
            vigencia_fim=datetime.date(2026, 12, 31),
        ))
        repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-2")

        conflito = repository.existe_optin_ativo_equivalente(
            FINANCIADOR_TESTE,
            documento_ufr=DOC_UFR,
            documento_titular=DOC_UFR,
            credenciadoras={"555"},
            arranjos={"MC"},
            vigencia_inicio=datetime.date(2026, 6, 1),
            vigencia_fim=datetime.date(2026, 6, 30),
        )
        assert conflito is False
    finally:
        _limpar()


def test_existe_optin_ativo_equivalente_falso_quando_so_conjuntos_sobrepoem():
    """Guarda contra a checagem de vigência ser ignorada: credenciadoras e
    arranjos do ativo são curinga (sobrepõem qualquer coisa), mas a vigência
    não sobrepõe — precisa continuar exigindo o E lógico com a vigência."""
    from apps.optin import repository

    _limpar()
    try:
        optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base(
            data_assinatura=datetime.date(2025, 12, 1),
            credenciadoras=["99T"],
            arranjos=["99T"],
            vigencia_inicio=datetime.date(2026, 1, 1),
            vigencia_fim=datetime.date(2026, 6, 30),
        ))
        repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-3")

        conflito = repository.existe_optin_ativo_equivalente(
            FINANCIADOR_TESTE,
            documento_ufr=DOC_UFR,
            documento_titular=DOC_UFR,
            credenciadoras={"555"},
            arranjos={"MC"},
            vigencia_inicio=datetime.date(2026, 8, 1),
            vigencia_fim=datetime.date(2026, 12, 31),
        )
        assert conflito is False
    finally:
        _limpar()


def test_listar_filtra_por_status():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")

    resultado = repository.listar(FINANCIADOR_TESTE, {"status": "ATIVO", "documento_ufr": DOC_UFR}, limit=50)
    assert any(r["id"] == optin["id"] for r in resultado)

    vazio = repository.listar(FINANCIADOR_TESTE, {"status": "REJEITADO", "documento_ufr": DOC_UFR}, limit=50)
    assert vazio == []
    _limpar()


def test_criar_e_confirmar_optout():
    from apps.optin import repository

    _limpar()
    optin = repository.criar_optin_pendente(FINANCIADOR_TESTE, _dados_base())
    repository.atualizar_status(FINANCIADOR_TESTE, optin["id"], "ATIVO", protocolo_cerc="P-1")

    optout = repository.criar_optout_pendente(FINANCIADOR_TESTE, optin["id"])
    assert optout["status"] == "PENDENTE"
    assert optout["referencia_externa"].startswith("OPTOUT-")

    repository.confirmar_optout(FINANCIADOR_TESTE, optout["id"], optin["id"], "P-1")

    optin_atualizado = repository.buscar_por_id(FINANCIADOR_TESTE, optin["id"])
    assert optin_atualizado["status"] == "ENCERRADO"
    _limpar()
