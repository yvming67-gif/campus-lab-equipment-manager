import json
import uuid
import warnings
from datetime import datetime, time, timedelta

from django.core.management import call_command
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.core.paginator import UnorderedObjectListWarning
from django.test import Client, TestCase
from django.utils import timezone

from . import safety, services
from .models import AuditEvent, Lab, LabGrant, Resource, Role, SafetyAcknowledgement, SafetyProcedure, User, UserRole


class SafetyProcedureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.admin = User.objects.get(username="admin")
        cls.teacher = User.objects.get(username="teacher")
        cls.room = Resource.objects.get(code="ROOM-101")
        cls.reviewer = User.objects.create(username="safety_reviewer", school_no="SAFETY-REVIEWER", display_name="授权复核员")
        UserRole.objects.create(user=cls.reviewer, role=Role.objects.get(code="LAB_ADMIN"))
        LabGrant.objects.create(user=cls.reviewer, lab=cls.room.lab)
        cls.outside = User.objects.create(username="outside_admin", school_no="SAFETY-OUTSIDE", display_name="无权管理员")
        UserRole.objects.create(user=cls.outside, role=Role.objects.get(code="LAB_ADMIN"))

    def procedure_values(self, **updates):
        today = timezone.localdate()
        values = {
            "title": "课程安全信息（测试数据）",
            "risk_categories": "测试占位；不构成真实风险评估",
            "applicable_users": "仅测试账号",
            "prerequisites": "测试环境",
            "required_ppe": "待责任岗位核验",
            "pre_use_checks": "测试字段",
            "operating_cautions": "测试字段",
            "prohibited_actions": "测试字段",
            "incident_route": "测试字段，不包含真实联系人",
            "source_title": "测试用批准材料占位",
            "source_reference": "TEST-DOC-001",
            "source_version": "test-v1",
            "source_effective_date": today - timedelta(days=30),
            "effective_from": today,
            "review_due_date": today + timedelta(days=30),
            "requires_acknowledgement": True,
            "change_reason": "仅用于自动化测试，不是已批准安全规程",
        }
        values.update(updates)
        return values

    def create_published(self, resource=None, lab=None, **updates):
        resource = resource or (None if lab else self.room)
        draft, _ = safety.save_draft(self.admin, resource.pk if resource else None,
                                     self.procedure_values(**updates), lab_id=lab.pk if lab else None)
        safety.submit_for_review(self.admin, draft.pk)
        return safety.publish(self.reviewer, draft.pk, "测试复核：来源、适用对象及版本信息已核对；测试内容不构成真实安全规程。")

    def test_pending_procedure_is_not_shown_and_missing_content_does_not_invent_a_block(self):
        draft, _ = safety.save_draft(self.admin, self.room.pk, self.procedure_values())
        safety.submit_for_review(self.admin, draft.pk)
        self.assertIsNone(safety.current_procedure(self.room))
        start = timezone.localtime(timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking = services.create_reservation(self.teacher, self.room.pk, start, start + timedelta(hours=1), "测试场地申请", request_key=uuid.uuid4())
        self.assertEqual(booking.status, "PENDING")

    def test_room_requires_confirmation_for_current_published_version(self):
        procedure = self.create_published()
        self.assertEqual(safety.current_procedure(self.room).pk, procedure.pk)
        start = timezone.localtime(timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        with self.assertRaisesRegex(services.BusinessError, "阅读并确认"):
            services.create_reservation(self.teacher, self.room.pk, start, start + timedelta(hours=1), "测试场地申请", request_key=uuid.uuid4())
        receipt = safety.acknowledge(self.teacher, procedure.pk)
        self.assertEqual(receipt.procedure_id, procedure.pk)
        booking = services.create_reservation(self.teacher, self.room.pk, start, start + timedelta(hours=1), "测试场地申请", request_key=uuid.uuid4())
        self.assertEqual(booking.status, "PENDING")

    def test_new_required_safety_version_is_rechecked_at_room_entry(self):
        first = self.create_published()
        safety.acknowledge(self.teacher, first.pk)
        day = timezone.localdate() + timedelta(days=2)
        start = timezone.make_aware(datetime.combine(day, time(10)), services.TZ)
        booking = services.create_reservation(self.teacher, self.room.pk, start, start + timedelta(hours=1),
                                              "验证安全版本在入场时重新确认", request_key=uuid.uuid4())
        services.approve(self.admin, booking.pk, True, "测试预约通过")

        second = self.create_published(source_version="test-v2", change_reason="测试新版本生效后的重新确认")
        notice = self.teacher.notifications.filter(kind="SAFETY_VERSION").order_by("-created_at").first()
        self.assertIsNotNone(notice)
        self.assertIn(booking.code, notice.content)
        self.assertIn("未自动取消", notice.content)
        with self.assertRaisesRegex(services.BusinessError, "新版本.*阅读并确认"):
            services.start_room(self.admin, booking.pk, self.teacher.school_no, 8,
                                "测试入场核对", arrived_at=start, at=start)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertFalse(hasattr(booking, "room_use"))

        safety.acknowledge(self.teacher, second.pk)
        use = services.start_room(self.admin, booking.pk, self.teacher.school_no, 8,
                                  "确认新版本后入场", arrived_at=start, at=start)
        self.assertEqual(use.reservation_id, booking.pk)
        self.assertEqual(use.started_at, start)

    def test_submitter_cannot_publish_own_procedure_and_pending_content_is_locked(self):
        draft, _ = safety.save_draft(self.admin, self.room.pk, self.procedure_values())
        safety.submit_for_review(self.admin, draft.pk)
        draft.title = "尝试覆盖待复核内容"
        with self.assertRaises(ValidationError):
            draft.save()
        with self.assertRaisesRegex(services.BusinessError, "必须分离"):
            safety.publish(self.admin, draft.pk, "不能自审")

    def test_reviewer_can_return_and_submitter_can_create_immutable_revision(self):
        draft, _ = safety.save_draft(self.admin, self.room.pk, self.procedure_values())
        safety.submit_for_review(self.admin, draft.pk)
        with self.assertRaisesRegex(services.BusinessError, "必须分离"):
            safety.return_for_revision(self.admin, draft.pk, "本人不能复核")
        with self.assertRaisesRegex(services.BusinessError, "必须写明"):
            safety.return_for_revision(self.reviewer, draft.pk, "  ")

        note = "请补充经授权岗位批准的来源文件编号及其适用房间范围。"
        returned, event = safety.return_for_revision(self.reviewer, draft.pk, note)
        self.assertEqual(returned.status, "RETURNED")
        self.assertEqual(returned.review_note, note)
        self.assertEqual(returned.reviewed_by_id, self.reviewer.pk)
        self.assertIsNone(returned.published_at)
        self.assertEqual(event.action, "SAFETY_RETURN_REVISION")
        self.assertTrue(AuditEvent.objects.filter(pk=event.pk, new_state__contains={"status": "RETURNED"}).exists())
        with self.assertRaisesRegex(services.BusinessError, "原提交人"):
            safety.start_revision(self.reviewer, returned.pk)

        revision = safety.start_revision(self.admin, returned.pk)
        self.assertEqual(revision.version, returned.version + 1)
        self.assertEqual(revision.status, "DRAFT")
        self.assertEqual(revision.title, returned.title)
        self.assertEqual(revision.review_note, "")
        self.assertEqual(revision.change_reason, f"根据安全信息v{returned.version}退回意见修订；请补充具体修改内容。")
        self.assertEqual(SafetyProcedure.objects.get(pk=returned.pk).review_note, note)

        revision.title = "修订后的课程安全信息测试稿"
        revision.save()
        with self.assertRaises(ValidationError):
            returned.title = "尝试覆盖被退回版本"
            returned.save()

    def test_review_decisions_notify_the_procedure_author(self):
        published, _ = safety.save_draft(self.admin, self.room.pk, self.procedure_values())
        safety.submit_for_review(self.admin, published.pk)
        safety.publish(self.reviewer, published.pk, "已核对课程测试材料来源及适用范围。")
        publish_notice = self.admin.notifications.filter(
            kind="SAFETY_REVIEW_RESULT", audit__action="SAFETY_PUBLISH"
        ).get()
        self.assertIn("已发布", publish_notice.content)
        self.assertIn("已核对课程测试材料", publish_notice.content)

        returned, _ = safety.save_draft(
            self.admin, self.room.pk,
            self.procedure_values(change_reason="第二份仅用于退回通知测试"),
        )
        safety.submit_for_review(self.admin, returned.pk)
        note = "请补充来源材料的适用房间范围。"
        safety.return_for_revision(self.reviewer, returned.pk, note)
        return_notice = self.admin.notifications.filter(
            kind="SAFETY_REVIEW_RESULT", audit__action="SAFETY_RETURN_REVISION"
        ).get()
        self.assertIn("已退回修订", return_notice.content)
        self.assertIn(note, return_notice.content)

    def test_equipment_specific_safety_is_published_shown_and_requires_ack_when_configured(self):
        equipment = Resource.objects.get(code="EQ-OSC-01")
        procedure = self.create_published(resource=equipment)
        self.assertEqual(safety.current_procedures(equipment), [procedure])
        self.assertTrue(safety.needs_ack(self.teacher, equipment))
        self.assertFalse(safety.needs_ack(self.teacher, Resource.objects.get(code="EQ-SEN-01")))

        start = timezone.localtime(timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        with self.assertRaisesRegex(services.BusinessError, "阅读并确认"):
            services.create_reservation(self.teacher, equipment.pk, start, start + timedelta(hours=1),
                                         "设备专属安全信息门控测试", request_key=uuid.uuid4())
        self.client.force_login(self.teacher)
        page = self.client.get(f"/resources/{equipment.pk}/")
        self.assertContains(page, "资源专属")
        self.assertContains(page, procedure.title)

        api_client = Client()
        login = api_client.post("/api/mini/v1/auth/login/", json.dumps({"username": "teacher", "password": "Demo@2026"}), content_type="application/json")
        token = login.json()["data"]["token"]
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
        detail = api_client.get(f"/api/mini/v1/resources/{equipment.pk}/", **headers).json()["data"]
        self.assertEqual(detail["safety_procedures"][0]["scope"], "RESOURCE")
        acknowledged = api_client.post(f"/api/mini/v1/resources/{equipment.pk}/safety/{procedure.pk}/ack/", "{}", content_type="application/json", **headers)
        self.assertEqual(acknowledged.status_code, 200, acknowledged.content)
        self.assertFalse(safety.needs_ack(self.teacher, equipment))

    def test_room_safety_does_not_apply_to_associated_fixed_equipment(self):
        equipment = Resource.objects.select_related("equipment__access_room").get(code="EQ-ROB-FIX-01")
        room = equipment.equipment.access_room
        LabGrant.objects.get_or_create(user=self.reviewer, lab=room.lab)
        shared = self.create_published(lab=room.lab, title="测试管理单元通用版本")
        room_only = self.create_published(resource=room, title="测试场地专属版本")
        equipment_only = self.create_published(resource=equipment, title="测试设备专属版本")

        self.assertEqual(set(safety.current_procedures(room)), {shared, room_only})
        self.assertEqual(set(safety.current_procedures(equipment)), {shared, equipment_only})
        self.client.force_login(self.teacher)
        room_page = self.client.get(f"/resources/{room.pk}/")
        equipment_page = self.client.get(f"/resources/{equipment.pk}/")
        self.assertContains(room_page, room_only.title)
        self.assertNotContains(room_page, equipment_only.title)
        self.assertContains(equipment_page, equipment_only.title)
        self.assertNotContains(equipment_page, room_only.title)

    def test_equipment_checkout_requires_acknowledgement_of_new_version(self):
        equipment = Resource.objects.get(code="EQ-OSC-01")
        day = timezone.localdate() + timedelta(days=2)
        start = timezone.make_aware(datetime.combine(day, time(10)), services.TZ)
        booking = services.create_reservation(
            self.teacher, equipment.pk, start, start + timedelta(hours=1),
            "设备交接确认测试", request_key=uuid.uuid4(),
        )
        self.assertEqual(booking.status, "CONFIRMED")
        version = self.create_published(resource=equipment)
        with self.assertRaisesRegex(services.BusinessError, "阅读并确认"):
            services.checkout(self.admin, booking.pk, self.teacher.school_no, equipment.code, at=start)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertFalse(hasattr(booking, "loan"))

        safety.acknowledge(self.teacher, version.pk)
        loan = services.checkout(self.admin, booking.pk, self.teacher.school_no, equipment.code, at=start)
        self.assertEqual(loan.reservation_id, booking.pk)

    def test_management_unit_information_applies_only_to_its_resources_and_fans_out_notifications(self):
        lab = self.room.lab
        equipment = Resource.objects.get(code="EQ-OSC-01")
        other_lab_equipment = Resource.objects.get(code="EQ-CAM-01")
        procedure = self.create_published(lab=lab)
        self.assertEqual(safety.current_procedures(self.room), [procedure])
        self.assertIn(procedure, safety.current_procedures(equipment))
        self.assertNotIn(procedure, safety.current_procedures(other_lab_equipment))
        self.assertTrue(safety.needs_ack(self.teacher, self.room))
        self.assertTrue(safety.needs_ack(self.teacher, equipment))
        self.assertFalse(safety.needs_ack(self.teacher, other_lab_equipment))
        self.assertEqual(procedure.scope_label, "管理单元通用")

        safety.acknowledge(self.teacher, procedure.pk, resource=equipment)
        self.assertFalse(safety.needs_ack(self.teacher, equipment))
        self.assertFalse(safety.needs_ack(self.teacher, self.room))
        with self.assertRaisesRegex(services.BusinessError, "不能作为当前版本确认"):
            safety.acknowledge(self.teacher, procedure.pk, resource=other_lab_equipment)

    def test_lab_wide_publication_notifies_future_confirmed_bookings_in_scope_only(self):
        day = timezone.localdate() + timedelta(days=3)
        room_start = timezone.make_aware(datetime.combine(day, time(10)), services.TZ)
        equipment_start = timezone.make_aware(datetime.combine(day, time(12)), services.TZ)
        other_start = timezone.make_aware(datetime.combine(day, time(14)), services.TZ)
        in_lab_equipment = Resource.objects.get(code="EQ-OSC-01")
        outside_lab_equipment = Resource.objects.get(code="EQ-SEN-02")

        room_booking = services.create_reservation(self.teacher, self.room.pk, room_start, room_start + timedelta(hours=1),
                                                   "创建管理单元通知测试预约", request_key=uuid.uuid4())
        self.assertEqual(room_booking.status, "PENDING")
        services.approve(self.admin, room_booking.pk, True, "仅用于安全信息通知测试")
        equipment_booking = services.create_reservation(self.teacher, in_lab_equipment.pk, equipment_start,
                                                        equipment_start + timedelta(hours=1), "同范围设备通知测试预约",
                                                        request_key=uuid.uuid4())
        outside_booking = services.create_reservation(self.teacher, outside_lab_equipment.pk, other_start,
                                                      other_start + timedelta(hours=1), "其他管理单元不应收到通知",
                                                      request_key=uuid.uuid4())
        self.assertEqual(equipment_booking.status, "CONFIRMED")
        self.assertEqual(outside_booking.status, "CONFIRMED")

        self.create_published(lab=self.room.lab)
        notices = list(self.teacher.notifications.filter(kind="SAFETY_VERSION", audit__action="SAFETY_PUBLISH"))
        self.assertEqual(len(notices), 1)
        notified_text = "\n".join(item.content for item in notices)
        self.assertIn(room_booking.code, notified_text)
        self.assertIn(equipment_booking.code, notified_text)
        self.assertNotIn(outside_booking.code, notified_text)

    def test_lab_scope_authorization_and_database_enforce_exactly_one_target(self):
        with self.assertRaisesRegex(services.BusinessError, "管理权限"):
            safety.save_draft(self.outside, None, self.procedure_values(), lab_id=self.room.lab_id)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SafetyProcedure.objects.create(version=1, created_by=self.admin, **self.procedure_values())
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SafetyProcedure.objects.create(resource=self.room, lab_scope=self.room.lab, version=999,
                                               created_by=self.admin, **self.procedure_values())

    def test_web_management_unit_page_and_equipment_safety_ack_use_scope_permissions(self):
        lab = self.room.lab
        outsider = Client()
        outsider.force_login(self.outside)
        self.assertEqual(outsider.get(f"/labs/{lab.pk}/safety/").status_code, 403)
        admin = Client()
        admin.force_login(self.admin)
        self.assertContains(admin.get(f"/labs/{lab.pk}/safety/"), "管理单元通用")

        equipment = Resource.objects.get(code="EQ-OSC-01")
        procedure = self.create_published(lab=lab)
        response = admin.post(f"/resources/{equipment.pk}/safety/{procedure.pk}/ack/")
        self.assertEqual(response.status_code, 302)

    def test_web_review_form_records_return_note_and_shows_it_to_submitter(self):
        draft, _ = safety.save_draft(self.admin, self.room.pk, self.procedure_values())
        safety.submit_for_review(self.admin, draft.pk)
        self.client.force_login(self.reviewer)
        page = self.client.get(f"/resources/{self.room.pk}/safety/")
        self.assertContains(page, "确认来源并发布")
        self.assertContains(page, "退回修订")
        note = "来源批准信息需要补充核对。"
        result = self.client.post(f"/safety-procedures/{draft.pk}/return/", {"review_note": note})
        self.assertEqual(result.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "RETURNED")
        self.client.force_login(self.admin)
        page = self.client.get(f"/resources/{self.room.pk}/safety/")
        self.assertContains(page, note)
        self.assertContains(page, "基于此版本创建修订草稿")
        self.assertEqual(self.client.post(f"/safety-procedures/{draft.pk}/revise/").status_code, 302)
        self.assertTrue(SafetyProcedure.objects.filter(resource=self.room, status="DRAFT", version=draft.version + 1).exists())

    def test_submitted_and_published_safety_attribution_cannot_be_rewritten(self):
        procedure = self.create_published()
        mutations = [
            ("submitted_at", procedure.submitted_at + timedelta(seconds=1)),
            ("reviewed_by", self.admin),
            ("reviewed_at", procedure.reviewed_at + timedelta(seconds=1)),
            ("review_note", "覆盖复核意见"),
            ("published_by", self.admin),
            ("published_at", procedure.published_at + timedelta(seconds=1)),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                procedure.refresh_from_db()
                setattr(procedure, field, value)
                with self.assertRaises(ValidationError):
                    procedure.save()

    def test_superseded_safety_publication_attribution_stays_immutable(self):
        procedure = self.create_published()
        procedure.status = "SUPERSEDED"
        procedure.save()
        procedure.published_at += timedelta(seconds=1)
        with self.assertRaisesRegex(ValidationError, "归属记录不可覆盖"):
            procedure.save()

    def test_future_effective_version_cannot_be_published(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        with self.assertRaisesRegex(services.BusinessError, "未来版本"):
            self.create_published(effective_from=tomorrow, source_effective_date=timezone.localdate())

    def test_new_version_can_replace_an_overdue_published_version(self):
        expired = self.create_published()
        SafetyProcedure.objects.filter(pk=expired.pk).update(review_due_date=timezone.localdate() - timedelta(days=1))
        self.assertIsNone(safety.current_procedure(self.room))

        replacement = self.create_published(source_version="test-v2", change_reason="复核已过期版本后发布新版本")
        expired.refresh_from_db()
        self.assertEqual(expired.status, "SUPERSEDED")
        self.assertEqual(safety.current_procedure(self.room).pk, replacement.pk)

    def test_expired_review_version_is_historical_not_current_or_a_fake_booking_block(self):
        procedure = self.create_published()
        SafetyProcedure.objects.filter(pk=procedure.pk).update(review_due_date=timezone.localdate() - timedelta(days=1))
        self.assertIsNone(safety.current_procedure(self.room))
        self.assertEqual(safety.latest_published_procedure(self.room).pk, procedure.pk)
        self.assertFalse(safety.needs_ack(self.teacher, self.room))
        booking_day=timezone.localdate()+timedelta(days=1)
        start=timezone.make_aware(datetime.combine(booking_day,time(10)),services.TZ)
        booking=services.create_reservation(self.teacher,self.room.pk,start,start+timedelta(hours=1),"仅验证过期安全版本不被误当现行制度",request_key=uuid.uuid4())
        self.assertEqual(booking.status,"PENDING")
        self.client.force_login(self.teacher)
        page=self.client.get(f"/resources/{self.room.pk}/")
        self.assertContains(page,"过期或待复核")
        self.assertContains(page,"仅供追溯来源")
        self.assertNotContains(page,"阅读并确认此版本")
        api_client=Client()
        login=api_client.post("/api/mini/v1/auth/login/",json.dumps({"username":"teacher","password":"Demo@2026"}),content_type="application/json")
        token=login.json()["data"]["token"];headers={"HTTP_AUTHORIZATION":"Bearer "+token}
        detail=api_client.get(f"/api/mini/v1/resources/{self.room.pk}/",**headers)
        self.assertFalse(detail.json()["data"]["safety_current"])
        denied=api_client.post(f"/api/mini/v1/resources/{self.room.pk}/safety/{procedure.pk}/ack/","{}",content_type="application/json",**headers)
        self.assertNotEqual(denied.status_code,200)

    def test_published_content_and_acknowledgement_are_append_only(self):
        procedure = self.create_published()
        procedure.operating_cautions = "改写已发布内容"
        with self.assertRaises(ValidationError):
            procedure.save()
        with self.assertRaises(ValidationError):
            procedure.delete()
        acknowledgement = safety.acknowledge(self.teacher, procedure.pk)
        with self.assertRaises(ValidationError):
            acknowledgement.delete()
        acknowledgement.acknowledged_at = timezone.now() + timedelta(days=1)
        with self.assertRaises(ValidationError):
            acknowledgement.save()

    def test_room_scope_applies_to_web_management_and_mini_read_confirmation(self):
        client = Client()
        client.force_login(self.outside)
        self.assertEqual(client.get(f"/resources/{self.room.pk}/safety/").status_code, 403)
        client.force_login(self.admin)
        self.assertContains(client.get(f"/resources/{self.room.pk}/safety/"), "批准来源")

        procedure = self.create_published()
        api_client = Client()
        response = api_client.post("/api/mini/v1/auth/login/", json.dumps({"username": "teacher", "password": "Demo@2026"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        token = response.json()["data"]["token"]
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
        detail = api_client.get(f"/api/mini/v1/resources/{self.room.pk}/", **headers)
        self.assertEqual(detail.json()["data"]["safety_procedure"]["version"], procedure.version)
        acknowledged = api_client.post(f"/api/mini/v1/resources/{self.room.pk}/safety/{procedure.pk}/ack/", "{}", content_type="application/json", **headers)
        self.assertEqual(acknowledged.status_code, 200, acknowledged.content)
        self.assertTrue(acknowledged.json()["data"]["acknowledged"])


class ResourceCatalogFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.student = User.objects.get(username="student")
        cls.lab = Lab.objects.get(code="LAB-101")

    def test_web_filters_multiple_rooms_by_category_capacity_facility_and_status(self):
        client = Client()
        client.force_login(self.student)
        response = client.get("/resources/", {"kind": "ROOM", "lab": self.lab.pk, "capacity_min": "30", "facility": "投影"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.code for row in response.context["resources"]], ["ROOM-101"])
        category = client.get("/resources/", {"kind": "ROOM", "category": "传感器"})
        self.assertEqual([row.code for row in category.context["resources"]], ["ROOM-102"])
        status = client.get("/resources/", {"kind": "ROOM", "service_status": "NORMAL"})
        # One synthetic room is deliberately marked maintenance in the demo fixture.
        self.assertEqual(status.context["page"].paginator.count, 5)

    def test_unfiltered_web_catalog_uses_stable_pagination_order(self):
        client = Client()
        client.force_login(self.student)
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", UnorderedObjectListWarning)
            response = client.get("/resources/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse([item for item in captured if issubclass(item.category, UnorderedObjectListWarning)])
        rows = list(response.context["resources"])
        self.assertEqual([row.pk for row in rows], sorted(row.pk for row in rows))

    def test_mini_catalog_uses_the_same_filters_and_data(self):
        client = Client()
        login = client.post("/api/mini/v1/auth/login/", json.dumps({"username": "student", "password": "Demo@2026"}), content_type="application/json")
        token = login.json()["data"]["token"]
        response = client.get("/api/mini/v1/resources/", {"kind": "ROOM", "category": "计算机", "capacity_min": "30"}, HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["code"] for row in response.json()["data"]["items"]], ["ROOM-301"])
        self.assertEqual(response.json()["data"]["items"][0]["space_category"], "计算机教学实验室")
