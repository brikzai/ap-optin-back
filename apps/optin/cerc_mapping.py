"""Interpreta o array 207 multi-status de /opt_in e /opt_out da CERC e
decide a transição de estado local (SPEC-01 §7.1/§7.2, §9.1).

Nunca trata o HTTP 207 como sucesso global (SPEC-01 §4.1) — cada item é
interpretado individualmente e correlacionado por `referenciaExterna`.
"""

# 104803/106803 = "já informado" -> sucesso idempotente após reconciliação
# (§7.1/§7.2) mesmo vindo com status="1" (IT-03 da SPEC-01 §11.2).
CODIGOS_IDEMPOTENTES_OPT_IN = {"104803"}
CODIGOS_IDEMPOTENTES_OPT_OUT = {"106803"}


class ResultadoItemCerc:
    def __init__(self, status_local: str, protocolo: str = None, erro_codigo: str = None, erro_mensagem: str = None):
        self.status_local = status_local
        self.protocolo = protocolo
        self.erro_codigo = erro_codigo
        self.erro_mensagem = erro_mensagem


def _primeiro_erro(item: dict):
    erros = item.get("erros") or []
    if not erros:
        return None, None
    primeiro = erros[0]
    return primeiro.get("codigo"), primeiro.get("mensagem")


def interpretar_item_opt_in(item: dict) -> ResultadoItemCerc:
    if item.get("status") == "0":
        return ResultadoItemCerc("ATIVO", protocolo=item.get("protocolo"))

    codigo, mensagem = _primeiro_erro(item)
    if codigo in CODIGOS_IDEMPOTENTES_OPT_IN:
        return ResultadoItemCerc("ATIVO", protocolo=item.get("protocolo"), erro_codigo=codigo)
    return ResultadoItemCerc("REJEITADO", erro_codigo=codigo, erro_mensagem=mensagem)


def interpretar_item_opt_out(item: dict) -> ResultadoItemCerc:
    if item.get("status") == "0":
        return ResultadoItemCerc("CONFIRMADO", protocolo=item.get("protocolo"))

    codigo, mensagem = _primeiro_erro(item)
    if codigo in CODIGOS_IDEMPOTENTES_OPT_OUT:
        return ResultadoItemCerc("CONFIRMADO", protocolo=item.get("protocolo"), erro_codigo=codigo)
    return ResultadoItemCerc("REJEITADO", erro_codigo=codigo, erro_mensagem=mensagem)


def correlacionar_por_referencia(itens: list, referencia_externa: str) -> dict:
    for item in itens:
        if item.get("referenciaExterna") == referencia_externa:
            return item
    raise KeyError(f"referenciaExterna {referencia_externa!r} não encontrada na resposta da CERC")
