import pytest

from apps.optin.cerc_mapping import (
    correlacionar_por_referencia,
    interpretar_item_opt_in,
    interpretar_item_opt_out,
)


def test_interpretar_item_opt_in_sucesso():
    item = {"status": "0", "protocolo": "P-1", "referenciaExterna": "REF-1", "erros": []}
    resultado = interpretar_item_opt_in(item)
    assert resultado.status_local == "ATIVO"
    assert resultado.protocolo == "P-1"


def test_interpretar_item_opt_in_104803_e_idempotente():
    # IT-03 (SPEC-01 §11.2): CERC retorna 104803 -> reconciliado para ATIVO, sem erro ao chamador.
    item = {
        "status": "1",
        "protocolo": "P-1",
        "referenciaExterna": "REF-1",
        "erros": [{"codigo": "104803", "mensagem": "Opt-in já informado"}],
    }
    resultado = interpretar_item_opt_in(item)
    assert resultado.status_local == "ATIVO"
    assert resultado.erro_codigo == "104803"


def test_interpretar_item_opt_in_104806_e_rejeitado():
    # IT-04 (SPEC-01 §11.2): CERC retorna 104806 -> 422, opt-in REJEITADO.
    item = {
        "status": "1",
        "referenciaExterna": "REF-1",
        "erros": [{"codigo": "104806", "mensagem": "dataInicio menor que dataAssinaturaOptIn"}],
    }
    resultado = interpretar_item_opt_in(item)
    assert resultado.status_local == "REJEITADO"
    assert resultado.erro_codigo == "104806"


def test_interpretar_item_opt_out_sucesso():
    item = {"status": "0", "protocolo": "P-1", "referenciaExterna": "REF-2", "erros": []}
    resultado = interpretar_item_opt_out(item)
    assert resultado.status_local == "CONFIRMADO"


def test_interpretar_item_opt_out_106803_e_idempotente():
    item = {
        "status": "1",
        "referenciaExterna": "REF-2",
        "erros": [{"codigo": "106803", "mensagem": "Opt-out já informado"}],
    }
    resultado = interpretar_item_opt_out(item)
    assert resultado.status_local == "CONFIRMADO"


def test_correlacionar_por_referencia_encontra_item():
    itens = [{"referenciaExterna": "REF-1"}, {"referenciaExterna": "REF-2"}]
    assert correlacionar_por_referencia(itens, "REF-2") == {"referenciaExterna": "REF-2"}


def test_correlacionar_por_referencia_lanca_key_error_se_ausente():
    with pytest.raises(KeyError):
        correlacionar_por_referencia([{"referenciaExterna": "REF-1"}], "REF-9")
