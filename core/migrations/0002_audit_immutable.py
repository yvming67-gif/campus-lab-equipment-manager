from django.db import migrations
SQL = """
CREATE FUNCTION core_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit events are append-only'; END; $$;
CREATE TRIGGER core_audit_no_change BEFORE UPDATE OR DELETE ON core_auditevent
FOR EACH ROW EXECUTE FUNCTION core_audit_immutable();
"""
REVERSE = "DROP TRIGGER IF EXISTS core_audit_no_change ON core_auditevent; DROP FUNCTION IF EXISTS core_audit_immutable();"
class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
