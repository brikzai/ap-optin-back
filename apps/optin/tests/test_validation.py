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
