import uuid
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.test import TestCase,Client,override_settings
from django.core.exceptions import PermissionDenied
from django.db import transaction,IntegrityError,DatabaseError,connection
from core.models import *
from core import services as s
from core.jobs import schedule,deliver
from core.queries import utilisation

class Fixture:
    def setup_data(self):
        self.now=datetime(2026,10,10,9,0,tzinfo=ZoneInfo('Asia/Shanghai'))
        BusinessConfig.objects.get_or_create(pk=1)
        self.lab=Laboratory.objects.create(code='A',name='电子实验室',location='A201')
        self.other_lab=Laboratory.objects.create(code='B',name='机器人实验室',location='B302')
        self.student=User.objects.create_user('student',password='Test-only-83iJx!',role='student')
        self.other=User.objects.create_user('other',password='Test-only-29sJx!',role='student')
        self.teacher=User.objects.create_user('teacher',role='teacher')
        self.manager=User.objects.create_user('manager',role='manager')
        self.manager.managed_labs.add(self.lab)
        self.manager2=User.objects.create_user('manager2',role='manager')
        self.manager2.managed_labs.add(self.lab)
        self.tech=User.objects.create_user('tech',role='technician')
        self.tech2=User.objects.create_user('tech2',role='technician')
        self.sys=User.objects.create_user('system',role='system',is_superuser=True)
        self.e=Equipment.objects.create(tag='EQ1',name='示波器',category='示波器',laboratory=self.lab)
        self.e2=Equipment.objects.create(tag='EQ2',name='传感器',category='传感器',laboratory=self.lab)
        self.foreign=Equipment.objects.create(tag='EQ3',name='电脑',category='电脑',laboratory=self.other_lab)
        Accessory.objects.create(equipment=self.e,name='电源')
    def reserve(self,user=None,e=None,start=None,end=None,**kw):
        return s.reserve(user or self.student,(e or self.e).pk,start or self.now+timedelta(hours=1),
            end or self.now+timedelta(hours=2),'课程实验',now=self.now,**kw)
    def approved(self,user=None,e=None):
        r=self.reserve(user,e)
        return s.review(self.manager,r.pk,True,'资格时段检查通过',now=self.now)
    def loan(self,user=None,e=None):
        r=self.approved(user,e)
        return s.issue(self.manager,r.pk,r.applicant.username,r.equipment.tag,True,'功能正常，配件齐全',now=r.start)
    def damaged(self):
        loan=self.loan()
        s.receive(self.manager,loan.pk,'damage','外壳损坏，配件齐全',now=loan.due_at)
        return Incident.objects.get(loan=loan)

class BusinessTests(Fixture,TestCase):
    @classmethod
    def setUpTestData(cls):
        f=cls();f.setup_data()
        for k,v in f.__dict__.items(): setattr(cls,k,v)
    def test_uc01_free_and_occupied(self):
        self.assertEqual(connection.vendor,'postgresql')
        self.assertTrue(s.available(self.e,self.now+timedelta(hours=1),self.now+timedelta(hours=2),now=self.now)[0])
        self.approved()
        self.assertFalse(s.available(self.e,self.now+timedelta(hours=1),self.now+timedelta(hours=2),now=self.now)[0])
    def test_uc01_service_and_boundary(self):
        r=self.approved()
        self.assertTrue(s.available(self.e,r.end,r.end+timedelta(hours=1),now=self.now)[0])
        self.e.service='disabled';self.e.save()
        self.assertFalse(s.available(self.e,r.end,r.end+timedelta(hours=1),now=self.now)[0])
    def test_uc02_submit_and_missing_equipment(self):
        self.assertEqual(self.reserve().status,'pending')
        with self.assertRaises(s.BusinessError): s.reserve(self.other,999999,self.now+timedelta(hours=1),self.now+timedelta(hours=2),'用途',now=self.now)
    def test_uc02_teacher_qualification_no_bypass(self):
        self.e.qualification='robot';self.e.dangerous=True;self.e.save()
        for user in [self.student,self.teacher]:
            with self.assertRaisesMessage(s.BusinessError,'培训资格'): self.reserve(user)
        Qualification.objects.create(user=self.teacher,code='robot',expires_at=self.now+timedelta(days=30))
        self.assertEqual(self.reserve(self.teacher).status,'pending')
    def test_qualification_must_cover_end(self):
        self.e.qualification='robot';self.e.save()
        Qualification.objects.create(user=self.student,code='robot',expires_at=self.now+timedelta(hours=1))
        with self.assertRaises(s.BusinessError): self.reserve()
    def test_uc03_approve_and_competitor(self):
        a=self.reserve();b=self.reserve(self.other)
        s.review(self.manager,a.pk,True,'允许',now=self.now)
        with self.assertRaises(s.BusinessError): s.review(self.manager,b.pk,True,'允许',now=self.now)
        self.assertEqual(Review.objects.count(),1)
        self.assertEqual(Reservation.objects.filter(status='approved').count(),1)
    def test_uc03_reject_reason_and_wrong_scope(self):
        r=self.reserve()
        with self.assertRaises(s.BusinessError): s.review(self.manager,r.pk,False,'',now=self.now)
        with self.assertRaises(PermissionDenied): s.review(self.sys,r.pk,True,'通过',now=self.now)
        foreign=self.reserve(self.other,self.foreign)
        with self.assertRaises(PermissionDenied): s.review(self.manager,foreign.pk,True,'通过',now=self.now)
        self.assertEqual(s.review(self.manager,r.pk,False,'用途不符',now=self.now).status,'rejected')
    def test_maintenance_during_review(self):
        r=self.reserve()
        s.maintain_equipment(self.manager,self.e.pk,self.e.name,self.e.category,'','',False,False,'disabled','维护停用',now=self.now)
        with self.assertRaises(s.BusinessError): s.review(self.manager,r.pk,True,'允许',now=self.now)
        r.refresh_from_db(); self.assertEqual(r.status,'cancelled')
    def test_recheck_training_on_approval(self):
        self.e.qualification='safety';self.e.save()
        q=Qualification.objects.create(user=self.student,code='safety',expires_at=self.now+timedelta(days=1))
        r=self.reserve();q.delete()
        with self.assertRaises(s.BusinessError): s.review(self.manager,r.pk,True,'通过',now=self.now)
        self.assertFalse(Review.objects.exists())
    def test_uc04_cancel_and_late_or_other(self):
        r=self.approved()
        with self.assertRaises(PermissionDenied): s.cancel(self.other,r.pk,'取消',now=self.now)
        with self.assertRaises(s.BusinessError): s.cancel(self.student,r.pk,'取消',now=r.start)
        self.assertEqual(s.cancel(self.student,r.pk,'计划调整',now=self.now).status,'cancelled')
        with self.assertRaises(s.BusinessError): s.cancel(self.student,r.pk,'重试',now=self.now)
    def test_uc05_issue_and_identity_duplicate(self):
        r=self.approved()
        with self.assertRaises(s.BusinessError): s.issue(self.manager,r.pk,'other',self.e.tag,True,'正常',now=r.start)
        with self.assertRaises(s.BusinessError): s.issue(self.manager,r.pk,self.student.username,'WRONG',True,'正常',now=r.start)
        with self.assertRaises(s.BusinessError): s.issue(self.manager,r.pk,self.student.username,self.e.tag,False,'正常',now=r.start)
        loan=s.issue(self.manager,r.pk,self.student.username,self.e.tag,True,'正常',now=r.start)
        self.assertEqual(loan.accessories,[{'name':'电源','quantity':1}])
        with self.assertRaises(s.BusinessError): s.issue(self.manager,r.pk,self.student.username,self.e.tag,True,'正常',now=r.start)
        self.assertEqual(Loan.objects.count(),1)
    def test_issue_window_and_service_block(self):
        r=self.approved()
        for when in [self.now,r.start+timedelta(minutes=30)]:
            with self.assertRaises(s.BusinessError): s.issue(self.manager,r.pk,self.student.username,self.e.tag,True,'正常',now=when)
        self.e.service='quarantine';self.e.save()
        with self.assertRaises(s.BusinessError): s.issue(self.manager,r.pk,self.student.username,self.e.tag,True,'正常',now=r.start)
    def test_uc06_normal_return_duplicate_missing_loan(self):
        loan=self.loan()
        ret=s.receive(self.manager,loan.pk,'normal','功能正常，配件齐全',now=loan.due_at)
        self.assertEqual(ret.overdue_seconds,0)
        loan.refresh_from_db();self.e.refresh_from_db()
        self.assertEqual(loan.closed_at,loan.due_at);self.assertEqual(self.e.physical,'stock')
        self.assertEqual(loan.reservation.status,'completed')
        with self.assertRaises(s.BusinessError): s.receive(self.manager,loan.pk,'normal','重试',now=loan.due_at)
        with self.assertRaises(s.BusinessError): s.receive(self.manager,999,'normal','无记录',now=loan.due_at)
    def test_overdue_user_and_device_availability(self):
        loan=self.loan()
        when=loan.due_at+timedelta(minutes=1)
        with self.assertRaises(s.BusinessError):
            s.reserve(self.student,self.e2.pk,when+timedelta(hours=1),when+timedelta(hours=2),'用途',now=when)
        self.assertFalse(s.available(self.e,when+timedelta(days=2),when+timedelta(days=2,hours=1),now=when)[0])
        ret=s.receive(self.manager,loan.pk,'normal','逾期归还',now=when)
        self.assertEqual(ret.overdue_seconds,60)
    def test_damage_missing_fault_atomic_return(self):
        for condition in ['damage','missing','fault']:
            with self.subTest(condition=condition):
                e=Equipment.objects.create(tag=condition,name='设备',category='类型',laboratory=self.lab)
                r=s.reserve(self.other,e.pk,self.now+timedelta(hours=1),self.now+timedelta(hours=2),'用途',now=self.now)
                s.review(self.manager,r.pk,True,'通过',now=self.now)
                loan=s.issue(self.manager,r.pk,self.other.username,e.tag,True,'正常',now=r.start)
                s.receive(self.manager,loan.pk,condition,'异常检查',now=r.end)
                e.refresh_from_db();loan.refresh_from_db()
                self.assertEqual(e.service,'quarantine');self.assertEqual(e.physical,'stock')
                self.assertIsNotNone(loan.closed_at)
                self.assertEqual(Incident.objects.filter(loan=loan).count(),1)
    def test_uc07_user_incident_isolation_and_unrelated_denied(self):
        loan=self.loan()
        future=s.reserve(self.other,self.e.pk,loan.due_at,loan.due_at+timedelta(hours=1),'接续实验',now=self.now)
        i=s.report_incident(self.student,self.e.pk,'运行噪声异常',now=loan.issued_at)
        self.e.refresh_from_db();future.refresh_from_db();loan.refresh_from_db()
        self.assertEqual(i.status,'pending');self.assertEqual(self.e.service,'quarantine')
        self.assertEqual(future.status,'cancelled');self.assertIsNone(loan.closed_at)
        self.assertEqual(loan.reservation.status,'borrowed')
        with self.assertRaises(PermissionDenied): s.report_incident(self.other,self.e.pk,'无关设备',now=self.now)
    def test_uc07_confirm_false_alarm_restore_after_return(self):
        loan=self.loan()
        i=s.report_incident(self.student,self.e.pk,'怀疑故障',now=loan.issued_at)
        s.confirm_incident(self.manager,i.pk,'resolve','确认误报',now=loan.issued_at)
        self.e.refresh_from_db(); self.assertEqual(self.e.service,'quarantine')
        s.receive(self.manager,loan.pk,'normal','复检正常',now=loan.due_at)
        self.e.refresh_from_db();self.assertEqual(self.e.service,'normal')
    def test_uc08_full_repair_and_technician_cannot_accept(self):
        i=self.damaged()
        w=s.create_work_order(self.manager,i.pk,self.tech.pk,'修复外壳',now=self.now+timedelta(hours=2))
        with self.assertRaises(PermissionDenied): s.progress_work(self.tech2,w.pk,False,'他人工单',now=self.now)
        s.progress_work(self.tech,w.pk,False,'检修中',now=self.now+timedelta(hours=3))
        s.progress_work(self.tech,w.pk,True,'完成修复',now=self.now+timedelta(hours=4))
        with self.assertRaises(PermissionDenied): s.accept_work(self.tech,w.pk,True,'自验收',now=self.now)
        s.accept_work(self.manager,w.pk,True,'验收合格',now=self.now+timedelta(hours=5))
        self.e.refresh_from_db();i.refresh_from_db()
        self.assertEqual(self.e.service,'normal');self.assertEqual(i.status,'resolved')
        self.assertTrue(ReturnRecord.objects.exists())
    def test_uc08_multiple_work_orders_and_incidents_block_restore(self):
        i=self.damaged()
        extra=s.report_incident(self.manager,self.e.pk,'另一未解决故障',now=self.now+timedelta(hours=2))
        a=s.create_work_order(self.manager,i.pk,self.tech.pk,'工单一',now=self.now)
        b=s.create_work_order(self.manager,i.pk,self.tech.pk,'工单二',now=self.now)
        for w in [a,b]: s.progress_work(self.tech,w.pk,True,'修复',now=self.now)
        s.accept_work(self.manager,a.pk,True,'合格',now=self.now)
        self.e.refresh_from_db();self.assertEqual(self.e.service,'repair')
        s.accept_work(self.manager,b.pk,True,'合格',now=self.now)
        self.e.refresh_from_db();self.assertNotEqual(self.e.service,'normal')
        s.confirm_incident(self.manager,extra.pk,'resolve','独立异常已解决',now=self.now)
        self.e.refresh_from_db();self.assertEqual(self.e.service,'normal')
    def test_disabled_blocks_repair_restore_and_acceptance_rejection(self):
        i=self.damaged();w=s.create_work_order(self.manager,i.pk,self.tech.pk,'维修',now=self.now)
        s.progress_work(self.tech,w.pk,True,'提交',now=self.now)
        s.accept_work(self.manager,w.pk,False,'复测不合格',now=self.now)
        w.refresh_from_db();self.assertEqual(w.status,'progress')
        self.e.refresh_from_db()
        s.maintain_equipment(self.manager,self.e.pk,self.e.name,self.e.category,'','',False,False,'disabled','停用',now=self.now)
        s.progress_work(self.tech,w.pk,True,'再次提交',now=self.now)
        s.accept_work(self.manager,w.pk,True,'修复合格',now=self.now)
        self.e.refresh_from_db();self.assertEqual(self.e.service,'disabled')
    def test_time_validation_and_adjacent_personal_slots(self):
        for start,end in [(self.now-timedelta(minutes=30),self.now),
            (self.now,self.now),(self.now+timedelta(days=15),self.now+timedelta(days=15,hours=1)),
            (self.now+timedelta(hours=1),self.now+timedelta(hours=10)),
            (self.now+timedelta(minutes=31),self.now+timedelta(hours=1)),
            ('bad','bad'),(self.now.replace(tzinfo=None),self.now.replace(tzinfo=None)+timedelta(hours=1))]:
            with self.subTest(start=start),self.assertRaises(s.BusinessError):
                s.reserve(self.student,self.e.pk,start,end,'用途',now=self.now)
        r=self.reserve()
        with self.assertRaisesMessage(s.BusinessError,'本人'): self.reserve(e=self.e2)
        adjacent=s.reserve(self.student,self.e2.pk,r.end,r.end+timedelta(hours=1),'用途',now=self.now)
        self.assertEqual(adjacent.status,'pending')
    def test_personal_quota_independent_of_device_conflict(self):
        for n in range(3):
            start=self.now+timedelta(hours=1+n*2)
            s.reserve(self.student,self.e.pk,start,start+timedelta(hours=1),'用途',now=self.now)
        with self.assertRaisesMessage(s.BusinessError,'最多'):
            s.reserve(self.student,self.e2.pk,self.now+timedelta(hours=8),self.now+timedelta(hours=9),'用途',now=self.now)
    def test_request_idempotency_and_misuse(self):
        key=str(uuid.uuid4())
        a=self.reserve(key=key);b=self.reserve(key=key)
        self.assertEqual(a.pk,b.pk);self.assertEqual(Reservation.objects.count(),1)
        with self.assertRaisesMessage(s.BusinessError,'标识'): self.reserve(e=self.e2,key=key)
        self.assertEqual(Audit.objects.filter(object_type='Reservation').count(),1)
    def test_issue_and_return_idempotency(self):
        r=self.approved();key=str(uuid.uuid4())
        args=(self.manager,r.pk,self.student.username,self.e.tag,True,'正常')
        a=s.issue(*args,key=key,now=r.start);b=s.issue(*args,key=key,now=r.start)
        self.assertEqual(a.pk,b.pk)
        key=str(uuid.uuid4());args=(self.manager,a.pk,'normal','正常')
        x=s.receive(*args,key=key,now=r.end);y=s.receive(*args,key=key,now=r.end)
        self.assertEqual(x.pk,y.pk);self.assertEqual(ReturnRecord.objects.count(),1)
    def test_expiry_scheduler_repeated(self):
        a=self.reserve();b=self.approved(self.other,self.e2)
        schedule(a.start)
        a.refresh_from_db();b.refresh_from_db()
        self.assertEqual(a.status,'expired');self.assertEqual(b.status,'approved')
        schedule(b.start+timedelta(minutes=30));n=Audit.objects.count();tasks=Notification.objects.count()
        schedule(b.start+timedelta(minutes=31))
        self.assertEqual(Audit.objects.count(),n);self.assertEqual(Notification.objects.count(),tasks)
        b.refresh_from_db();self.assertEqual(b.status,'expired')
    def test_notification_failure_does_not_rollback_business_and_retry_recovery(self):
        r=self.approved();n=Notification.objects.order_by('-id').first()
        deliver(self.now,mode='fail');r.refresh_from_db()
        self.assertEqual(r.status,'approved');n.refresh_from_db();self.assertEqual(n.attempts,1)
        deliver(self.now+timedelta(seconds=59),mode='fail');n.refresh_from_db();self.assertEqual(n.attempts,1)
        deliver(self.now+timedelta(minutes=1),mode='fail');n.refresh_from_db();self.assertEqual(n.attempts,2)
        deliver(self.now+timedelta(minutes=6),mode='success');n.refresh_from_db()
        self.assertEqual(n.status,'sent');self.assertEqual(n.attempts,3)
        before=MessageDelivery.objects.count();deliver(self.now+timedelta(days=1),mode='success')
        self.assertEqual(MessageDelivery.objects.count(),before)
    def test_notification_final_failure_and_stale_lease(self):
        self.reserve();n=Notification.objects.first()
        for minute in [0,1,6,21,22]: deliver(self.now+timedelta(minutes=minute),mode='fail')
        n.refresh_from_db();self.assertEqual(n.status,'failed');self.assertEqual(n.attempts,4)
        n.status='sending';n.lease_until=self.now-timedelta(seconds=1);n.next_attempt=self.now;n.save()
        deliver(self.now,mode='success');n.refresh_from_db();self.assertEqual(n.status,'sent')
    def test_due_overdue_notifications_once(self):
        loan=self.loan();schedule(loan.due_at-timedelta(minutes=30));schedule(loan.due_at-timedelta(minutes=20))
        self.assertEqual(Notification.objects.filter(event_key=f'loan:{loan.pk}:due').count(),1)
        schedule(loan.due_at+timedelta(minutes=1));schedule(loan.due_at+timedelta(minutes=2))
        self.assertEqual(Notification.objects.filter(event_key=f'loan:{loan.pk}:overdue').count(),1)
    def test_transaction_rollback_submit_and_review(self):
        with patch('core.services.notify',side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError): self.reserve()
        self.assertEqual(Reservation.objects.count(),0);self.assertEqual(Audit.objects.count(),0)
        self.assertEqual(CommandReceipt.objects.count(),0)
        r=self.reserve();before=Audit.objects.count()
        with patch('core.services.audit',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError): s.review(self.manager,r.pk,True,'通过',now=self.now)
        r.refresh_from_db();self.assertEqual(r.status,'pending');self.assertFalse(Review.objects.exists())
        self.assertEqual(Audit.objects.count(),before)
    def test_transaction_rollback_abnormal_return(self):
        loan=self.loan();before=Audit.objects.count()
        with patch('core.services.set_service',side_effect=RuntimeError('fault')):
            with self.assertRaises(RuntimeError): s.receive(self.manager,loan.pk,'damage','损坏',now=loan.due_at)
        loan.refresh_from_db();self.e.refresh_from_db()
        self.assertIsNone(loan.closed_at);self.assertEqual(self.e.physical,'out')
        self.assertFalse(ReturnRecord.objects.exists());self.assertFalse(Incident.objects.exists())
        self.assertEqual(Audit.objects.count(),before)
    def test_database_constraints_and_audit_immutable(self):
        r=self.reserve()
        with self.assertRaises(DatabaseError),transaction.atomic(): Audit.objects.all().update(reason='tamper')
        with self.assertRaises(DatabaseError),transaction.atomic(): Audit.objects.all().delete()
        with self.assertRaises(IntegrityError),transaction.atomic():
            Reservation.objects.create(applicant=self.other,equipment=self.e,start=r.end,end=r.start,purpose='bad')
    def test_report_downtime_excluded(self):
        loan=self.loan();s.receive(self.manager,loan.pk,'normal','正常',now=loan.due_at)
        s.set_service(self.e,'disabled',self.manager,'维护',loan.due_at)
        used,total,ratio=utilisation(self.e,self.now,self.now+timedelta(hours=4),self.now+timedelta(hours=4))
        self.assertEqual((used,total,ratio),(1.0,2.0,50.0))

class WebTests(Fixture,TestCase):
    @classmethod
    def setUpTestData(cls):
        f=cls();f.setup_data()
        for k,v in f.__dict__.items(): setattr(cls,k,v)
    def test_login_csrf_and_password_hash(self):
        self.assertNotEqual(self.student.password,'Test-only-83iJx!')
        self.assertTrue(self.student.check_password('Test-only-83iJx!'))
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/login/',{'username':'student','password':'Test-only-83iJx!'}).status_code,403)
        self.assertEqual(c.get('/equipment/').status_code,302)
        self.assertTrue(c.login(username='student',password='Test-only-83iJx!'))
    def test_backend_scope_direct_id_and_post(self):
        r=self.reserve()
        self.client.force_login(self.other)
        for url in [f'/reservations/{r.pk}/',f'/act/cancel/{r.pk}/',f'/act/review/{r.pk}/']:
            self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get('/users/').status_code,403)
        self.assertEqual(self.client.get('/audits/').status_code,403)
        self.client.force_login(self.sys)
        self.assertEqual(self.client.post(f'/act/review/{r.pk}/',{'key':uuid.uuid4(),'approve':'yes','reason':'绕过'}).status_code,404)
    def test_pages_all_roles_and_form_errors(self):
        for user,urls in [(self.student,['/','/labs/','/equipment/','/reservations/','/loans/','/incidents/','/notifications/']),
             (self.manager,['/','/work/','/report/','/audits/','/act/equipment-new/0/']),
             (self.tech,['/','/work/']),
             (self.sys,['/','/users/','/foundation/','/act/user/0/','/act/lab/0/','/act/qualification/0/','/act/config/1/'])]:
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username,url=url): self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.student)
        response=self.client.post(f'/act/reserve/{self.e.pk}/',{'key':uuid.uuid4(),'start':'bad','end':'bad','purpose':'用途'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.context['form'].errors['start'],['输入一个有效的日期/时间。'])
        self.assertEqual(response.context['form'].errors['end'],['输入一个有效的日期/时间。'])
    def test_browser_equivalent_post_flow(self):
        with patch('core.services.timezone.now',return_value=self.now),patch('core.views.timezone.now',return_value=self.now):
            self.client.force_login(self.student)
            response=self.client.post(f'/act/reserve/{self.e.pk}/',{'key':uuid.uuid4(),'start':'2026-10-10T10:00','end':'2026-10-10T11:00','purpose':'页面流程'})
            self.assertEqual(response.status_code,302)
            r=Reservation.objects.get()
            self.client.force_login(self.manager)
            self.assertEqual(self.client.post(f'/act/review/{r.pk}/',{'key':uuid.uuid4(),'approve':'yes','reason':'通过'}).status_code,302)
        with patch('core.services.timezone.now',return_value=r.start):
            self.assertEqual(self.client.post(f'/act/issue/{r.pk}/',{'key':uuid.uuid4(),'borrower_username':'student','equipment_tag':'EQ1','accessories_checked':'on','normal_checked':'on','inspection':'正常'}).status_code,302)
            loan=Loan.objects.get()
            self.assertEqual(self.client.post(f'/act/receive/{loan.pk}/',{'key':uuid.uuid4(),'condition':'normal','notes':'齐全正常'}).status_code,302)
