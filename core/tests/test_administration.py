from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.core.exceptions import PermissionDenied
from core.models import *
from core import services as s
from .test_business import Fixture

class AdministrationTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_user_role_assignment_scopes_and_password(self):
        u=s.save_user(self.sys,0,'newmanager','manager',True,[self.lab.pk],'Create-Test-X71!',now=self.now)
        self.assertTrue(u.check_password('Create-Test-X71!'))
        self.assertEqual(list(u.managed_labs.all()),[self.lab])
        with self.assertRaises(PermissionDenied):s.save_user(self.manager,u.pk,'newmanager','system',True,[],'',now=self.now)
        with self.assertRaises(s.BusinessError):s.save_user(self.sys,self.sys.pk,'system','student',True,[],'',now=self.now)
        s.save_user(self.sys,u.pk,'newmanager','teacher',True,[],'',now=self.now)
        self.assertFalse(u.managed_labs.exists())
    def test_lab_qualification_parameters(self):
        lab=s.save_lab(self.sys,0,'C','第三实验室','C301','模拟全天开放',now=self.now)
        self.assertEqual(lab.code,'C')
        q=s.save_qualification(self.sys,self.student.pk,'robot',self.now+timedelta(days=5),now=self.now)
        self.assertEqual(q.code,'robot')
        s.save_config(self.sys,10,30,4,2,15,now=self.now)
        self.assertEqual(BusinessConfig.objects.get().max_future,2)
        with self.assertRaises(PermissionDenied):s.save_config(self.manager,10,30,4,2,15,now=self.now)
        with self.assertRaises(s.BusinessError):s.save_config(self.sys,10,7,4,2,15,now=self.now)
    def test_equipment_creation_and_safety_metadata(self):
        e=s.add_equipment(self.manager,self.lab.pk,'EQ-NEW','机器人','机器人','robot',True,True,'线缆,电源',now=self.now)
        self.assertEqual(e.accessories.count(),2)
        with self.assertRaises(PermissionDenied):s.add_equipment(self.manager,self.other_lab.pk,'EQ-FOREIGN','设备','类型','',False,False,'线缆',now=self.now)
        r=self.reserve()
        s.maintain_equipment(self.manager,self.e.pk,'示波器','测量设备','更新','safety',True,False,'normal','新增安全培训要求',now=self.now)
        r.refresh_from_db();self.assertEqual(r.status,'cancelled')
    def test_inactive_actor_and_manager_mutation_denied(self):
        self.student.is_active=False;self.student.save()
        with self.assertRaises(PermissionDenied):self.reserve()
        with self.assertRaises(PermissionDenied):self.reserve(self.manager)
    def test_return_scope_and_invalid_result(self):
        loan=self.loan()
        with self.assertRaises(PermissionDenied):s.receive(self.student,loan.pk,'normal','非法归还',now=loan.due_at)
        with self.assertRaises(s.BusinessError):s.receive(self.manager,loan.pk,'invalid','检查',now=loan.due_at)
        with self.assertRaises(s.BusinessError):s.receive(self.manager,loan.pk,'normal','',now=loan.due_at)
        self.assertFalse(ReturnRecord.objects.exists())
    def test_atomic_rollback_cancel_issue_acceptance(self):
        r=self.approved()
        for operation in [lambda:s.cancel(self.student,r.pk,'取消',now=self.now),
                          lambda:s.issue(self.manager,r.pk,self.student.username,self.e.tag,True,'正常',now=r.start)]:
            with patch('core.services.audit',side_effect=RuntimeError('injected transaction failure')):
                with self.assertRaises(RuntimeError):operation()
            r.refresh_from_db();self.e.refresh_from_db()
            self.assertEqual(r.status,'approved');self.assertEqual(self.e.physical,'stock');self.assertFalse(Loan.objects.exists())
        loan=s.issue(self.manager,r.pk,self.student.username,self.e.tag,True,'正常',now=r.start)
        s.receive(self.manager,loan.pk,'damage','损坏',now=r.end)
        i=Incident.objects.get();w=s.create_work_order(self.manager,i.pk,self.tech.pk,'维修',now=r.end)
        s.progress_work(self.tech,w.pk,True,'完成',now=r.end)
        with patch('core.services.audit',side_effect=RuntimeError('injected transaction failure')):
            with self.assertRaises(RuntimeError):s.accept_work(self.manager,w.pk,True,'合格',now=r.end)
        w.refresh_from_db();i.refresh_from_db();self.e.refresh_from_db()
        self.assertEqual(w.status,'acceptance');self.assertEqual(i.status,'confirmed');self.assertEqual(self.e.service,'repair')
    def test_personal_unfinished_loan_conflict_and_approval_overdue(self):
        loan=self.loan()
        with self.assertRaisesMessage(s.BusinessError,'未结束'):
            s.reserve(self.student,self.e2.pk,loan.issued_at,loan.due_at,'另一设备',now=loan.issued_at)
        later=loan.due_at+timedelta(hours=1)
        r=s.reserve(self.student,self.e2.pk,later,later+timedelta(hours=1),'后续预约',now=self.now)
        with self.assertRaisesMessage(s.BusinessError,'逾期'):
            s.review(self.manager,r.pk,True,'审核',now=loan.due_at+timedelta(minutes=1))
    def test_open_work_order_prevents_reservation_and_false_resolution(self):
        i=self.damaged();w=s.create_work_order(self.manager,i.pk,self.tech.pk,'维修',now=self.now)
        with self.assertRaises(s.BusinessError):s.confirm_incident(self.manager,i.pk,'resolve','跳过维修',now=self.now)
        self.e.service='normal';self.e.save() # simulate inconsistent imported metadata, service must still reject.
        with self.assertRaisesMessage(s.BusinessError,'工单'):self.reserve(self.other)
    def test_seed_is_repeatable_and_protects_non_demo(self):
        from django.core.management import call_command,CommandError
        User.objects.create_user('demo_student1',role='student',demo=False)
        with self.assertRaises(CommandError):call_command('seed_demo')
        self.assertFalse(Laboratory.objects.filter(demo=True).exists())
