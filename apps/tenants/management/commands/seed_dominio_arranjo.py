from django.core.management.base import BaseCommand
from sqlalchemy import text

from apps.tenants import registry
from shared.cloudsql_client import get_db


class Command(BaseCommand):
    help = "Seed mínimo de dominio_arranjo (código 99T = todos). Idempotente. Spec 2026-09-02 §6.3."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)

    def handle(self, *args, **opts):
        cnpj = opts["tenant"]
        with get_db(cnpj)._engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dominio_arranjo (codigo, descricao, ativo, atualizado_em)
                VALUES ('99T', 'Todos os arranjos', true, now())
                ON CONFLICT (codigo) DO NOTHING
            """))
        self.stdout.write(self.style.SUCCESS(f"[seed] {registry.nome_banco(cnpj)}: dominio_arranjo ok"))
