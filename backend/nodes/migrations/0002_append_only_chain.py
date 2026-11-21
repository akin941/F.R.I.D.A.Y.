# Phase 9 brief D8: the node invocation chain is append-only at the database
# level (the audit 0002 pattern). No exception: not for cascades (every FK is
# PROTECT), not for a SET_NULL (an UPDATE), not for an archival path (a later
# phase defines a chain-aware one in its own ADR). Also seeds the single
# NodeAuditHead row that serializes appends.

from django.db import migrations

CREATE_SQL = """
CREATE OR REPLACE FUNCTION nodes_nodeinvocation_protect()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'nodes_nodeinvocation is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER nodes_nodeinvocation_append_only
BEFORE UPDATE OR DELETE ON nodes_nodeinvocation
FOR EACH ROW EXECUTE FUNCTION nodes_nodeinvocation_protect();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS nodes_nodeinvocation_append_only ON nodes_nodeinvocation;
DROP FUNCTION IF EXISTS nodes_nodeinvocation_protect();
"""


def seed_head(apps, schema_editor):
    head = apps.get_model("nodes", "NodeAuditHead")
    head.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
        migrations.RunPython(seed_head, migrations.RunPython.noop),
    ]
