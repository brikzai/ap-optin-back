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


def conjuntos_se_sobrepoem(a: set, b: set) -> bool:
    """Compara dois conjuntos (credenciadoras ou arranjos) tratando '99T' como
    curinga universal (SPEC-01 §2.3/§5.6): se qualquer lado contiver '99T',
    há sobreposição total."""
    if "99T" in a or "99T" in b:
        return True
    return bool(a & b)


def vigencias_se_sobrepoem(inicio_a, fim_a, inicio_b, fim_b) -> bool:
    """Interseção de intervalos fechados [inicio, fim] (SPEC-01 §5.6)."""
    return inicio_a <= fim_b and inicio_b <= fim_a


def mascarar_documento(documento: str) -> str:
    """SPEC-01 §8: documentos mascarados em log (ex.: '12345678****99')."""
    tamanho = len(documento)
    if tamanho <= 4:
        return "*" * tamanho
    prefixo = min(8, max(2, tamanho - 6))
    sufixo_tamanho = min(2, tamanho - prefixo)
    meio = "*" * (tamanho - prefixo - sufixo_tamanho)
    return documento[:prefixo] + meio + documento[tamanho - sufixo_tamanho:]


def validar_arranjos(lista: list, ativos: set) -> None:
    """VAL005 — SPEC-01 §7.1 104015/104016. '99T' sempre aceito sem checar domínio."""
    for codigo in lista:
        if codigo != "99T" and codigo not in ativos:
            raise ValidationError("VAL005", f"arranjo fora do domínio vigente: {codigo}")


def validar_evidencia(evidencia_id) -> None:
    """VAL008 — só a presença é verificada aqui; acessibilidade do storage de
    evidências fica fora de escopo (não há storage especificado na SPEC-01)."""
    if not evidencia_id:
        raise ValidationError("VAL008", "evidenciaAutorizacaoId ausente")
