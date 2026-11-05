"""Native transport parity, ownership and retry receipts; simulated data only."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, Client
from django.db import connections
from django.utils import timezone
from . import services as s
from .models import User, Resource, Reservation, ReviewNote, Incident, AuditEvent, Notification, UserRole

BASE="/api/mini/v1"


class PhaseOneApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):call_command("seed_demo",verbosity=0)

    def setUp(self):
        self.client=Client(enforce_csrf_checks=True)
        self.student=User.objects.get(username="student");self.other=User.objects.get(username="student2")
        self.teacher=User.objects.get(username="teacher");self.admin=User.objects.get(username="admin")
        self.eq=Resource.objects.get(code="EQ-OSC-01");self.restricted=Resource.objects.get(code="EQ-CAM-01")
        self.room=Resource.objects.get(code="ROOM-101")
        self.start=(timezone.localtime()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        self.token=self.session(self.student)

    def session(self,user):
        session=SessionStore();session.update({"channel":"campus-mini-v1","mini_user_id":user.pk});session.set_expiry(7200);session.create()
        return session.session_key

    def post(self,path,body,token=None):
        return self.client.post(BASE+path,json.dumps(body),content_type="application/json",HTTP_AUTHORIZATION="Bearer "+(token or self.token))

    def get(self,path,data=None,token=None):
        return self.client.get(BASE+path,data or {},HTTP_AUTHORIZATION="Bearer "+(token or self.token))

    def book(self,user=None,resource=None):
        return s.create_reservation(user or self.student,(resource or self.restricted).pk,
            self.start,self.start+timedelta(hours=1),"模拟课程用途")

    def incident_values(self,**changes):
        values={"resource_id":self.eq.pk,"kind":"FAULT","description":"模拟资源异常现象",
                "occurred_at":(timezone.localtime()-timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                "request_key":str(uuid.uuid4())}
        values.update(changes);return values

    def test_supplement_preserves_pending_and_retries_create_one_note(self):
        b=self.book();body={"reason":"补充模拟课程安排","request_key":str(uuid.uuid4())}
        a=self.post(f"/bookings/{b.pk}/supplement/",body)
        self.assertEqual(a.status_code,200,a.content)
        b.refresh_from_db();self.assertEqual(b.status,"PENDING")
        notifications=Notification.objects.count()
        repeated=self.post(f"/bookings/{b.pk}/supplement/",body)
        self.assertTrue(repeated.json()["data"]["replayed"])
        self.assertEqual(a.json()["data"]["note_id"],repeated.json()["data"]["note_id"])
        self.assertEqual(ReviewNote.objects.filter(reservation=b).count(),1)
        self.assertEqual(Notification.objects.count(),notifications)
        self.assertEqual(AuditEvent.objects.filter(action="MINI_ACTION_RECEIPT").count(),1)

    def test_changed_content_or_different_operation_cannot_reuse_action_key(self):
        b=self.book();key=str(uuid.uuid4())
        self.assertEqual(self.post(f"/bookings/{b.pk}/supplement/",{"reason":"原说明","request_key":key}).status_code,200)
        changed=self.post(f"/bookings/{b.pk}/supplement/",{"reason":"不同说明","request_key":key})
        self.assertEqual(changed.status_code,409)
        self.assertEqual(self.post("/incidents/",self.incident_values(request_key=key)).status_code,409)
        self.assertFalse(Incident.objects.exists())

    def test_supplement_requires_owner_pending_valid_text_and_key(self):
        b=self.book(self.other)
        self.assertEqual(self.post(f"/bookings/{b.pk}/supplement/",{"reason":"越权","request_key":str(uuid.uuid4())}).status_code,404)
        own=self.book(resource=self.eq)
        self.assertEqual(self.post(f"/bookings/{own.pk}/supplement/",{"reason":"已确认不能补充","request_key":str(uuid.uuid4())}).status_code,409)
        pending=self.book()
        for data in [{"reason":"","request_key":str(uuid.uuid4())},{"reason":"x"},{"reason":"x","request_key":"bad"},{"reason":"x","request_key":str(uuid.uuid4()),"actor":self.admin.pk}]:
            with self.subTest(data=data):self.assertEqual(self.post(f"/bookings/{pending.pk}/supplement/",data).status_code,400)
        self.assertFalse(ReviewNote.objects.exists())

    def test_supplement_retry_after_approval_returns_original_receipt_without_reopening(self):
        b=self.book();body={"reason":"补充说明","request_key":str(uuid.uuid4())}
        self.assertEqual(self.post(f"/bookings/{b.pk}/supplement/",body).status_code,200)
        s.approve(self.admin,b.pk,True,"模拟批准")
        retry=self.post(f"/bookings/{b.pk}/supplement/",body)
        self.assertEqual(retry.status_code,200,retry.content);self.assertTrue(retry.json()["data"]["replayed"])
        detail=self.get(f"/bookings/{b.pk}/").json()["data"]
        self.assertEqual(detail["status"],"CONFIRMED");self.assertFalse(detail["can_supplement"])
        self.assertEqual(len(detail["supplements"]),1)

    def test_ordinary_unlinked_incident_matches_web_service_rules_and_replay(self):
        body=self.incident_values();a=self.post("/incidents/",body)
        self.assertEqual(a.status_code,200,a.content)
        item=Incident.objects.get(pk=a.json()["data"]["id"])
        self.assertEqual(item.reporter_id,self.student.pk);self.assertEqual(item.status,"PENDING")
        self.assertFalse(item.blocks_service)
        retry=self.post("/incidents/",body)
        self.assertEqual(retry.status_code,200);self.assertTrue(retry.json()["data"]["replayed"])
        self.assertEqual(Incident.objects.count(),1)
        self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"NORMAL")

    def test_linked_in_use_incident_preserves_graded_blocking(self):
        b=self.book(resource=self.eq)
        s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        body=self.incident_values(reservation=b.pk)
        result=self.post("/incidents/",body)
        self.assertEqual(result.status_code,200,result.content)
        self.assertTrue(result.json()["data"]["blocks_service"])
        b.refresh_from_db();self.assertEqual(b.status,"IN_USE")
        self.eq.refresh_from_db();self.assertEqual(self.eq.service_status,"ISOLATED")

    def test_incident_rejects_other_reservations_wrong_resource_and_invalid_fields(self):
        other=self.book(self.other,self.eq)
        foreign=self.post("/incidents/",self.incident_values(reservation=other.pk))
        self.assertEqual(foreign.status_code,400)
        own=self.book()
        self.assertEqual(self.post("/incidents/",self.incident_values(reservation=own.pk)).status_code,409)
        for changes in [{"kind":"SAFETY"},{"description":""},{"resource_id":"9"*100},{"reservation":"9"*100},{"resource_id":{"id":1}},{"occurred_at":"bad"},{"request_key":"bad"},{"reported_by":self.admin.pk}]:
            with self.subTest(changes=changes):
                self.assertLess(self.post("/incidents/",self.incident_values(**changes)).status_code,500)
                self.assertGreaterEqual(self.post("/incidents/",self.incident_values(**changes)).status_code,400)
        self.assertFalse(Incident.objects.exists())

    def test_incident_future_time_rolls_back_all_business_and_receipt_writes(self):
        before=AuditEvent.objects.count();notifications=Notification.objects.count()
        body=self.incident_values(occurred_at=self.start.isoformat())
        result=self.post("/incidents/",body)
        self.assertEqual(result.status_code,409)
        self.assertEqual(Incident.objects.count(),0)
        self.assertEqual(AuditEvent.objects.count(),before)
        self.assertEqual(Notification.objects.count(),notifications)
        self.assertFalse(self.get("/receipts/"+body["request_key"]+"/").json()["data"]["found"])

    def test_receipt_lookup_is_owner_scoped_and_absent_is_not_failure(self):
        body=self.incident_values();self.post("/incidents/",body)
        own=self.get("/receipts/"+body["request_key"]+"/")
        self.assertTrue(own.json()["data"]["found"])
        foreign=self.get("/receipts/"+body["request_key"]+"/",token=self.session(self.other))
        self.assertFalse(foreign.json()["data"]["found"]);self.assertIsNone(foreign.json()["data"]["result"])
        self.assertIn("不代表",foreign.json()["data"]["notice"])
        self.assertEqual(self.get("/receipts/not-a-uuid/").status_code,400)

    def test_role_revocation_and_deactivation_reject_mutations_and_receipt_lookup(self):
        UserRole.objects.filter(user=self.student).delete()
        self.assertEqual(self.post("/incidents/",self.incident_values()).status_code,403)
        self.assertFalse(Incident.objects.exists())
        token=self.session(self.other);User.objects.filter(pk=self.other.pk).update(is_active=False)
        self.assertEqual(self.get("/receipts/"+str(uuid.uuid4())+"/",token=token).status_code,401)

    def test_resource_slot_preview_is_scoped_anonymous_and_input_bounded(self):
        b=self.book(self.other,self.eq)
        result=self.get(f"/resources/{self.eq.pk}/",{"slot_day":self.start.date().isoformat(),"duration_minutes":60})
        self.assertEqual(result.status_code,200,result.content)
        preview=result.json()["data"]["slot_options"]
        selected=next(x for x in preview["items"] if x["label"]=="10:00—11:00")
        self.assertFalse(selected["available"])
        for secret in [b.code,b.purpose,self.other.display_name]:self.assertNotIn(secret,result.content.decode())
        for values in [{"slot_day":"bad"},{"slot_day":"9999-12-31"},{"slot_day":self.start.date().isoformat(),"duration_minutes":"9"*100}]:
            with self.subTest(values=values):self.assertEqual(self.get(f"/resources/{self.eq.pk}/",values).status_code,400)

    def test_booking_details_next_step_and_notes_do_not_cross_owners(self):
        b=self.book();s.review_note(self.admin,b.pk,"补充场景材料")
        result=self.get(f"/bookings/{b.pk}/").json()["data"]
        self.assertEqual(result["next_step"]["responsible_role"],"对应实验室管理员")
        self.assertTrue(result["can_supplement"]);self.assertTrue(result["can_report"])
        self.assertEqual(result["supplements"][0]["content"],"补充场景材料")
        self.assertEqual(self.get(f"/bookings/{b.pk}/",token=self.session(self.other)).status_code,404)

    def test_business_error_does_not_leave_transport_receipt(self):
        body=self.incident_values()
        with patch("core.services.report_incident",side_effect=s.BusinessError("模拟失败")):
            self.assertEqual(self.post("/incidents/",body).status_code,409)
        self.assertFalse(AuditEvent.objects.filter(action="MINI_ACTION_RECEIPT").exists())
        self.assertFalse(Incident.objects.exists())


class PhaseOneActionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        call_command("seed_demo",verbosity=0)
        self.student=User.objects.get(username="student")
        self.eq=Resource.objects.get(code="EQ-OSC-01")
        self.restricted=Resource.objects.get(code="EQ-CAM-01")
        self.start=(timezone.localtime()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        session=SessionStore();session.update({"channel":"campus-mini-v1","mini_user_id":self.student.pk});session.set_expiry(7200);session.create()
        self.token=session.session_key

    def parallel(self,path,first,second):
        barrier=Barrier(2)
        def work(body):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                r=Client().post(BASE+path,json.dumps(body),content_type="application/json",HTTP_AUTHORIZATION="Bearer "+self.token)
                return r.status_code,r.json()
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(work,first);b=pool.submit(work,second)
            return [a.result(timeout=25),b.result(timeout=25)]

    def test_parallel_same_supplement_has_one_note_and_one_receipt(self):
        b=s.create_reservation(self.student,self.restricted.pk,self.start,self.start+timedelta(hours=1),"模拟课程")
        body={"reason":"并发同内容补充","request_key":str(uuid.uuid4())}
        results=self.parallel(f"/bookings/{b.pk}/supplement/",body,body)
        self.assertEqual([x[0] for x in results],[200,200])
        self.assertEqual(ReviewNote.objects.filter(reservation=b).count(),1)
        self.assertEqual(AuditEvent.objects.filter(action="MINI_ACTION_RECEIPT").count(),1)

    def test_parallel_incident_same_key_changed_body_has_one_winner(self):
        body={"resource_id":self.eq.pk,"kind":"FAULT","description":"并发异常A",
              "occurred_at":(timezone.localtime()-timedelta(minutes=1)).isoformat(),"request_key":str(uuid.uuid4())}
        results=self.parallel("/incidents/",body,dict(body,description="并发异常B"))
        self.assertEqual(sorted(x[0] for x in results),[200,409])
        self.assertEqual(Incident.objects.count(),1)
        self.assertEqual(AuditEvent.objects.filter(action="MINI_ACTION_RECEIPT").count(),1)
