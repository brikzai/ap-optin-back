# Migrations do Schema

## Nomeação

Cada migration é um arquivo SQL com o padrão `NNNN_descricao_snake.sql`:
- `NNNN`: quatro dígitos únicos e crescentes (ex: `0001`, `0002`, etc.)
- Números duplicados são rejeitados pelo runner
- Arquivos `.md` são ignorados e não processados

## Forward-only: nunca edite um arquivo já aplicado

Uma vez que uma migration é aplicada a um banco, ela nunca deve ser editada. O runner mantém um ledger (`schema_aplicado`) que armazena o checksum de cada arquivo aplicado:
- Se o checksum do arquivo mudou, a migration é rejeitada com erro `MigrationEditada`
- Para corrigir ou adicionar schema, crie o próximo arquivo numerado (ex: `0002_*.sql`)
- Cada arquivo é aplicado dentro de uma única transação; se falhar, toda a migration é revertida e **não é registrada** no ledger

## Caveat: pg8000 e o caractere `%`

O runner aplica as statements via `conn.exec_driver_sql` do PostgreSQL driver `pg8000`. Nesse driver, `%` é um marcador de parâmetro e precisa ser escapado:

- Uma migration contendo `LIKE '%x'` deve ser escrita `LIKE '%%x'`
- O `%%` é interpretado como um `%` literal

Planeje com cuidado antes de incluir patterns com `%` em `LIKE`, `~`, ou outras operações com strings.

## Como aplicar

```bash
# Todos os tenants
python manage.py migrate_tenants

# Um tenant específico
python manage.py migrate_tenants --tenant 12345678000199

# Prévia sem fazer nada (dry-run)
python manage.py migrate_tenants --dry-run
```

Dry-run não escreve nem no ledger nem no schema — apenas valida.

## Referências

- Local setup: veja `docs/dev-setup.md`
- Design e especificação: `docs/superpowers/specs/2026-09-02-database-multitenant-migrations-design.md` §4
