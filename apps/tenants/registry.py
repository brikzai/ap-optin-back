"""Registro de tenants — quem existe (TENANT_IDS) e em que banco cada um vive.

Spec: docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md §2, §3.2.
Nome de banco é SEMPRE ap_<cnpj>; a validação aqui e a guarda em get_db (§5)
tornam impossível dois tenants no mesmo banco.
"""

import re
from typing import Optional

from sqlalchemy.engine import make_url

from shared.secrets import get_secret
from shared.tenant_config import get_tenant_config

_CNPJ = re.compile(r"^\d{14}$")


class RegistroTenantsInvalido(RuntimeError):
    pass


def tenant_ids() -> list[str]:
    bruto = get_secret("TENANT_IDS")
    ids = []
    for parte in bruto.split(","):
        cnpj = parte.strip()
        if cnpj and cnpj not in ids:
            ids.append(cnpj)
    if not ids:
        raise RegistroTenantsInvalido("TENANT_IDS está vazio")
    return ids


def nome_banco(financiador_id: str) -> str:
    if not _CNPJ.match(financiador_id or ""):
        raise RegistroTenantsInvalido(f"financiador_id inválido: {financiador_id!r} (esperado CNPJ com 14 dígitos)")
    return f"ap_{financiador_id}"


def nome_banco_da_config(config: dict) -> str:
    if config.get("database_url"):
        return make_url(config["database_url"]).database or ""
    return config.get("cloudsql_db_name") or ""


def chave_banco(config: dict) -> tuple:
    if config.get("database_url"):
        url = make_url(config["database_url"])
        return ("url", url.host, url.port, url.database)
    return ("cloudsql", config.get("cloudsql_connection_name"), config.get("cloudsql_db_name"))


def validar_config(financiador_id: str, config: dict) -> None:
    esperado = nome_banco(financiador_id)
    real = nome_banco_da_config(config)
    if real != esperado:
        raise RegistroTenantsInvalido(
            f"tenant {financiador_id}: banco configurado é {real!r}, esperado {esperado!r} (spec §3.2)"
        )


def detectar_colisao(financiador_id: str, config: dict) -> Optional[str]:
    minha = chave_banco(config)
    for outro in tenant_ids():
        if outro == financiador_id:
            continue
        if chave_banco(get_tenant_config(outro)) == minha:
            return outro
    return None
