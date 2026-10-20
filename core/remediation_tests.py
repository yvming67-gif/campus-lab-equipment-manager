"""Audit counterexamples. Every assertion states an externally observable invariant."""
from datetime import timedelta
import uuid
from django.test import TestCase, Client
from django.core.management import call_command
from django.utils import timezone
from .models import *
from . import services as s


class RemediationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    def setUp(self):
        self.student = User.objects.get(username='student')
        self.teacher = User.objects.get(username='teacher')
        self.admin = User.objects.get(username='admin')
        self.sys = User.objects.get(username='sysadmin')
        self.eq = Resource.objects.get(code='EQ-OSC-01')
        self.room = Resource.objects.get(code='ROOM-101')
        self.start = timezone.localtime(timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        self.end = self.start+timedelta(hours=1)

    def book(self, user=None, resource=None):
        return s.create_reservation(user or self.student,(resource or self.eq).pk,self.start,self.end,'整改回归')

    def test_aud0001_inactive_borrower_cannot_collect(self):
        b=self.book();User.objects.filter(pk=self.student.pk).update(is_active=False)
        with self.assertRaises(s.BusinessError):
            s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        self.assertFalse(Loan.objects.exists())

    def test_aud0001_teacher_role_rechecked_at_room_start(self):
        b=self.book(self.teacher,self.room);s.approve(self.admin,b.pk,True,'批准')
        UserRole.objects.filter(user=self.teacher,role__code='TEACHER').delete()
        with self.assertRaises(s.BusinessError):
            s.start_room(self.admin,b.pk,self.teacher.school_no,1,'核验',at=self.start)
        self.assertFalse(RoomUse.objects.exists())

    def test_aud0001_return_allowed_after_deactivation(self):
        b=self.book();s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        User.objects.filter(pk=self.student.pk).update(is_active=False)
        result=s.return_equipment(self.admin,b.pk,self.eq.code,'已接收',at=self.end)
        self.assertEqual(result.loan.reservation_id,b.pk)

    def test_aud0006_query_rejects_objective_invalid_times(self):
        for start,end in [(self.start+timedelta(minutes=1),self.end+timedelta(minutes=1)),(self.start,self.start+timedelta(hours=9)),(self.start+timedelta(days=15),self.end+timedelta(days=15)),(self.start-timedelta(days=3),self.end-timedelta(days=3))]:
            with self.subTest(start=start):self.assertFalse(s.availability(self.eq,start,end)[0])

    def test_aud0009_bad_cancel_version_is_not_500(self):
        b=self.book();c=Client(raise_request_exception=False);c.force_login(self.student)
        r=c.post(f'/bookings/{b.pk}/cancel/',{'version':'bad','reason':'取消'})
        self.assertLess(r.status_code,500);b.refresh_from_db();self.assertEqual(b.status,'CONFIRMED')

    def test_aud0009_bad_delivery_id_is_not_500(self):
        c=Client(raise_request_exception=False);c.force_login(self.admin)
        for pk in ['bad','',None]:
            with self.subTest(pk=pk):
                r=c.post('/deliveries/',{'pk':pk or '', 'reason':'联系'})
                self.assertLess(r.status_code,500)

    def test_aud0011_explicit_seed_preserves_revocation(self):
        UserRole.objects.filter(user=self.admin).delete();LabGrant.objects.filter(user=self.admin).delete()
        call_command('seed_demo',verbosity=0)
        self.assertFalse(self.admin.has_role('LAB_ADMIN'));self.assertFalse(LabGrant.objects.filter(user=self.admin).exists())

    def test_aud0028_resource_audit_contains_old_details(self):
        data=dict(code=self.eq.code,name=self.eq.name,kind='EQUIPMENT',lab=self.eq.lab,description='',category='示波器',risk='HIGH_VALUE',qualification=None,accessories='POWER,电源,2',reason='安全分级调整')
        s.save_resource(self.admin,data,self.eq.pk)
        ev=AuditEvent.objects.filter(action='RESOURCE_EDIT',entity_id=self.eq.pk).first()
        self.assertEqual(ev.old_state['equipment']['risk'],'NORMAL')
        self.assertEqual(len(ev.old_state['accessories']),2)

    def test_aud0010_grace_message_uses_policy(self):
        b=self.book();Policy.objects.filter(pk=1).update(grace_minutes=60)
        with self.assertRaisesMessage(s.BusinessError,'60'):
            s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start+timedelta(minutes=61))
