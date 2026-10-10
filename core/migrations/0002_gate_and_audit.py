from django.db import migrations

def initialise(apps,schema_editor):
    apps.get_model('core','BusinessConfig').objects.get_or_create(pk=1)

class Migration(migrations.Migration):
    dependencies=[('core','0001_initial')]
    operations=[migrations.RunPython(initialise,migrations.RunPython.noop),
        migrations.RunSQL('''
CREATE FUNCTION campus_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit history is immutable'; END; $$;
CREATE TRIGGER campus_audit_no_update_delete BEFORE UPDATE OR DELETE ON core_audit
FOR EACH ROW EXECUTE FUNCTION campus_audit_immutable();
''',reverse_sql='DROP TRIGGER campus_audit_no_update_delete ON core_audit; DROP FUNCTION campus_audit_immutable();')]
