"""Acesso a dados do agregado opt-in (SPEC-01 §5/§6) via CloudSqlClient (sem ORM)."""

import sqlalchemy
from django.utils import timezone
from ulid import ULID

from apps.optin.validation import conjuntos_se_sobrepoem, vigencias_se_sobrepoem
from shared.cloudsql_client import get_db


def proxima_referencia_externa(financiador_id: str, prefixo: str, sequencia: str) -> str:
    ano = timezone.localtime(timezone.now()).year
    with get_db(financiador_id)._engine.connect() as conn:
        seq = conn.execute(sqlalchemy.text(f"SELECT nextval('{sequencia}')")).scalar()
    return f"{prefixo}-{ano}-{seq:09d}"


def _com_filhas(financiador_id: str, optin: dict) -> dict:
    optin_id = optin["id"]
    optin["credenciadoras"] = [
        r["cnpj"] for r in get_db(financiador_id).table("optin_credenciadora").select("cnpj").eq("optin_id", optin_id).execute().data
    ]
    optin["arranjos"] = [
        r["codigo"] for r in get_db(financiador_id).table("optin_arranjo").select("codigo").eq("optin_id", optin_id).execute().data
    ]
    return optin


def criar_optin_pendente(financiador_id: str, dados: dict) -> dict:
    optin_id = f"opt_{ULID()}"
    referencia_externa = proxima_referencia_externa(financiador_id, "OPTIN", "optin_referencia_seq")

    with get_db(financiador_id)._engine.begin() as conn:
        conn.execute(sqlalchemy.text("""
            INSERT INTO optin (
                id, referencia_externa, origem, status, cnpj_solicitante, cnpj_financiador,
                documento_ufr, documento_ufr_tipo, documento_titular, data_assinatura,
                vigencia_inicio, vigencia_fim, carteira, evidencia_id
            ) VALUES (
                :id, :referencia_externa, 'OPTIN', 'PENDENTE', :cnpj_solicitante, :cnpj_financiador,
                :documento_ufr, :documento_ufr_tipo, :documento_titular, :data_assinatura,
                :vigencia_inicio, :vigencia_fim, :carteira, :evidencia_id
            )
        """), {
            "id": optin_id,
            "referencia_externa": referencia_externa,
            "cnpj_solicitante": dados["cnpj_solicitante"],
            "cnpj_financiador": dados["cnpj_financiador"],
            "documento_ufr": dados["documento_ufr"],
            "documento_ufr_tipo": dados["documento_ufr_tipo"],
            "documento_titular": dados["documento_titular"],
            "data_assinatura": dados["data_assinatura"],
            "vigencia_inicio": dados["vigencia_inicio"],
            "vigencia_fim": dados["vigencia_fim"],
            "carteira": dados.get("carteira"),
            "evidencia_id": dados["evidencia_id"],
        })
        for cnpj in dados["credenciadoras"]:
            conn.execute(
                sqlalchemy.text("INSERT INTO optin_credenciadora (optin_id, cnpj) VALUES (:optin_id, :cnpj)"),
                {"optin_id": optin_id, "cnpj": cnpj},
            )
        for codigo in dados["arranjos"]:
            conn.execute(
                sqlalchemy.text("INSERT INTO optin_arranjo (optin_id, codigo) VALUES (:optin_id, :codigo)"),
                {"optin_id": optin_id, "codigo": codigo},
            )

    return buscar_por_id(financiador_id, optin_id)


def buscar_por_id(financiador_id: str, optin_id: str):
    rows = get_db(financiador_id).table("optin").select("*").eq("id", optin_id).execute().data
    if not rows:
        return None
    return _com_filhas(financiador_id, rows[0])


def buscar_ativos_por_ufr(financiador_id: str, documento_ufr: str, documento_titular: str) -> list:
    candidatos = (
        get_db(financiador_id).table("optin").select("*")
        .eq("documento_ufr", documento_ufr)
        .eq("documento_titular", documento_titular)
        .eq("status", "ATIVO")
        .execute().data
    )
    return [_com_filhas(financiador_id, c) for c in candidatos]


def existe_optin_ativo_equivalente(financiador_id: str, documento_ufr, documento_titular, credenciadoras, arranjos, vigencia_inicio, vigencia_fim) -> bool:
    for candidato in buscar_ativos_por_ufr(financiador_id, documento_ufr, documento_titular):
        if not conjuntos_se_sobrepoem(set(candidato["credenciadoras"]), credenciadoras):
            continue
        if not conjuntos_se_sobrepoem(set(candidato["arranjos"]), arranjos):
            continue
        if vigencias_se_sobrepoem(candidato["vigencia_inicio"], candidato["vigencia_fim"], vigencia_inicio, vigencia_fim):
            return True
    return False


def atualizar_status(financiador_id: str, optin_id: str, status: str, protocolo_cerc: str = None) -> dict:
    dados = {"status": status, "atualizado_em": timezone.now()}
    if protocolo_cerc is not None:
        dados["protocolo_cerc"] = protocolo_cerc
    resultado = get_db(financiador_id).table("optin").update(dados).eq("id", optin_id).execute()
    return _com_filhas(financiador_id, resultado.data[0])


def atualizar_campos(financiador_id: str, optin_id: str, dados: dict) -> dict:
    dados = {**dados, "atualizado_em": timezone.now()}
    resultado = get_db(financiador_id).table("optin").update(dados).eq("id", optin_id).execute()
    return _com_filhas(financiador_id, resultado.data[0])


def atualizar_arranjos(financiador_id: str, optin_id: str, arranjos: list) -> None:
    get_db(financiador_id).table("optin_arranjo").delete().eq("optin_id", optin_id).execute()
    for codigo in arranjos:
        get_db(financiador_id).table("optin_arranjo").insert({"optin_id": optin_id, "codigo": codigo}).execute()


def atualizar_credenciadoras(financiador_id: str, optin_id: str, credenciadoras: list) -> None:
    get_db(financiador_id).table("optin_credenciadora").delete().eq("optin_id", optin_id).execute()
    for cnpj in credenciadoras:
        get_db(financiador_id).table("optin_credenciadora").insert({"optin_id": optin_id, "cnpj": cnpj}).execute()


def listar(financiador_id: str, filtros: dict, limit: int) -> list:
    query = get_db(financiador_id).table("optin").select("*")
    for campo in ("status", "documento_ufr", "origem", "carteira"):
        if filtros.get(campo):
            query = query.eq(campo, filtros[campo])
    if filtros.get("vigente_em"):
        query = query.lte("vigencia_inicio", filtros["vigente_em"]).gte("vigencia_fim", filtros["vigente_em"])
    resultado = query.order("criado_em", desc=True).limit(limit).execute().data
    return [_com_filhas(financiador_id, r) for r in resultado]


def arranjos_ativos(financiador_id: str) -> set:
    rows = get_db(financiador_id).table("dominio_arranjo").select("codigo").eq("ativo", True).execute().data
    return {r["codigo"] for r in rows}


def criar_optout_pendente(financiador_id: str, optin_id: str) -> dict:
    optout_id = f"optout_{ULID()}"
    referencia_externa = proxima_referencia_externa(financiador_id, "OPTOUT", "optout_referencia_seq")
    inserted = get_db(financiador_id).table("optout").insert({
        "id": optout_id,
        "optin_id": optin_id,
        "referencia_externa": referencia_externa,
        "status": "PENDENTE",
    }).execute()
    return inserted.data[0]


def confirmar_optout(financiador_id: str, optout_id: str, optin_id: str, protocolo_cerc: str) -> None:
    get_db(financiador_id).table("optout").update({"status": "CONFIRMADO", "protocolo_cerc": protocolo_cerc}).eq("id", optout_id).execute()
    get_db(financiador_id).table("optin").update({"status": "ENCERRADO", "atualizado_em": timezone.now()}).eq("id", optin_id).execute()


def rejeitar_optout(financiador_id: str, optout_id: str) -> None:
    get_db(financiador_id).table("optout").update({"status": "REJEITADO"}).eq("id", optout_id).execute()
