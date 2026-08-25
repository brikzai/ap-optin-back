"""Acesso a dados da entidade cliente — pré-requisito mínimo para gerir opt-ins
(design: docs/superpowers/specs/2026-08-25-frontend-integration-design.md §2)."""

from ulid import ULID

from shared.cloudsql_client import get_db


def criar(financiador_id: str, dados: dict) -> dict:
    cliente_id = f"cli_{ULID()}"
    inserted = get_db(financiador_id).table("cliente").insert({
        "id": cliente_id,
        "documento": dados["documento"],
        "documento_tipo": dados["documento_tipo"],
        "nome": dados["nome"],
        "email": dados.get("email"),
        "telefone": dados.get("telefone"),
    }).execute()
    return inserted.data[0]


def buscar_por_documento(financiador_id: str, documento: str):
    rows = get_db(financiador_id).table("cliente").select("*").eq("documento", documento).execute().data
    return rows[0] if rows else None


def buscar_por_id(financiador_id: str, cliente_id: str):
    rows = get_db(financiador_id).table("cliente").select("*").eq("id", cliente_id).execute().data
    return rows[0] if rows else None


def listar(financiador_id: str, filtros: dict, limit: int) -> list:
    query = get_db(financiador_id).table("cliente").select("*")
    if filtros.get("documento"):
        query = query.eq("documento", filtros["documento"])
    return query.order("criado_em", desc=True).limit(limit).execute().data
