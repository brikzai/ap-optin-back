# optin-service — Plan 04: Local Validation Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pure-function local validation — document normalization/digit-check, vigência rules, credenciadoras curinga rule — matching SPEC-01's `VAL0xx` catalog, with zero CERC/database dependency.

**Architecture:** A single module, `apps/optin/validation.py`, raising a typed `ValidationError(codigo, mensagem)`. No I/O — trivially unit-testable.

**Tech Stack:** Python 3.12 stdlib only (`re`, `datetime`).

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§4). Normative source: `SPEC-01-optin-e-gestao.md` §2 (R1-R8), §2.3 (curingas), §7.3 (VAL001-VAL010), §11.1 (testes unitários). Series: plan 4 of 7.

**Depends on:** `2026-08-19-optin-plan-01-scaffold.md` (repo layout — this module lives inside `apps/optin/`, already created there).

## Global Constraints

- Documents are stored **without formatting**, zero-padded left: 14 digits (CNPJ), 11 digits (CPF), 8 digits (CNPJ raiz) (SPEC-01 §4.1).
- `dominio_arranjo` (accepted arranjo codes) is a versioned table, **never** a hardcoded compile-time enum (SPEC-01 §2.3) — this plan does not implement arranjo-domain checking (VAL005), which needs the `dominio_arranjo` table (Plan 02) and is deferred to the internal API plan that reads it.

---

### Task 1: `apps/optin/validation.py`

**Files:**
- Create: `optin/apps/optin/validation.py`
- Test: `optin/apps/optin/tests/test_validation.py`

**Interfaces:**
- Produces: `ValidationError(codigo: str, mensagem: str)`; `normalizar_documento(raw: str) -> str`; `tipo_documento(documento: str) -> str` (`"CPF"|"CNPJ"|"CNPJ_RAIZ"`); `validar_documento(raw: str) -> tuple[str, str]`; `validar_vigencia(data_assinatura, vigencia_inicio, vigencia_fim) -> None`; `validar_credenciadoras(lista: list[str]) -> None`. The internal API plan imports all of these.

- [ ] **Step 1: Write the failing test**

```python
# optin/apps/optin/tests/test_validation.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/optin/tests/test_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.optin.validation'`

- [ ] **Step 3: Write `apps/optin/validation.py`**

```python
"""Regras locais pré-CERC — SPEC-01 §2 (R1-R8) e §7.3 (VAL001-VAL010)."""

import re


class ValidationError(Exception):
    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(f"{codigo}: {mensagem}")


def normalizar_documento(raw: str) -> str:
    """Remove formatação e aplica zero-padding à esquerda (8/11/14 dígitos)."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise ValidationError("VAL001", "documento vazio")
    if len(digits) <= 8:
        return digits.zfill(8)
    if len(digits) <= 11:
        return digits.zfill(11)
    return digits.zfill(14)


def tipo_documento(documento: str) -> str:
    tamanho = len(documento)
    if tamanho == 8:
        return "CNPJ_RAIZ"
    if tamanho == 11:
        return "CPF"
    if tamanho == 14:
        return "CNPJ"
    raise ValidationError("VAL001", f"documento com tamanho inválido: {tamanho}")


def _digito_verificador(base: str, pesos: list) -> str:
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def _validar_cpf(cpf: str) -> bool:
    if cpf == cpf[0] * 11:
        return False
    dv1 = _digito_verificador(cpf[:9], list(range(10, 1, -1)))
    dv2 = _digito_verificador(cpf[:9] + dv1, list(range(11, 1, -1)))
    return cpf[-2:] == dv1 + dv2


def _validar_cnpj(cnpj: str) -> bool:
    if cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    dv1 = _digito_verificador(cnpj[:12], pesos1)
    dv2 = _digito_verificador(cnpj[:12] + dv1, pesos2)
    return cnpj[-2:] == dv1 + dv2


def validar_documento(raw: str) -> tuple:
    """Normaliza e valida dígito verificador (CNPJ raiz não tem DV a validar)."""
    documento = normalizar_documento(raw)
    tipo = tipo_documento(documento)
    if tipo == "CPF" and not _validar_cpf(documento):
        raise ValidationError("VAL002", "dígito verificador de CPF inválido")
    if tipo == "CNPJ" and not _validar_cnpj(documento):
        raise ValidationError("VAL002", "dígito verificador de CNPJ inválido")
    return documento, tipo


def validar_vigencia(data_assinatura, vigencia_inicio, vigencia_fim) -> None:
    """R2/R3 — SPEC-01 §2.2. Datas já parseadas (datetime.date)."""
    if vigencia_fim < vigencia_inicio:
        raise ValidationError("VAL003", "vigenciaFim menor que vigenciaInicio")
    if vigencia_inicio < data_assinatura:
        raise ValidationError("VAL004", "vigenciaInicio menor que dataAssinatura")


def validar_credenciadoras(lista: list) -> None:
    """VAL006/VAL007 — SPEC-01 §2.3 (curinga 99T)."""
    if not lista:
        raise ValidationError("VAL006", "lista de credenciadoras vazia")
    if "99T" in lista and len(lista) > 1:
        raise ValidationError("VAL007", "mistura de 99T com CNPJs específicos na mesma lista")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/optin/tests/test_validation.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/optin/validation.py apps/optin/tests/test_validation.py
git commit -m "feat: local validation rules (document normalization, vigencia, credenciadoras)"
```

---

## Self-Review Notes

- **Spec coverage:** SPEC-01 §2.2 (R2/R3), §2.3 (curinga) → covered. VAL001/VAL002 (document) → covered. VAL005 (arranjo domain), VAL008-VAL010 (evidência, carteira, anti-duplicidade) are deferred to the internal API plan, since they need the database (`dominio_arranjo`, existing `optin` rows) — this plan is deliberately pure-function/no-I/O.
- **Placeholder scan:** none.
- **Type consistency:** `ValidationError.codigo`/`.mensagem` and the five function names/signatures are the exact surface the internal API plan imports.

**Next:** `2026-08-19-optin-plan-05-secrets.md` (Secret Manager reader).
