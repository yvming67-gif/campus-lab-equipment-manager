"""Runtime contract checks; no contact with external people or providers."""
import json,tempfile,zipfile,hashlib
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta
from django.test import override_settings,TestCase
from django.core.exceptions import ImproperlyConfigured
from . import tests as fixtures
from .models import *
from . import services as s

class OperationsRegression(TestCase):
    setUpTestData=classmethod(fixtures.BusinessTests.setUpTestData.__func__)
    setUp=fixtures.BusinessTests.setUp
    book=fixtures.BusinessTests.book

    def test_all_seven_lists_reach_items_beyond_one_hundred(self):
        now=s.timezone.now()
        resources=[Resource(code=f'PAGE-{i:03}',name=f'模拟分页设备{i}',kind='EQUIPMENT',lab=self.eq.lab) for i in range(105)]
        Resource.objects.bulk_create(resources)
        EquipmentDetail.objects.bulk_create([EquipmentDetail(resource=x,category='分页模拟') for x in resources])
        Reservation.objects.bulk_create([Reservation(applicant=self.student,resource=self.eq,starts_at=self.start,ends_at=self.end,purpose='分页模拟',status='REJECTED') for _ in range(105)])
        incidents=[Incident(resource=self.eq,reporter=self.admin,kind='FAULT',description='分页模拟',occurred_at=now,blocks_service=False) for _ in range(105)]
        Incident.objects.bulk_create(incidents)
        WorkOrder.objects.bulk_create([WorkOrder(incident=x,assigned_to=self.tech,created_by=self.admin) for x in incidents])
        events=[AuditEvent(actor=self.admin,entity_type='resource',entity_id=self.eq.pk,lab=self.eq.lab,action='PAGE_SAMPLE',reason='分页模拟') for _ in range(105)]
        AuditEvent.objects.bulk_create(events)
        notifications=[Notification(audit=x,recipient=self.admin,kind='PAGE_SAMPLE',content='分页模拟') for x in events]
        Notification.objects.bulk_create(notifications)
        Delivery.objects.bulk_create([Delivery(notification=x) for x in notifications])
        for path,user in [('/resources/',self.admin),('/bookings/',self.admin),('/incidents/',self.admin),('/work/',self.admin),('/notifications/',self.admin),('/audit/',self.admin),('/deliveries/',self.sys)]:
            with self.subTest(path=path):
                self.client.force_login(user)
                response=self.client.get(path,{'page':'4'})
                self.assertEqual(response.status_code,200)
                page=response.context['page'];self.assertGreater(page.paginator.count,100)
                self.assertEqual(page.number,4);self.assertTrue(page.object_list)
        response=self.client.get('/resources/',{'q':'PAGE','page':'2'})
        self.assertIn('q=PAGE',response.context['filters'])

    def test_missing_quantity_retry_preserves_one_return_fact(self):
        b=self.book();loan=s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        item=loan.items.first();self.assertIsNotNone(item)
        quantities={item.pk:0}
        original=s.return_equipment(self.admin,b.pk,self.eq.code,'实收缺件',return_quantities=quantities,at=self.end)
        retry=s.return_equipment(self.admin,b.pk,self.eq.code,'实收缺件',return_quantities=quantities,at=self.end)
        self.assertEqual(original.pk,retry.pk);self.assertEqual(ReturnRecord.objects.count(),1)
    @override_settings(DEMO_MODE=False,CAMPUS_IDENTITY_ADAPTER='')
    def test_missing_school_adapter_fails_closed(self):
        from .adapters import identity_adapter
        with self.assertRaises(ImproperlyConfigured):identity_adapter()
        self.client.post('/login/');response=self.client.get('/identity/')
        self.assertEqual(response.status_code,503)
        self.assertFalse(self.client.session.get('_auth_user_id'))

    @override_settings(DEMO_MODE=False,CAMPUS_IDENTITY_ADAPTER='core.operations_tests.TestIdentity')
    def test_school_identity_requires_state_and_existing_active_mapping(self):
        self.client.post('/login/');response=self.client.get('/identity/')
        self.assertEqual(response.status_code,302);self.assertTrue(response.url.startswith('https://'))
        self.assertEqual(self.client.get('/auth/callback/').status_code,302)
        self.assertEqual(int(self.client.session['_auth_user_id']),self.student.pk)
        self.assertEqual(self.client.get('/auth/callback/').status_code,403)
        self.assertTrue(AuditEvent.objects.filter(action='LOGIN',new_state__identity='SCHOOL').exists())

    @override_settings(DEMO_MODE=False,CAMPUS_MESSAGE_ADAPTER='')
    def test_unconfigured_external_send_retries_without_mock_success(self):
        booking=self.book();task=Delivery.objects.filter(notification__recipient=self.student,channel='EXTERNAL').first()
        self.assertIsNotNone(task);self.assertTrue(s.process_delivery(task.pk))
        task.refresh_from_db();self.assertEqual(task.status,'RETRY')
        self.assertFalse(MockReceipt.objects.filter(delivery=task).exists())
        self.assertEqual(Reservation.objects.get(pk=booking.pk).status,'CONFIRMED')

    @override_settings(DEMO_MODE=False,CAMPUS_MESSAGE_ADAPTER='core.operations_tests.fake_receipt')
    def test_real_adapter_contract_uses_stable_delivery_id(self):
        self.book();task=Delivery.objects.filter(channel='EXTERNAL').first()
        self.assertTrue(s.process_delivery(task.pk));task.refresh_from_db();self.assertEqual(task.status,'SENT')
        self.assertFalse(s.process_delivery(task.pk));self.assertEqual(task.attempts.count(),1)

    def test_escalation_is_deduplicated_and_visible_to_backup_manager(self):
        from .operations import escalations
        b=self.book(self.teacher,self.room)
        other=User.objects.create_user(username='backup',school_no='BACKUP',display_name='模拟备岗')
        UserRole.objects.create(user=other,role=Role.objects.get(code='LAB_ADMIN'));LabGrant.objects.create(user=other,lab=self.room.lab)
        escalations(self.start-timedelta(minutes=30));escalations(self.start-timedelta(minutes=20))
        self.assertEqual(AuditEvent.objects.filter(action='APPROVAL_ESCALATION',entity_id=b.pk).count(),1)
        self.assertTrue(Notification.objects.filter(recipient=other,kind='APPROVAL_ESCALATION').exists())

    def test_health_is_private_and_degraded_without_worker_or_backup(self):
        self.client.force_login(self.student);self.assertEqual(self.client.get('/operations/health/').status_code,403)
        self.client.force_login(self.sys);self.assertEqual(self.client.get('/operations/health/').status_code,503)
        with tempfile.TemporaryDirectory() as folder,override_settings(LOCAL=Path(folder)):
            now=s.timezone.now();JobHeartbeat.objects.create(name='business-jobs',last_success=now)
            (Path(folder)/'last-backup.json').write_text(json.dumps({'last_success':now.isoformat()}))
            self.assertEqual(self.client.get('/operations/health/').status_code,200)

    def test_restore_archive_rejects_escape_and_tampering(self):
        import sys
        from django.conf import settings
        sys.path.insert(0,str(settings.BASE_DIR/'tools'))
        from restore_archive import inspect_archive
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'archive.zip';dump=b'sample'
            base={'database_sha256':hashlib.sha256(dump).hexdigest(),'session_data':'excluded','attachments':[]}
            for extra in ['../outside','media/../../outside',None]:
                with zipfile.ZipFile(p,'w') as z:
                    z.writestr('database.dump',b'tampered' if extra is None else dump);z.writestr('manifest.json',json.dumps(base))
                    if extra:z.writestr(extra,b'bad')
                with self.assertRaises(ValueError):inspect_archive(p)

    def test_aud0044_owned_room_query_matches_fixed_equipment_submission(self):
        EquipmentDetail.objects.filter(resource=self.eq).update(portable=False,access_room=self.room)
        self.eq.refresh_from_db()
        room=self.book(self.teacher,self.room);s.approve(self.admin,room.pk,True,'同意')
        self.assertTrue(s.availability(self.eq,self.start,self.end,applicant=self.teacher)[0])
        self.assertFalse(s.availability(self.eq,self.start,self.end,applicant=self.student)[0])
        self.client.force_login(self.teacher)
        response=self.client.get('/resources/',{'q':self.eq.code,'starts_at':self.start.strftime('%Y-%m-%dT%H:%M'),'ends_at':self.end.strftime('%Y-%m-%dT%H:%M')})
        self.assertContains(response,'时段可预约')
        self.assertEqual(self.book(self.teacher,self.eq).status,'CONFIRMED')

    def test_aud0047_cancel_and_expiry_end_current_review_flag(self):
        b=self.book();Reservation.objects.filter(pk=b.pk).update(review_required=True,review_reason='风险变化')
        s.cancel(self.student,b.pk,'课程取消');b.refresh_from_db()
        self.assertFalse(b.review_required);self.assertEqual(b.review_reason,'')
        self.assertTrue(AuditEvent.objects.filter(entity_id=b.pk,action='REVIEW_ENDED',old_state__review_required=True).exists())
        b=self.book(self.teacher);Reservation.objects.filter(pk=b.pk).update(review_required=True,review_reason='风险变化')
        s.expire_booking(b.pk,at=self.start+timedelta(minutes=31));b.refresh_from_db()
        self.assertEqual(b.status,'EXPIRED');self.assertFalse(b.review_required)

class TestIdentity:
    @staticmethod
    def begin(request,state,callback):return 'https://identity.example.edu/authorize'
    @staticmethod
    def complete(request,state,callback):return {'state':state,'school_no':User.objects.get(username='student').school_no}

def fake_receipt(**values):
    assert values['idempotency_key'].isdigit()
    return {'accepted':True,'receipt_id':'simulated-contract-only'}
