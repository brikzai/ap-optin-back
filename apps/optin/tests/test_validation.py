import datetime
import pytest
from apps.optin.validation import (
    ValidationError,
    normalizar_documento,
    tipo_documento,
    validar_documento,
    validar_vigencia,
    validar_credenciadoras,
)


def test_normalizar_documento_cnpj_formatado():
    assert normalizar_documento("12.345.678/0001-99") == "12345678000199"


def test_normalizar_documento_cpf_sem_zero_a_esquerda():
    assert normalizar_documento("1234567890") == "01234567890"


def test_normalizar_documento_cnpj_raiz_preservada():
    assert normalizar_documento("12345678") == "12345678"


def test_tipo_documento_classifica_por_tamanho():
    assert tipo_documento("12345678") == "CNPJ_RAIZ"
    assert tipo_documento("01234567890") == "CPF"
    assert tipo_documento("12345678000199") == "CNPJ"


def test_validar_documento_cpf_valido():
    documento, tipo = validar_documento("111.444.777-35")
    assert documento == "11144477735"
    assert tipo == "CPF"


def test_validar_documento_cnpj_valido():
    documento, tipo = validar_documento("11.222.333/0001-81")
    assert documento == "11222333000181"
    assert tipo == "CNPJ"


def test_validar_documento_cpf_dv_invalido():
    with pytest.raises(ValidationError) as exc:
        validar_documento("111.111.111-11")
    assert exc.value.codigo == "VAL002"


def test_validar_documento_cnpj_raiz_nao_exige_dv():
    documento, tipo = validar_documento("11222333")
    assert documento == "11222333"
    assert tipo == "CNPJ_RAIZ"


def test_validar_vigencia_fim_antes_do_inicio():
    with pytest.raises(ValidationError) as exc:
        validar_vigencia(
            datetime.date(2026, 8, 10), datetime.date(2026, 8, 11), datetime.date(2026, 8, 1)
        )
    assert exc.value.codigo == "VAL003"


def test_validar_vigencia_inicio_antes_da_assinatura():
    with pytest.raises(ValidationError) as exc:
        validar_vigencia(
            datetime.date(2026, 8, 10), datetime.date(2026, 8, 9), datetime.date(2027, 8, 10)
        )
    assert exc.value.codigo == "VAL004"


def test_validar_vigencia_ok():
    validar_vigencia(
        datetime.date(2026, 8, 10), datetime.date(2026, 8, 11), datetime.date(2027, 8, 10)
    )


def test_validar_credenciadoras_lista_vazia():
    with pytest.raises(ValidationError) as exc:
        validar_credenciadoras([])
    assert exc.value.codigo == "VAL006"


def test_validar_credenciadoras_mistura_curinga_com_especifico():
    with pytest.raises(ValidationError) as exc:
        validar_credenciadoras(["99T", "12345678000199"])
    assert exc.value.codigo == "VAL007"


def test_validar_credenciadoras_ok():
    validar_credenciadoras(["99T"])
    validar_credenciadoras(["12345678000199", "98765432000100"])


from apps.optin.validation import (
    ValidationError,
    conjuntos_se_sobrepoem,
    mascarar_documento,
    validar_arranjos,
    validar_evidencia,
    vigencias_se_sobrepoem,
)


def test_conjuntos_se_sobrepoem_com_intersecao():
    assert conjuntos_se_sobrepoem({"VCC", "MCC"}, {"MCC"}) is True


def test_conjuntos_se_sobrepoem_sem_intersecao():
    assert conjuntos_se_sobrepoem({"VCC"}, {"BCC"}) is False


def test_conjuntos_se_sobrepoem_curinga_99t_lado_a():
    assert conjuntos_se_sobrepoem({"99T"}, {"BCC"}) is True


def test_conjuntos_se_sobrepoem_curinga_99t_lado_b():
    assert conjuntos_se_sobrepoem({"VCC"}, {"99T"}) is True


def test_vigencias_se_sobrepoem_com_intersecao():
    import datetime

    a_inicio, a_fim = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
    b_inicio, b_fim = datetime.date(2026, 6, 1), datetime.date(2027, 6, 1)
    assert vigencias_se_sobrepoem(a_inicio, a_fim, b_inicio, b_fim) is True


def test_vigencias_se_sobrepoem_disjuntas():
    import datetime

    a_inicio, a_fim = datetime.date(2025, 1, 1), datetime.date(2025, 12, 31)
    b_inicio, b_fim = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
    assert vigencias_se_sobrepoem(a_inicio, a_fim, b_inicio, b_fim) is False


def test_mascarar_documento_cnpj():
    assert mascarar_documento("12345678000199") == "12345678****99"


def test_mascarar_documento_8_digitos_cnpj_raiz():
    assert mascarar_documento("12345678") == "12****78"


def test_mascarar_documento_11_digitos_cpf():
    assert mascarar_documento("11144477735") == "11144****35"


def test_validar_arranjos_aceita_curinga_sem_checar_dominio():
    validar_arranjos(["99T"], ativos={"VCC", "MCC"})


def test_validar_arranjos_aceita_codigo_ativo():
    validar_arranjos(["VCC"], ativos={"VCC", "MCC"})


def test_validar_arranjos_rejeita_codigo_fora_do_dominio():
    with pytest.raises(ValidationError) as exc:
        validar_arranjos(["ZZZ"], ativos={"VCC", "MCC"})
    assert exc.value.codigo == "VAL005"


def test_validar_evidencia_aceita_id_presente():
    validar_evidencia("doc_01H...")


def test_validar_evidencia_rejeita_ausente():
    with pytest.raises(ValidationError) as exc:
        validar_evidencia("")
    assert exc.value.codigo == "VAL008"
