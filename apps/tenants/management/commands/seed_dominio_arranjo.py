from django.core.management.base import BaseCommand
from sqlalchemy import text

from apps.tenants import registry
from apps.tenants.dominio_arranjo_cerc import ARRANJOS_CERC
from shared.cloudsql_client import get_db


class Command(BaseCommand):
    help = (
        "Seed de dominio_arranjo: curinga 99T + os 47 códigos oficiais da CERC "
        "(apps/tenants/dominio_arranjo_cerc.py). Idempotente. Spec 2026-09-02 §6.3."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)

    def handle(self, *args, **opts):
        cnpj = opts["tenant"]
        codigos = [("99T", "Todos os arranjos")] + ARRANJOS_CERC
        with get_db(cnpj)._engine.begin() as conn:
            for codigo, descricao in codigos:
                conn.execute(
                    text("""
                        INSERT INTO dominio_arranjo (codigo, descricao, ativo, atualizado_em)
                        VALUES (:codigo, :descricao, true, now())
                        ON CONFLICT (codigo) DO NOTHING
                    """),
                    {"codigo": codigo, "descricao": descricao},
                )
        self.stdout.write(
            self.style.SUCCESS(f"[seed] {registry.nome_banco(cnpj)}: dominio_arranjo ok ({len(codigos)} códigos)")
        )
