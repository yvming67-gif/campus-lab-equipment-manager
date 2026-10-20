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

    def test_aud0002_upgrade_requires_recheck_preserves_original_decision(self):
        from .coordination import recheck
        b=self.book();decision=b.decision.pk
        s.save_resource(self.admin,dict(code=self.eq.code,name=self.eq.name,kind='EQUIPMENT',lab=self.eq.lab,description='',category='示波器',risk='HIGH_VALUE',qualification=None,accessories='',reason='安全升级'),self.eq.pk)
        b.refresh_from_db();self.assertTrue(b.review_required);self.assertEqual(b.decision.pk,decision)
        with self.assertRaises(s.BusinessError):s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        recheck(self.admin,b.pk,True,'用途与安全条件复核',expected=b.version)
        self.assertEqual(s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start).reservation_id,b.pk)
        self.assertEqual(ApprovalDecision.objects.filter(reservation=b).count(),1)
        self.assertEqual(ReservationReview.objects.filter(reservation=b).count(),1)

    def test_aud0005_unrelated_report_does_not_block_but_authorized_report_does(self):
        report=s.report_incident(self.student,self.eq.pk,'FAULT','普通待核实')
        self.eq.refresh_from_db();self.assertFalse(report.blocks_service);self.assertEqual(self.eq.service_status,'NORMAL')
        s.report_incident(self.admin,self.eq.pk,'FAULT','授权紧急检查')
        self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,'ISOLATED')

    def test_aud0007_delay_coordination_releases_no_extra_time(self):
        from .coordination import coordinate
        b=self.book();other=User.objects.get(username='student2')
        next_booking=s.create_reservation(other,self.eq.pk,self.end,self.end+timedelta(hours=1),'后续使用')
        s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        coordinate(self.admin,next_booking.pk,'WAIT','前人未归还',self.end+timedelta(minutes=1),at=self.end+timedelta(minutes=1))
        self.assertFalse(s.expire_booking(next_booking.pk,at=self.end+timedelta(minutes=31)))
        s.return_equipment(self.admin,b.pk,self.eq.code,'已还',at=self.end+timedelta(minutes=31))
        loan=s.checkout(self.admin,next_booking.pk,other.school_no,self.eq.code,at=self.end+timedelta(minutes=32))
        self.assertEqual(loan.due_at,self.end+timedelta(hours=1))

    def test_aud0007_unproven_wait_is_rejected(self):
        from .coordination import coordinate
        b=self.book()
        with self.assertRaises(s.BusinessError):coordinate(self.admin,b.pk,'WAIT','没有前序占用',self.start,at=self.start)

    def test_aud0008_fault_notifies_confirmed_applicant(self):
        b=self.book();before=self.student.notifications.count()
        s.report_incident(self.admin,self.eq.pk,'FAULT','安全检查')
        b.refresh_from_db();self.assertTrue(b.review_required);self.assertGreater(self.student.notifications.count(),before)

    def test_aud0019_borrower_can_access_own_return_incident_only(self):
        b=self.book();s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        s.return_equipment(self.admin,b.pk,self.eq.code,'损坏',damaged=True,at=self.end)
        item=Incident.objects.get(reservation=b);c=Client();c.force_login(self.student)
        self.assertEqual(c.get(f'/incidents/{item.pk}/').status_code,200)
        c.force_login(User.objects.get(username='teacher'));self.assertEqual(c.get(f'/incidents/{item.pk}/').status_code,404)

    def test_aud0021_single_day_closure_preserves_other_days(self):
        from .coordination import calendar_exception
        b=self.book();day=self.start.date()
        calendar_exception(self.sys,self.eq.lab_id,day,True,None,None,'临时闭馆')
        self.assertFalse(s.availability(self.eq,self.start,self.end)[0]);b.refresh_from_db();self.assertTrue(b.review_required)
        self.assertTrue(s.availability(self.eq,self.start+timedelta(days=1),self.end+timedelta(days=1))[0])
        with self.assertRaises(s.BusinessError):s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)

    def test_aud0022_fixed_equipment_checks_both_booking_directions(self):
        EquipmentDetail.objects.filter(resource=self.eq).update(portable=False,access_room=self.room)
        b=self.book()
        with self.assertRaises(s.BusinessError):self.book(self.teacher,self.room)
        s.cancel(self.student,b.pk,'释放')
        room=self.book(self.teacher,self.room);s.approve(self.admin,room.pk,True,'课堂')
        with self.assertRaises(s.BusinessError):self.book()

    def test_aud0024_responses_append_and_reviewer_is_independent(self):
        from .coordination import respond_incident
        b=self.book();i=s.report_incident(self.admin,self.eq.pk,'DAMAGE','检查',booking_id=b.pk)
        respond_incident(self.student,i.pk,'DISPUTE','提出异议')
        with self.assertRaises(s.BusinessError):respond_incident(self.admin,i.pk,'REVIEW','本人结案')
        self.assertEqual(i.responses.count(),1);i.refresh_from_db();self.assertEqual(i.description,'检查')

    def test_aud0025_reassignment_after_technician_deactivation(self):
        from .coordination import reassign_work
        tech=User.objects.get(username='tech');i=s.report_incident(self.admin,self.eq.pk,'FAULT','故障');s.verify_incident(self.admin,i.pk,'CONFIRM','确认')
        w=s.create_work_order(self.admin,i.pk,tech.pk,'维修');User.objects.filter(pk=tech.pk).update(is_active=False)
        replacement=User.objects.create(username='repair_backup',school_no='TECH-TEST',display_name='替补维修');UserRole.objects.create(user=replacement,role=Role.objects.get(code='TECHNICIAN'))
        reassign_work(self.admin,w.pk,replacement.pk,'离岗交接')
        s.update_work(replacement,w.pk,'START','接单')
        with self.assertRaises(s.BusinessError):s.update_work(tech,w.pk,'PROGRESS','越权')

    def test_aud0043_revocation_notifies_and_blocks(self):
        r=Resource.objects.get(code='EQ-ROB-01');b=self.book(resource=r);s.approve(self.admin,b.pk,True,'合格')
        before=self.student.notifications.count()
        s.grant_user(self.sys,dict(user=self.student,roles=list(self.student.roles.all()),labs=[],active=True,qualification=r.equipment.qualification,expires_at=None,revoke=True,reason='撤销'))
        b.refresh_from_db();self.assertTrue(b.review_required);self.assertGreater(self.student.notifications.count(),before)
        with self.assertRaises(s.BusinessError):s.checkout(self.admin,b.pk,self.student.school_no,r.code,at=self.start)

    def test_aud0014_changed_return_is_not_silently_accepted(self):
        b=self.book();s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        original=s.return_equipment(self.admin,b.pk,self.eq.code,'完好',at=self.end)
        self.assertEqual(s.return_equipment(self.admin,b.pk,self.eq.code,'完好',at=self.end).pk,original.pk)
        with self.assertRaises(s.BusinessError):s.return_equipment(self.admin,b.pk,'WRONG','新增损坏',damaged=True,at=self.end)

    def test_aud0020_invalid_header_only_pdf_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        i=s.report_incident(self.student,self.eq.pk,'FAULT','故障')
        with self.assertRaises(s.BusinessError):s.upload_evidence(self.student,i.pk,SimpleUploadedFile('bad.pdf',b'%PDF-1.4\n%%EOF'))
        self.assertFalse(Evidence.objects.exists())

    def test_aud0003_all_list_entries_have_reachable_pagination(self):
        for n in range(101):
            ev=s.audit(self.admin,self.eq,'LIST_TEST',reason=str(n))
            Notification.objects.create(audit=ev,recipient=self.admin,kind='LIST_TEST',content=f'通知{n}')
        c=Client();c.force_login(self.admin)
        for path in ['/resources/','/bookings/','/incidents/','/work/','/notifications/','/audit/','/deliveries/']:
            with self.subTest(path=path):
                response=c.get(path);self.assertEqual(response.status_code,200);self.assertIsNotNone(response.context['page'])
        response=c.get('/notifications/?page=4');self.assertEqual(response.context['page'].number,4)
