"""Read snapshot parity and bounded work, using simulated isolated records."""
from datetime import timedelta, time
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import (User, Resource, Reservation, Policy, CalendarException,
                     OpeningPeriod, SafetyAcknowledgement, SafetyProcedure,
                     UserQualification, UserRole, AuditEvent)
from . import services as s
from .experience import SlotSnapshot, slot_options, approval_context, next_step


class ExperienceTests(TestCase):
    @classmethod
    def setUpTestData(cls): call_command("seed_demo",verbosity=0)

    def setUp(self):
        self.student=User.objects.get(username="student");self.other=User.objects.get(username="student2")
        self.teacher=User.objects.get(username="teacher");self.admin=User.objects.get(username="admin")
        self.system=User.objects.get(username="sysadmin")
        self.eq=Resource.objects.select_related("lab","equipment","room").get(code="EQ-OSC-01")
        self.robot=Resource.objects.select_related("lab","equipment","room").get(code="EQ-ROB-01")
        self.room=Resource.objects.select_related("lab","equipment","room").get(code="ROOM-101")
        self.now=timezone.now();self.start=(timezone.localtime(self.now)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        self.policy=Policy.objects.get(pk=1)

    def book(self,user=None,resource=None,start=None,**kwargs):
        start=start or self.start
        return s.create_reservation(user or self.student,(resource or self.eq).pk,start,start+timedelta(hours=1),"模拟实验",**kwargs)

    def parity(self,resource=None,user=None,start=None,end=None,at=None,exclude=None,time_at=None):
        r,u,a,at=resource or self.eq,user or self.student,start or self.start,at or self.now
        z=end or a+timedelta(hours=1)
        b=Reservation(resource=r,applicant=u,starts_at=a,ends_at=z,participants=1)
        snapshot=SlotSnapshot(r,u,a.astimezone(s.TZ).date(),at)
        def outcome(facts):
            try:s.validate(b,self.policy,at,exclude=exclude,time_at=time_at,snapshot=facts);return "ok"
            except s.BusinessError as error:return str(error)
        actual,batched=outcome(None),outcome(snapshot)
        self.assertEqual(actual,batched)
        return actual

    def test_snapshot_matches_live_time_and_exact_now_boundaries(self):
        self.assertEqual(self.parity(),"ok")
        self.assertEqual(self.parity(at=self.start),"ok")
        self.assertIn("不得早于",self.parity(at=self.start+timedelta(seconds=1)))
        self.assertIn("粒度",self.parity(start=self.start+timedelta(minutes=1)))
        self.assertIn("开放",self.parity(start=self.start.replace(hour=7)))
        self.assertIn("最长",self.parity(end=self.start+timedelta(hours=9)))

    def test_snapshot_matches_exception_opening_and_closure(self):
        exception=CalendarException.objects.create(lab=self.eq.lab,day=self.start.date(),closed=True,actor=self.system,reason="模拟关闭")
        self.assertIn("关闭",self.parity())
        exception.closed=False;exception.starts_at=time(9);exception.ends_at=time(11);exception.save()
        self.assertEqual(self.parity(),"ok")
        self.assertIn("例外",self.parity(start=self.start.replace(hour=12)))

    def test_snapshot_matches_qualification_and_revocation(self):
        self.assertEqual(self.parity(self.robot),"ok")
        self.assertIn("培训",self.parity(self.robot,self.other))
        UserQualification.objects.filter(user=self.student).update(expires_at=self.start+timedelta(minutes=30))
        self.assertIn("培训",self.parity(self.robot))
        UserRole.objects.filter(user=self.student).delete()
        self.assertIn("身份",self.parity())

    def test_snapshot_matches_resource_holds(self):
        for status in ["ISOLATED","MAINTENANCE","DISABLED","SCRAPPED"]:
            with self.subTest(status=status):
                self.eq.service_status=status
                self.assertIn("不可预约",self.parity())

    def test_snapshot_matches_conflict_half_open_and_exclusion(self):
        b=self.book(self.other)
        self.assertIn("冲突",self.parity())
        self.assertEqual(self.parity(start=b.ends_at),"ok")
        self.assertEqual(self.parity(user=self.other,exclude=b.pk),"ok")

    def test_snapshot_matches_pending_future_quota(self):
        for code in ["EQ-CAM-01","EQ-ROB-01","EQ-PC-01"]:
            self.book(resource=Resource.objects.get(code=code))
        self.assertIn("额度",self.parity())

    def test_snapshot_matches_parallel_equipment_limit(self):
        self.policy.equipment_quota=10;self.policy.simultaneous_equipment=2;self.policy.save()
        for code in ["EQ-PC-01","EQ-SEN-01"]:self.book(resource=Resource.objects.get(code=code))
        self.assertIn("最多使用",self.parity())

    def test_snapshot_matches_teacher_only_and_pending_room_overlap(self):
        self.assertIn("仅教师",self.parity(self.room))
        self.book(self.teacher,Resource.objects.get(code="ROOM-202"))
        self.assertIn("只能负责",self.parity(self.room,self.teacher))

    def test_snapshot_keeps_pending_room_conflict_after_planned_start(self):
        self.book(self.teacher,Resource.objects.get(code="ROOM-202"))
        at=self.start+timedelta(minutes=30)
        self.assertIn("只能负责",self.parity(self.room,self.teacher,start=at,at=at))

    def test_snapshot_matches_fixed_room_conflicts_both_directions(self):
        detail=self.eq.equipment;detail.portable=False;detail.access_room=self.room;detail.save()
        b=self.book(self.teacher,self.room);s.approve(self.admin,b.pk,True,"模拟批准")
        self.assertIn("场地占用冲突",self.parity())
        # The same responsible teacher may use a fixed device in their own room booking.
        self.assertEqual(self.parity(self.eq,self.teacher),"ok")
        s.cancel(self.teacher,b.pk,"改为设备验证")
        self.book(self.student,self.eq)
        self.assertIn("固定设备使用冲突",self.parity(self.room,self.teacher))

    def test_snapshot_matches_overdue_actual_use_and_review_time_at(self):
        b=self.book();s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        at=b.ends_at+timedelta(minutes=1)
        self.assertIn("逾期",self.parity(start=self.start.replace(hour=14),at=at))
        self.assertIn("超期",self.parity(user=self.other,start=self.start.replace(hour=14),at=at))
        # time_at remains the original threshold used during a permitted review.
        other=Resource.objects.get(code="EQ-PC-01")
        self.assertEqual(self.parity(other,self.other,at=self.start+timedelta(minutes=2),time_at=self.start),"ok")

    def test_full_day_candidates_have_bounded_queries_and_no_write(self):
        before=AuditEvent.objects.count()
        with CaptureQueriesContext(connection) as queries:
            result=slot_options(self.eq,self.student,self.start.date(),60,at=self.now)
        self.assertLessEqual(len(queries),16)
        self.assertEqual(len(result["items"]),23)
        print(f"Slot preview measurement: {len(result['items'])} candidates, {len(queries)} database queries")
        self.assertTrue(all(x["available"] for x in result["items"]))
        self.assertEqual(AuditEvent.objects.count(),before)
        self.assertEqual(Reservation.objects.count(),0)

    def test_full_day_candidate_safety_ack_check_is_batched_and_enforced(self):
        today=timezone.localdate()
        procedure=SafetyProcedure.objects.bulk_create([SafetyProcedure(
            lab_scope=self.eq.lab,version=1,title="测试数据：资源安全信息",
            effective_from=today,review_due_date=today+timedelta(days=30),
            requires_acknowledgement=True,status="PUBLISHED",created_by=self.admin,
            change_reason="仅用于验证候选时段读取，不是正式安全规程",
        )])[0]
        with CaptureQueriesContext(connection) as queries:
            result=slot_options(self.eq,self.student,self.start.date(),60,at=self.now)
        self.assertLessEqual(len(queries),16)
        self.assertTrue(result["items"])
        self.assertTrue(all(not item["available"] and "阅读并确认" in item["reason"] for item in result["items"]))

        SafetyAcknowledgement.objects.create(procedure=procedure,user=self.student,acknowledged_at=self.now)
        with CaptureQueriesContext(connection) as queries:
            confirmed=slot_options(self.eq,self.student,self.start.date(),60,at=self.now)
        self.assertLessEqual(len(queries),16)
        self.assertTrue(all(item["available"] for item in confirmed["items"]))

    def test_slot_inputs_and_non_aligned_opening_start(self):
        for day,duration in [("bad",60),("2026-02-30",60),(self.start.date(),"1e3"),(self.start.date(),31),(self.start.date(),9999),((self.start+timedelta(days=30)).date(),60)]:
            with self.subTest(day=day,duration=duration),self.assertRaises(ValueError):
                slot_options(self.eq,self.student,day,duration,at=self.now)
        OpeningPeriod.objects.filter(lab=self.eq.lab).update(starts_at=time(8,15))
        result=slot_options(self.eq,self.student,self.start.date(),60,at=self.now)
        self.assertEqual(result["items"][0]["label"],"08:30—09:30")
        self.assertTrue(result["items"][0]["available"])

    def test_slot_privacy_and_pending_does_not_block_other_user(self):
        r=Resource.objects.get(code="EQ-CAM-01");b=self.book(self.other,r)
        result=slot_options(r,self.student,self.start.date(),60,at=self.now)
        target=next(x for x in result["items"] if x["label"]=="10:00—11:00")
        self.assertTrue(target["available"])
        data=str(result)
        for secret in [b.code,b.purpose,self.other.display_name]:self.assertNotIn(secret,data)

    def test_approval_snapshot_is_scoped_and_never_approves(self):
        b=self.book(resource=Resource.objects.get(code="EQ-CAM-01"))
        result=approval_context(b,self.admin,at=self.now)
        self.assertTrue(result["can_approve"])
        self.assertEqual({x["key"] for x in result["checks"]},{"state","time","quota","conflict","eligibility"})
        b.refresh_from_db();self.assertEqual(b.status,"PENDING")
        with self.assertRaises(s.BusinessError):approval_context(b,self.system)
        with self.assertRaises(s.BusinessError):approval_context(b,self.student)

    def test_next_step_uses_real_locations_and_actual_status(self):
        self.eq.equipment.handover_location="设备服务台（模拟交接地点）"
        self.eq.equipment.save(update_fields=["handover_location"])
        b=self.book();step=next_step(b,at=self.now)
        self.assertEqual(step["location"],"设备服务台（模拟交接地点）")
        self.eq.equipment.handover_location=""
        self.eq.equipment.save(update_fields=["handover_location"])
        b=Reservation.objects.select_related("resource__lab","resource__equipment").get(pk=b.pk)
        step=next_step(b,at=self.now)
        self.assertEqual(step["location"],"交接地点待登记")
        self.assertNotEqual(step["location"],self.eq.lab.location)
        self.assertIn("领取",step["title"])
        b.review_required=True
        self.assertIn("复核",next_step(b)["title"])
        b.status="IN_USE";b.review_required=False
        self.assertIn("超期",next_step(b,at=b.ends_at+timedelta(seconds=1))["title"])


class RoleDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo",verbosity=0)

    def dashboard_as(self,username):
        client=Client()
        client.force_login(User.objects.get(username=username))
        response=client.get(reverse("dashboard"))
        self.assertEqual(response.status_code,200)
        return response

    def test_student_dashboard_prioritizes_equipment_and_personal_bookings(self):
        response=self.dashboard_as("student")
        self.assertContains(response,"STUDENT / 个人实验安排")
        self.assertContains(response,"查找教学设备")
        self.assertContains(response,"查看我的预约与借还")
        self.assertNotContains(response,"TEACHER / 课程安排")
        self.assertNotContains(response,"处理待审核申请")

    def test_teacher_dashboard_prioritizes_room_and_course_schedule(self):
        response=self.dashboard_as("teacher")
        self.assertContains(response,"TEACHER / 课程安排")
        self.assertContains(response,"查找可预约场地")
        self.assertContains(response,"我的场地预约")
        self.assertContains(response,"等待审核，不占用资源时段")
        self.assertNotContains(response,"SYSTEM ADMIN / 身份与配置")

    def test_lab_admin_dashboard_routes_to_authorized_work_queues(self):
        response=self.dashboard_as("admin")
        self.assertContains(response,"LAB ADMIN / 今日管理")
        self.assertContains(response,"处理待审核申请")
        self.assertContains(response,"维护本实验室资源")
        self.assertContains(response,"维修待验收")
        self.assertNotContains(response,"SYSTEM ADMIN / 身份与配置")

    def test_technician_dashboard_prioritizes_assigned_work_only(self):
        response=self.dashboard_as("tech")
        self.assertContains(response,"TECHNICIAN / 指派任务")
        self.assertContains(response,"只处理分配给自己的维修工单")
        self.assertContains(response,"打开我的维修任务")
        self.assertNotContains(response,"处理待审核申请")
        self.assertNotContains(response,"管理用户与授权")

    def test_director_dashboard_shows_aggregate_not_actionable_approval(self):
        response=self.dashboard_as("director")
        self.assertContains(response,"CENTER OVERVIEW / 管理摘要")
        self.assertContains(response,"查看资源利用报表")
        self.assertContains(response,"审批由获授权实验室管理员办理")
        self.assertNotContains(response,"处理待审核申请")
        self.assertNotContains(response,"办理设备借出")

    def test_system_admin_dashboard_separates_account_configuration_from_business_approval(self):
        response=self.dashboard_as("sysadmin")
        self.assertContains(response,"SYSTEM ADMIN / 身份与配置")
        self.assertContains(response,"管理用户与授权")
        self.assertContains(response,"查看角色与资格目录")
        self.assertNotContains(response,"处理待审核申请")
        self.assertNotContains(response,"维修待验收")
