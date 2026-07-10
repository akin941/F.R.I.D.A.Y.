# Append-only enforcement at the database level (ADR-004).
# Admin read-only is convenience; this trigger is the actual guarantee.

from django.db import migrations

CREATE_SQL = """
CREATE OR REPLACE FUNCTION audit_auditlog_protect()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditlog is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditlog_append_only
BEFORE UPDATE OR DELETE ON audit_auditlog
FOR EACH ROW EXECUTE FUNCTION audit_auditlog_protect();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS audit_auditlog_append_only ON audit_auditlog;
DROP FUNCTION IF EXISTS audit_auditlog_protect();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
