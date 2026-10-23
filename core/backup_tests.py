"""Optional native PostgreSQL + non-filesystem storage round trip."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from unittest import skipUnless

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from . import services as s
from .backup import archive_database, pg_environment
from .models import Evidence, Resource, User

sys.path.insert(0,str(settings.BASE_DIR/'tools'))
from restore_archive import inspect_archive, restore

PG_BIN=os.environ.get('CAMPUS_TEST_PG_BIN')


class BackupEnvironmentTests(SimpleTestCase):
    def test_libpq_tls_and_channel_binding_names_are_explicit(self):
        db=dict(HOST='example.invalid',PORT='5432',USER='simulated',PASSWORD='not-a-real-secret',
                OPTIONS=dict(sslmode='verify-full',channel_binding='require',sslrootcert='example.crt'))
        result=pg_environment(db,{'PGCHANNELBINDING':'prefer'})
        self.assertEqual(result['PGCHANNELBINDING'],'require')
        self.assertNotIn('PGCHANNEL_BINDING',result)
        self.assertEqual(result['PGSSLMODE'],'verify-full')
        self.assertEqual(result['PGSSLROOTCERT'],'example.crt')
        self.assertEqual(result['PGHOST'],db['HOST'])
        self.assertEqual(result['PGPASSWORD'],db['PASSWORD'])


@skipUnless(PG_BIN, 'Native archive/restore integration requires CAMPUS_TEST_PG_BIN; see cloud backup guide')
class PortableBackupTests(TransactionTestCase):
    def setUp(self):
        call_command('seed_demo',verbosity=0)
        self.directory=tempfile.TemporaryDirectory(prefix='campus-cloud-backup-test-')
        self.addCleanup(self.directory.cleanup)
        self.folder=Path(self.directory.name)
        self.storage=InMemoryStorage()
        self.student=User.objects.get(username='student');self.admin=User.objects.get(username='admin')
        self.resource=Resource.objects.get(code='EQ-OSC-01')
        start=timezone.localtime(timezone.now()+timezone.timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        booking=s.create_reservation(self.student,self.resource.pk,start,start+timezone.timedelta(hours=1),'隔离备份模拟实验')
        s.checkout(self.admin,booking.pk,self.student.school_no,self.resource.code,at=start+timezone.timedelta(minutes=5))
        s.return_equipment(self.admin,booking.pk,self.resource.code,'模拟完好',at=start+timezone.timedelta(minutes=35))
        incident=s.report_incident(self.student,self.resource.pk,'FAULT','隔离备份模拟报告；不存在真实故障')
        body=b'isolated evidence bytes; not a real document'
        name=self.storage.save('evidence/simulated.txt',ContentFile(body))
        self.evidence=Evidence.objects.create(incident=incident,uploaded_by=self.student,file=name,original_name='simulated.txt',sha256=hashlib.sha256(body).hexdigest())
        session=SessionStore();session['acceptance']='simulated';session.create()

    def test_snapshot_and_non_filesystem_evidence_restore_to_new_database(self):
        archive=self.folder/'joint-backup.zip'
        result=archive_database(PG_BIN,archive,storage=self.storage)
        manifest=inspect_archive(archive)
        self.assertEqual(manifest['snapshot_method'],'pg_export_snapshot')
        self.assertEqual(manifest['session_data'],'excluded')
        self.assertEqual(manifest['table_counts']['core_reservation'],1)
        self.assertEqual(manifest['table_counts']['core_loan'],1)
        self.assertEqual(manifest['table_counts']['core_returnrecord'],1)
        self.assertFalse(result['restore_verified'])
        db=settings.DATABASES['default']
        # This helper refuses any existing target; it never restores over source.
        import secrets
        target='restore_check_'+secrets.token_hex(8)
        previous=os.environ.get('PGPASSWORD');os.environ['PGPASSWORD']=db['PASSWORD']
        try:receipt=restore(archive,PG_BIN,target,self.folder/'restored',db['HOST'],int(db['PORT']),db['USER'])
        finally:
            if previous is None:os.environ.pop('PGPASSWORD',None)
            else:os.environ['PGPASSWORD']=previous
        self.assertTrue(receipt['table_counts_verified'])
        self.assertEqual(receipt['sessions'],0)
        restored=self.folder/'restored'/self.evidence.file.name
        self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(),self.evidence.sha256)
        with self.assertRaisesMessage(ValueError,'目标归档已存在'):
            archive_database(PG_BIN,archive,storage=self.storage)

    def test_missing_or_corrupt_object_never_publishes_a_backup(self):
        self.storage.delete(self.evidence.file.name)
        archive=self.folder/'missing.zip'
        with self.assertRaises(FileNotFoundError):archive_database(PG_BIN,archive,storage=self.storage)
        self.assertFalse(archive.exists())
        self.storage.save(self.evidence.file.name,ContentFile(b'changed bytes'))
        with self.assertRaisesMessage(ValueError,'附件摘要'):
            archive_database(PG_BIN,self.folder/'corrupt.zip',storage=self.storage)
        self.assertFalse((self.folder/'corrupt.zip').exists())

    def test_size_limit_and_public_repository_destination_are_rejected(self):
        with self.assertRaises(ValueError):archive_database(PG_BIN,self.folder/'too-small.zip',storage=self.storage,maximum_bytes=1)
        self.assertFalse((self.folder/'too-small.zip').exists())
        with self.assertRaisesMessage(ValueError,'不能进入公开源码'):
            archive_database(PG_BIN,settings.BASE_DIR/'never-publish-backup.zip',storage=self.storage)
