from django.core.management.base import BaseCommand, CommandError

from apps.tenants import provisioning, registry


class Command(BaseCommand):
    help = "Cria o banco ap_<cnpj>, grava tenant_info e aplica as migrations (spec 2026-09-02 §3)."

    def add_arguments(self, parser):
        parser.add_argument("cnpj")
        parser.add_argument("--existente", action="store_true", help="reaproveita banco já criado (ex.: após restore)")

    def handle(self, *args, **opts):
        try:
            aplicadas = provisioning.provisionar(opts["cnpj"], existente=opts["existente"])
        except (registry.RegistroTenantsInvalido, provisioning.BancoJaExiste, provisioning.TenantInfoDivergente) as e:
            raise CommandError(str(e))
        nome = registry.nome_banco(opts["cnpj"])
        for a in aplicadas:
            self.stdout.write(f"[provisionar] {nome}: {a} aplicada")
        self.stdout.write(self.style.SUCCESS(f"[provisionar] {nome}: pronto ({len(aplicadas)} migration(s))"))
