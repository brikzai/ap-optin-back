from django.core.management.base import BaseCommand, CommandError

from apps.tenants import registry, runner
from shared.cloudsql_client import _create_engine, _verificar_tenant
from shared.tenant_config import get_tenant_config


class Command(BaseCommand):
    help = "Aplica db/migrations/*.sql pendentes em cada tenant de TENANT_IDS (spec 2026-09-02 §4)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="só este CNPJ")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        ids = [opts["tenant"]] if opts["tenant"] else registry.tenant_ids()
        falhas = []
        for cnpj in ids:
            nome = registry.nome_banco(cnpj)
            try:
                config = get_tenant_config(cnpj)
                registry.validar_config(cnpj, config)
                engine = _create_engine(config)
                try:
                    _verificar_tenant(engine, cnpj)
                    aplicadas = runner.aplicar(engine, runner.MIGRATIONS_DIR, dry_run=opts["dry_run"])
                finally:
                    engine.dispose()
            except Exception as e:  # um tenant não pode impedir os outros
                falhas.append(cnpj)
                self.stderr.write(f"[migrate] {nome}: ERRO {e}")
                continue
            verbo = "seria aplicada" if opts["dry_run"] else "aplicada"
            for a in aplicadas:
                self.stdout.write(f"[migrate] {nome}: {a} {verbo}")
            if not aplicadas:
                self.stdout.write(f"[migrate] {nome}: nada pendente")
        if falhas:
            raise CommandError(f"falhou em: {', '.join(falhas)}")
