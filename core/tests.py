import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction, IntegrityError, DatabaseError, connections
from django.utils import timezone
from .models import *
from . import services as s
from .reports import utilization

class BusinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):call_command("seed_demo",verbosity=0)
    def setUp(self):
        self.student=User.objects.get(username="student");self.other=User.objects.get(username="student2");self.teacher=User.objects.get(username="teacher");self.admin=User.objects.get(username="admin");self.tech=User.objects.get(username="tech");self.sys=User.objects.get(username="sysadmin")
        self.eq=Resource.objects.get(code="EQ-OSC-01");self.restricted=Resource.objects.get(code="EQ-ROB-01");self.room=Resource.objects.get(code="ROOM-101")
        self.now=timezone.now();self.start=timezone.localtime(self.now+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0);self.end=self.start+timedelta(hours=1)
    def book(self,who=None,res=None,**kwargs):return s.create_reservation(who or self.student,(res or self.eq).pk,kwargs.pop("start",self.start),kwargs.pop("end",self.end),"课程实验",**kwargs)
    def lend(self,b=None):
        b=b or self.book();return s.checkout(self.admin,b.pk,b.applicant.school_no,b.resource.code,at=b.starts_at+timedelta(minutes=5))
    def test_ordinary_auto_confirmation(self):
        b=self.book();self.assertEqual(b.status,"CONFIRMED");self.assertEqual(b.decision.mode,"AUTO");self.assertIsNone(b.decision.actor)
    def test_pending_does_not_occupy(self):
        b=self.book(res=self.restricted);b2=self.book(self.teacher,self.restricted);self.assertEqual((b.status,b2.status),("PENDING","PENDING"))
    def test_manual_approval_rechecks_conflict(self):
        a=self.book(res=self.restricted);b=self.book(self.teacher,self.restricted);s.approve(self.admin,a.pk,True,"培训与用途合规")
        with self.assertRaises(s.BusinessError):s.approve(self.admin,b.pk,True,"同意")
        b.refresh_from_db();self.assertEqual(b.status,"PENDING");self.assertFalse(ApprovalDecision.objects.filter(reservation=b).exists())
    def test_reject_and_supplement(self):
        b=self.book(res=self.restricted);s.review_note(self.admin,b.pk,"请说明安全措施");s.review_note(self.student,b.pk,"已完成培训")
        b.refresh_from_db();self.assertEqual(b.status,"PENDING");s.approve(self.admin,b.pk,False,"用途不符");b.refresh_from_db();self.assertEqual(b.status,"REJECTED")
    def test_student_cannot_book_room(self):
        with self.assertRaises(s.BusinessError):self.book(res=self.room)
    def test_room_and_equipment_are_independent(self):
        b=self.book(self.teacher,self.room);s.approve(self.admin,b.pk,True,"教学使用");c=self.book(self.teacher,self.eq);self.assertEqual(c.status,"CONFIRMED")
    def test_room_capacity_limit(self):
        with self.assertRaises(s.BusinessError):self.book(self.teacher,self.room,participants=33)
    def test_training_required(self):
        with self.assertRaises(s.BusinessError):self.book(self.other,self.restricted)
    def test_revoked_training_rechecked_on_pickup(self):
        b=self.book(res=self.restricted);s.approve(self.admin,b.pk,True,"通过");UserQualification.objects.filter(user=self.student).update(revoked=True)
        with self.assertRaises(s.BusinessError):self.lend(b)
    def test_wrong_time_order(self):
        with self.assertRaises(s.BusinessError):self.book(end=self.start)
    def test_past_time_rejected(self):
        with self.assertRaises(s.BusinessError):self.book(start=self.start-timedelta(days=3),end=self.end-timedelta(days=3))
    def test_slot_granularity(self):
        with self.assertRaises(s.BusinessError):self.book(start=self.start+timedelta(minutes=1))
    def test_future_window(self):
        with self.assertRaises(s.BusinessError):self.book(start=self.start+timedelta(days=15),end=self.end+timedelta(days=15))
    def test_max_duration(self):
        with self.assertRaises(s.BusinessError):self.book(end=self.start+timedelta(hours=9))
    def test_outside_opening(self):
        with self.assertRaises(s.BusinessError):self.book(start=self.start.replace(hour=7),end=self.start.replace(hour=8))
    def test_adjacent_half_open_times_allowed(self):
        self.book();self.assertEqual(self.book(self.other,start=self.end,end=self.end+timedelta(hours=1)).status,"CONFIRMED")
    def test_exact_conflict_blocked(self):
        self.book()
        with self.assertRaises(s.BusinessError):self.book(self.other)
    def test_idempotency_and_changed_payload(self):
        key=uuid.uuid4();a=self.book(request_key=key);b=self.book(request_key=key);self.assertEqual(a.pk,b.pk)
        with self.assertRaises(s.BusinessError):self.book(request_key=key,end=self.end+timedelta(hours=1))
    def test_future_quota_counts_pending(self):
        self.book(res=self.restricted);self.book(res=Resource.objects.get(code="EQ-CAM-01"));self.book()
        with self.assertRaises(s.BusinessError):self.book(res=Resource.objects.get(code="EQ-PC-01"))
    def test_room_and_equipment_quotas_are_separate(self):
        for r in Resource.objects.filter(kind="EQUIPMENT")[:3]:self.book(self.teacher,r)
        self.assertEqual(self.book(self.teacher,self.room).status,"PENDING")
    def test_simultaneous_equipment_limit(self):
        Policy.objects.filter(pk=1).update(equipment_quota=10)
        for r in Resource.objects.filter(kind="EQUIPMENT",equipment__risk="NORMAL")[:3]:self.book(res=r)
        with self.assertRaises(s.BusinessError):self.book(res=Resource.objects.get(code="EQ-PC-02"))
    def test_teacher_single_room_even_pending(self):
        self.book(self.teacher,self.room)
        with self.assertRaises(s.BusinessError):self.book(self.teacher,Resource.objects.get(code="ROOM-202"))
    def test_cancel_and_release(self):
        b=self.book();s.cancel(self.student,b.pk,"课程改期");self.assertEqual(self.book(self.other).status,"CONFIRMED")
    def test_cancel_after_start_rejected(self):
        b=self.book()
        with self.assertRaises(s.BusinessError):s.cancel(self.student,b.pk,"取消",at=self.start)
    def test_cancel_other_person_rejected(self):
        b=self.book()
        with self.assertRaises(s.BusinessError):s.cancel(self.other,b.pk,"取消")
    def test_optimistic_version(self):
        b=self.book(res=self.restricted)
        with self.assertRaises(s.BusinessError):s.approve(self.admin,b.pk,True,"通过",expected=99)
    def test_scope_not_system_admin_privilege(self):
        b=self.book(res=self.restricted)
        with self.assertRaises(s.BusinessError):s.approve(self.sys,b.pk,True,"通过")
        LabGrant.objects.filter(user=self.admin).delete()
        with self.assertRaises(s.BusinessError):s.approve(self.admin,b.pk,True,"通过")
    def test_checkout_identity_mismatch(self):
        b=self.book()
        with self.assertRaises(s.BusinessError):s.checkout(self.admin,b.pk,"WRONG",self.eq.code,at=self.start)
        self.assertFalse(Loan.objects.exists())
    def test_checkout_tag_mismatch(self):
        b=self.book()
        with self.assertRaises(s.BusinessError):s.checkout(self.admin,b.pk,self.student.school_no,"WRONG",at=self.start)
    def test_checkout_accessory_failure_isolates_without_loan(self):
        b=self.book();r=s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,accessories_ok=False,at=self.start)
        self.assertIsNone(r);self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"ISOLATED");self.assertFalse(Loan.objects.exists());self.assertEqual(Incident.objects.count(),1)
    def test_normal_return_snapshot_and_idempotency(self):
        loan=self.lend();r=s.return_equipment(self.admin,loan.reservation_id,self.eq.code,"完好",returned_at=self.end,at=self.end)
        r2=s.return_equipment(self.admin,loan.reservation_id,self.eq.code,"重试",at=self.end)
        self.assertEqual(r.pk,r2.pk);self.assertEqual(r.overdue_seconds,0);self.assertEqual(Loan.objects.count(),1)
        self.assertEqual(loan.items.filter(return_qty=1).count(),2)
    def test_return_without_loan_rejected(self):
        b=self.book()
        with self.assertRaises(s.BusinessError):s.return_equipment(self.admin,b.pk,self.eq.code,"归还",at=self.end)
    def test_overdue_blocks_then_clears(self):
        loan=self.lend();at=self.end+timedelta(minutes=5)
        with self.assertRaises(s.BusinessError):self.book(res=Resource.objects.get(code="EQ-PC-01"),start=self.end+timedelta(hours=1),end=self.end+timedelta(hours=2),at=at)
        r=s.return_equipment(self.admin,loan.reservation_id,self.eq.code,"完好",at=at);self.assertEqual(r.overdue_seconds,300);self.assertFalse(s.overdue_for(self.student,at))
    def test_damage_and_missing_preserve_return_fact(self):
        loan=self.lend();item=loan.items.first();r=s.return_equipment(self.admin,loan.reservation_id,self.eq.code,"屏幕破损，电源线缺失",damaged=True,return_quantities={item.pk:0},at=self.end)
        self.assertTrue(r.damaged and r.missing);self.assertEqual(Incident.objects.count(),2);self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"ISOLATED");self.assertTrue(Reservation.objects.filter(pk=loan.reservation_id,status="COMPLETED").exists())
    def test_return_state_fault(self):
        loan=self.lend();s.return_equipment(self.admin,loan.reservation_id,self.eq.code,"运行异常",fault=True,at=self.end)
        self.assertEqual(Incident.objects.get().kind,"FAULT")
    def test_room_start_and_end(self):
        b=self.book(self.teacher,self.room,participants=20);s.approve(self.admin,b.pk,True,"课程需要");s.start_room(self.admin,b.pk,self.teacher.school_no,20,"核验通过",at=self.start)
        s.end_room(self.admin,b.pk,"工位完好",at=self.end);b.refresh_from_db();self.assertEqual(b.status,"COMPLETED");self.assertIsNotNone(b.room_use.ended_at)
    def test_room_over_capacity_on_arrival(self):
        b=self.book(self.teacher,self.room);s.approve(self.admin,b.pk,True,"同意")
        with self.assertRaises(s.BusinessError):s.start_room(self.admin,b.pk,self.teacher.school_no,33,"检查",at=self.start)
    def test_room_abnormal_end(self):
        b=self.book(self.teacher,self.room);s.approve(self.admin,b.pk,True,"同意");s.start_room(self.admin,b.pk,self.teacher.school_no,10,"检查",at=self.start);s.end_room(self.admin,b.pk,"门禁故障",abnormal=True,at=self.end)
        self.room.refresh_from_db();self.eq.refresh_from_db();self.assertEqual(self.room.service_status,"ISOLATED");self.assertEqual(self.eq.service_status,"NORMAL")
    def test_incident_requires_confirmation_before_work(self):
        i=s.report_incident(self.student,self.eq.pk,"FAULT","开机无响应")
        with self.assertRaises(s.BusinessError):s.create_work_order(self.admin,i.pk,self.tech.pk,"安排维修")
        s.verify_incident(self.admin,i.pk,"confirm","已现场核实");w=s.create_work_order(self.admin,i.pk,self.tech.pk,"维修");self.assertEqual(w.status,"OPEN")
    def test_full_maintenance_cycle(self):
        i=s.report_incident(self.student,self.eq.pk,"FAULT","故障");s.verify_incident(self.admin,i.pk,"confirm","属实");w=s.create_work_order(self.admin,i.pk,self.tech.pk,"处理")
        s.update_work(self.tech,w.pk,"START","检测");s.update_work(self.tech,w.pk,"SUBMIT","更换电源，自检通过");self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"MAINTENANCE")
        s.accept_work(self.admin,w.pk,True,"复测通过");self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"NORMAL")
    def test_failed_acceptance_returns_to_repair(self):
        i=s.report_incident(self.student,self.eq.pk,"FAULT","故障");s.verify_incident(self.admin,i.pk,"confirm","属实");w=s.create_work_order(self.admin,i.pk,self.tech.pk,"处理");s.update_work(self.tech,w.pk,"START","开始");s.update_work(self.tech,w.pk,"SUBMIT","修复");s.accept_work(self.admin,w.pk,False,"复测失败");w.refresh_from_db();self.assertEqual(w.status,"IN_PROGRESS")
    def test_other_incident_prevents_restoration(self):
        i=s.report_incident(self.student,self.eq.pk,"FAULT","故障1");j=s.report_incident(self.other,self.eq.pk,"FAULT","故障2");s.verify_incident(self.admin,i.pk,"confirm","属实");w=s.create_work_order(self.admin,i.pk,self.tech.pk,"处理");s.update_work(self.tech,w.pk,"START","开始");s.update_work(self.tech,w.pk,"SUBMIT","修复");s.accept_work(self.admin,w.pk,True,"通过");self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"ISOLATED")
    def test_maintenance_cancels_future_and_preserves_history(self):
        b=self.book();i=s.report_incident(self.other,self.eq.pk,"FAULT","故障");s.verify_incident(self.admin,i.pk,"confirm","属实");s.create_work_order(self.admin,i.pk,self.tech.pk,"维修");b.refresh_from_db();self.assertEqual(b.status,"CANCELLED");self.assertTrue(AuditEvent.objects.filter(entity_id=b.pk,action="MAINTENANCE_CANCEL").exists())
    def test_hold_prevents_booking(self):
        s.set_resource_hold(self.admin,self.eq.pk,"DISABLED","停用")
        with self.assertRaises(s.BusinessError):self.book()
    def test_expiration_and_no_show(self):
        a=self.book(res=self.restricted);self.assertTrue(s.expire_booking(a.pk,self.start));b=self.book();self.assertTrue(s.expire_booking(b.pk,self.start+timedelta(minutes=31)))
    def test_reminder_deduplication(self):
        loan=self.lend();self.assertTrue(s.reminder(loan.reservation_id,"DUE_SOON",self.end-timedelta(minutes=10)));self.assertFalse(s.reminder(loan.reservation_id,"DUE_SOON",self.end-timedelta(minutes=9)))
    def test_delivery_failure_does_not_rollback_business(self):
        Policy.objects.filter(pk=1).update(mock_delivery_failure=True);b=self.book();task=Delivery.objects.filter(channel="MOCK_EXTERNAL",notification__audit__entity_id=b.pk).first();at=timezone.now()
        for delta in [0,1,6,21]:s.process_delivery(task.pk,at+timedelta(minutes=delta))
        task.refresh_from_db();self.assertEqual(task.status,"FAILED");self.assertEqual(task.attempts.count(),4);b.refresh_from_db();self.assertEqual(b.status,"CONFIRMED");self.assertEqual(task.notification.deliveries.get(channel="IN_APP").status,"SENT")
    def test_audit_database_rejects_update_and_delete(self):
        b=self.book();ev=AuditEvent.objects.filter(entity_type="reservation",entity_id=b.pk).first()
        for action in ["update","delete"]:
            with self.assertRaises(DatabaseError),transaction.atomic():
                if action=="update":AuditEvent.objects.filter(pk=ev.pk).update(reason="篡改")
                else:AuditEvent.objects.filter(pk=ev.pk).delete()
    def test_database_exclusion_even_without_service(self):
        b=self.book()
        with self.assertRaises(IntegrityError),transaction.atomic():Reservation.objects.create(applicant=self.other,resource=self.eq,starts_at=self.start,ends_at=self.end,purpose="绕过服务",status="CONFIRMED",approval_mode="AUTO")
    def test_transaction_rolls_back_partial_save(self):
        with patch("core.services.notify",side_effect=RuntimeError("local save error")):
            with self.assertRaises(RuntimeError):self.book()
        self.assertEqual(Reservation.objects.count(),0);self.assertEqual(ApprovalDecision.objects.count(),0)
    def test_evidence_access_and_hash(self):
        i=s.report_incident(self.student,self.eq.pk,"FAULT","故障");blob=b"%PDF-1.4\n%%EOF";e=s.upload_evidence(self.student,i.pk,SimpleUploadedFile("example.pdf",blob));self.assertEqual(len(e.sha256),64)
        with self.assertRaises(s.BusinessError):s.upload_evidence(self.other,i.pk,SimpleUploadedFile("x.pdf",blob))
        with self.assertRaises(s.BusinessError):s.upload_evidence(self.student,i.pk,SimpleUploadedFile("x.png",b"not png"))
        e.file.delete(save=False)
    def test_query_form_and_all_role_pages(self):
        for u,paths in [(self.student,["/","/resources/","/bookings/","/incidents/","/notifications/"]),(self.admin,["/manage/","/reports/","/audit/","/work/","/deliveries/"]),(self.sys,["/manage/","/manage/policy/","/manage/opening/","/manage/grant/","/audit/"])]:
            self.client.force_login(u)
            for path in paths:self.assertEqual(self.client.get(path).status_code,200,path)
        self.client.force_login(self.student);self.assertEqual(self.client.get("/manage/").status_code,403)
    def test_object_privacy(self):
        b=self.book();self.client.force_login(self.other);self.assertEqual(self.client.get(f"/bookings/{b.pk}/").status_code,404)
    def test_login_and_csrf_protection(self):
        self.assertEqual(self.client.get("/resources/").status_code,302)
        c=Client(enforce_csrf_checks=True);self.assertEqual(c.post("/login/").status_code,403)
    def test_identity_ticket_cannot_be_replayed(self):
        self.client.post("/login/");r=self.client.post("/identity/",{"username":"student","password":"Demo@2026"});self.assertEqual(r.status_code,302);ticket_url=r["Location"];self.assertEqual(self.client.get(ticket_url).status_code,302);self.assertEqual(self.client.get(ticket_url).status_code,403)
    def test_unusable_business_password(self):self.assertFalse(self.student.has_usable_password())
    def test_policy_revision_and_self_protection(self):
        policy=Policy.objects.get(pk=1);d=policy.snapshot();d["window_days"]=7;s.save_policy(self.sys,d,"课程参数调整");self.assertEqual(PolicyRevision.objects.count(),2)
        with self.assertRaises(s.BusinessError):s.grant_user(self.sys,{"user":self.sys,"roles":[],"labs":[],"active":False,"reason":"撤销"})
    def test_report_zero_denominator(self):
        start=timezone.now()-timedelta(days=7);rows=utilization([self.eq],start,timezone.now());self.assertGreaterEqual(rows[0]["capacity"],0)

    def test_resource_create_and_historical_accessory_snapshot(self):
        lab=self.eq.lab;data={"code":"EQ-NEW-01","name":"演示设备","kind":"EQUIPMENT","lab":lab,"description":"示例","category":"传感器","risk":"NORMAL","qualification":None,"capacity":None,"accessories":"A,适配器,2","reason":"增加演示资源"}
        r=s.save_resource(self.admin,data);self.assertEqual(r.equipment.category,"传感器");self.assertEqual(r.accessories.get().quantity,2)
        b=self.book();loan=self.lend(b);item=loan.items.first();Accessory.objects.filter(pk=item.accessory_id).update(name="新名称",quantity=9);item.refresh_from_db();self.assertNotEqual(item.name_snapshot,"新名称");self.assertEqual(item.out_qty,1)
    def test_authorization_change_and_revocation(self):
        data={"user":self.other,"roles":[Role.objects.get(code="LAB_ADMIN")],"labs":[self.eq.lab],"active":True,"qualification":None,"reason":"课程岗位调整"}
        s.grant_user(self.sys,data);self.assertTrue(s.manager(self.other,self.eq));data["roles"]=[];data["labs"]=[];s.grant_user(self.sys,data);self.assertFalse(s.manager(self.other,self.eq))
    def test_opening_preserves_versions(self):
        day=timezone.localdate()+timedelta(days=3);data={"lab":self.eq.lab,"weekday":day.isoweekday(),"starts_at":self.start.time().replace(hour=9,tzinfo=None),"ends_at":self.start.time().replace(hour=18,tzinfo=None),"effective":day,"reason":"课程日历调整"};obj=s.change_opening(self.sys,data)
        self.assertEqual(obj.valid_from,day);self.assertTrue(OpeningPeriod.objects.filter(lab=self.eq.lab,weekday=day.isoweekday(),valid_to=day-timedelta(days=1)).exists())
    def test_opening_does_not_break_confirmed_booking(self):
        b=self.book();day=b.starts_at.astimezone(s.TZ).date();data={"lab":self.eq.lab,"weekday":day.isoweekday(),"starts_at":self.start.time().replace(hour=12,tzinfo=None),"ends_at":self.start.time().replace(hour=18,tzinfo=None),"effective":day,"reason":"缩短开放"}
        with self.assertRaises(s.BusinessError):s.change_opening(self.sys,data)
    def test_notification_owner_and_read(self):
        self.book();self.client.force_login(self.other);self.assertNotIn("预约已自动确认",self.client.get("/notifications/").content.decode());self.client.post("/notifications/");self.assertTrue(Notification.objects.filter(recipient=self.student,read_at__isnull=True).exists())
        self.client.force_login(self.student);self.client.post("/notifications/");self.assertFalse(Notification.objects.filter(recipient=self.student,read_at__isnull=True).exists())
    def test_manual_contact_is_append_only_and_scoped(self):
        self.book();task=Delivery.objects.filter(channel="MOCK_EXTERNAL").first();task.status="FAILED";task.save();ev=s.record_manual_contact(self.admin,task.pk,"15:00电话告知（模拟）");self.assertEqual(ev.lab_id,self.eq.lab_id);task.refresh_from_db();self.assertEqual(task.status,"FAILED")
        with self.assertRaises(s.BusinessError):s.record_manual_contact(self.other,task.pk,"试图越权")
    def test_numeric_utilization_distinguishes_actual_and_planned(self):
        loan=self.lend();s.return_equipment(self.admin,loan.reservation_id,self.eq.code,"完好",at=self.start+timedelta(minutes=35))
        row=utilization([self.eq],self.start,self.end,at=self.end)[0];self.assertEqual(row["capacity"],1.0);self.assertEqual(row["booked"],1.0);self.assertEqual(row["actual"],0.5);self.assertEqual(row["rate"],50.0)
    def test_end_boundary_cannot_start_usage(self):
        b=self.book(end=self.start+timedelta(minutes=30))
        with self.assertRaises(s.BusinessError):s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=b.ends_at)
        self.assertTrue(s.expire_booking(b.pk,b.ends_at))
    def test_occurrence_and_report_times_are_separate(self):
        occurred=timezone.now()-timedelta(hours=1);i=s.report_incident(self.student,self.eq.pk,"FAULT","发现故障",occurred_at=occurred);self.assertEqual(i.occurred_at,occurred);self.assertGreater(i.reported_at,occurred)

    def test_independent_operation_has_shared_audit_request(self):
        s.report_incident(self.student,self.eq.pk,"FAULT","关联编号验证")
        events=AuditEvent.objects.filter(action__in=["REPORT_INCIDENT","SERVICE_STATUS"]);self.assertEqual(events.count(),2);self.assertEqual(events.values("request_id").distinct().count(),1)
    def test_http_request_id_matches_audit(self):
        self.client.force_login(self.student)
        r=self.client.post("/incidents/new/",{"resource":self.eq.pk,"kind":"FAULT","description":"HTTP关联编号验证","occurred_at":timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S")})
        self.assertEqual(r.status_code,302);self.assertTrue(AuditEvent.objects.filter(action="REPORT_INCIDENT",request_id=r["X-Request-ID"]).exists())

    def test_report_resource_pagination_preserves_scope(self):
        extra=[Resource(code=f"REPORT-{i}",name="分页模拟",kind="EQUIPMENT",lab=self.eq.lab) for i in range(61)];Resource.objects.bulk_create(extra)
        self.client.force_login(self.admin);a=self.client.get("/reports/");b=self.client.get("/reports/?page=2");self.assertEqual(a.status_code,200);self.assertEqual(len(a.context["rows"]),50);self.assertGreater(len(b.context["rows"]),0);self.assertLessEqual(len(b.context["rows"]),50)

    def test_restart_seed_preserves_revoked_permissions(self):
        UserRole.objects.filter(user=self.other).delete();call_command("seed_demo",if_empty=True,verbosity=0);self.assertFalse(self.other.roles.exists())

class ConcurrencyTests(TransactionTestCase):
    reset_sequences=True
    def setUp(self):
        call_command("seed_demo",verbosity=0);self.start=timezone.localtime(timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0);self.end=self.start+timedelta(hours=1)
    def race(self,functions):
        barrier=Barrier(len(functions))
        def run(fn):
            connections.close_all();barrier.wait()
            try:return fn()
            except s.BusinessError:return None
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:return list(pool.map(run,functions))
    def test_100_concurrent_resource_competitions(self):
        eq=Resource.objects.get(code="EQ-OSC-01");users=list(User.objects.filter(username__in=["student","student2"]))
        for group in range(100):
            results=self.race([lambda u=u:s.create_reservation(u,eq.pk,self.start,self.end,"并发实验") for u in users]);self.assertEqual(sum(x is not None for x in results),1,group)
            b=next(x for x in results if x);s.cancel(b.applicant,b.pk,"下一轮测试")
    def test_concurrent_approval_has_single_winner(self):
        eq=Resource.objects.get(code="EQ-ROB-01");admin=User.objects.get(username="admin");a=s.create_reservation(User.objects.get(username="student"),eq.pk,self.start,self.end,"用途");b=s.create_reservation(User.objects.get(username="teacher"),eq.pk,self.start,self.end,"用途")
        results=self.race([lambda x=x:s.approve(admin,x.pk,True,"通过") for x in [a,b]]);self.assertEqual(sum(x is not None for x in results),1)
    def test_person_quota_serialized_across_resources(self):
        user=User.objects.get(username="student");resources=list(Resource.objects.filter(kind="EQUIPMENT",equipment__risk="NORMAL").order_by("pk")[:4])
        for r in resources[:2]:s.create_reservation(user,r.pk,self.start,self.end,"用途")
        results=self.race([lambda r=r:s.create_reservation(user,r.pk,self.start,self.end,"用途") for r in resources[2:]]);self.assertEqual(sum(x is not None for x in results),1)


class LocalReadinessTests(TestCase):
    def test_readiness_has_no_account_data_or_credentials(self):
        response=self.client.get("/health/")
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(response.json()),{"service","status","instance"})
        self.assertEqual(response.json()["status"],"ready")
        self.assertEqual(len(response.json()["instance"]),16)
    def test_unavailable_database_is_not_reported_ready(self):
        from django.db import OperationalError
        with patch("config.health.connection.cursor",side_effect=OperationalError("not ready")):
            response=self.client.get("/health/")
        self.assertEqual(response.status_code,503)
        self.assertNotIn("instance",response.json())
