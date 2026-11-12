import json
from datetime import timedelta
from uuid import uuid4

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .demo_identity import public_demo_user_allowed, verify_demo_password, web_demo_accounts
from .models import ApprovalDecision, Reservation, Resource, User

STAFF_PASSWORDS = {
    username: f"test-only-password-for-{username}-2026"
    for username in ("admin", "tech", "sysadmin", "director")
}


class PublicDemoIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.student = User.objects.get(username="student")
        cls.admin = User.objects.get(username="admin")
        cls.teacher = User.objects.get(username="teacher")

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True, CAMPUS_DEMO_STAFF_PASSWORDS=STAFF_PASSWORDS)
    def test_roles_sign_in_through_the_same_web_entry(self):
        choices = (
            ("admin", "实验室运营"),
            ("tech", "维修工作台"),
            ("sysadmin", "系统设置"),
            ("director", "中心概览"),
        )
        self.assertNotContains(self.client.get("/access/"), 'href="/review/"')
        self.assertEqual(self.client.get("/review/").status_code, 404)
        for username, heading in choices:
            with self.subTest(username=username):
                client = Client()
                identity = client.post("/login/", follow=True)
                self.assertContains(identity, f'value="{username}"')
                self.assertContains(identity, 'value="student"')
                self.assertNotContains(identity, STAFF_PASSWORDS[username])
                for password in ("Demo@2026", STAFF_PASSWORDS["tech" if username == "admin" else "admin"]):
                    denied = client.post("/identity/", {"username": username, "password": password})
                    self.assertContains(denied, "账号或密码不正确")
                    self.assertNotIn("_auth_user_id", client.session)
                accepted = client.post(
                    "/identity/", {"username": username, "password": STAFF_PASSWORDS[username]}, follow=True
                )
                self.assertContains(accepted, f"<h1>{heading}</h1>")
                self.assertEqual(accepted.wsgi_request.user.username, username)
                client.post("/logout/")
                self.assertNotIn("_auth_user_id", client.session)

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True, CAMPUS_DEMO_STAFF_PASSWORDS=STAFF_PASSWORDS)
    def test_normal_login_supports_a_shared_approval_flow(self):
        student = Client()
        student.post("/login/")
        student.post("/identity/", {"username": "student", "password": "Demo@2026"}, follow=True)
        camera = Resource.objects.get(code="EQ-CAM-01")
        start = (timezone.localtime() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        submitted = student.post(f"/resources/{camera.pk}/", {
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "purpose": "验证不同岗位在同一系统内协作",
            "request_key": uuid4(),
            "participants": 1,
            "expected_approval_mode": "MANUAL",
        })
        self.assertEqual(submitted.status_code, 302, submitted.context["form"].errors if submitted.context else "")
        booking = Reservation.objects.get(applicant=self.student, resource=camera, status="PENDING")
        decision = {"choice": "approve", "version": booking.version, "reason": "已核对模拟申请"}
        url = f"/bookings/{booking.pk}/review/"

        sysadmin = Client()
        sysadmin.post("/login/")
        sysadmin.post("/identity/", {"username": "sysadmin", "password": STAFF_PASSWORDS["sysadmin"]}, follow=True)
        self.assertEqual(sysadmin.post(url, decision).status_code, 404)

        admin = Client()
        admin.post("/login/")
        admin.post("/identity/", {"username": "admin", "password": STAFF_PASSWORDS["admin"]}, follow=True)
        queue = admin.get("/bookings/?queue=review")
        self.assertContains(queue, f'href="{submitted.url}"')
        self.assertEqual(admin.post(url, decision).status_code, 302)
        self.assertTrue(ApprovalDecision.objects.filter(reservation=booking).exists())
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertContains(student.get(submitted.url), "已确认")

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True, CAMPUS_DEMO_STAFF_PASSWORDS=STAFF_PASSWORDS)
    def test_staff_password_rotation_revokes_sessions_and_pending_callbacks(self):
        client = Client()
        client.post("/login/")
        response = client.post("/identity/", {"username": "admin", "password": STAFF_PASSWORDS["admin"]}, follow=True)
        self.assertContains(response, "<h1>实验室运营</h1>")
        pending = Client()
        pending.post("/login/")
        callback = pending.post("/identity/", {"username": "admin", "password": STAFF_PASSWORDS["admin"]}).url
        response = Client().post("/api/mini/v1/auth/login/", json.dumps({"username": "admin", "password": STAFF_PASSWORDS["admin"]}), content_type="application/json")
        self.assertEqual(response.status_code, 401)
        with override_settings(CAMPUS_DEMO_STAFF_PASSWORDS={**STAFF_PASSWORDS, "admin": STAFF_PASSWORDS["admin"] + "-new"}):
            self.assertRedirects(client.get("/"), "/login/", fetch_redirect_response=False)
            self.assertEqual(pending.get(callback).status_code, 403)
            self.assertNotIn("_auth_user_id", pending.session)
        self.assertNotIn("_auth_user_id", client.session)

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True, CAMPUS_DEMO_STAFF_PASSWORDS={"admin": "short"})
    def test_unconfigured_staff_accounts_do_not_appear(self):
        client = Client()
        response = client.post("/login/", follow=True)
        self.assertNotContains(response, 'value="admin"')
        self.assertNotContains(response, 'value="tech"')

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True)
    def test_public_demo_identity_page_lists_only_student_and_teacher_accounts(self):
        self.admin.roles.add(self.student.roles.get(code="STUDENT"))
        client = Client()
        client.post("/login/")
        response = client.get("/identity/")
        self.assertContains(response, "student")
        self.assertContains(response, "teacher")
        self.assertNotContains(response, 'value="admin"')
        self.assertNotContains(response, "资源管理员")
        self.assertNotContains(response, "sysadmin")

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True)
    def test_shared_public_demo_password_cannot_authenticate_staff_role(self):
        self.assertIsNone(verify_demo_password("admin", "Demo@2026"))
        self.assertIsNotNone(verify_demo_password("student", "Demo@2026"))
        self.assertIsNotNone(verify_demo_password("teacher", "Demo@2026"))

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True)
    def test_existing_staff_web_session_is_revoked(self):
        client = Client()
        client.force_login(self.admin)
        response = client.get("/dashboard/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", client.session)

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True)
    def test_public_mini_login_rejects_staff_but_keeps_student_flow(self):
        client = Client()
        denied = client.post("/api/mini/v1/auth/login/", json.dumps({"username": "admin", "password": "Demo@2026"}), content_type="application/json")
        allowed = client.post("/api/mini/v1/auth/login/", json.dumps({"username": "student", "password": "Demo@2026"}), content_type="application/json")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200, allowed.content)

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True)
    def test_public_demo_rejects_account_with_both_student_and_admin_roles(self):
        self.admin.roles.add(self.student.roles.get(code="STUDENT"))
        self.assertFalse(public_demo_user_allowed(self.admin))
        self.assertNotIn(self.admin, web_demo_accounts())

        web_client = Client()
        web_client.post("/login/")
        web_response = web_client.post("/identity/", {"username": "admin", "password": "Demo@2026"})
        self.assertEqual(web_response.status_code, 200)
        self.assertNotIn("_auth_user_id", web_client.session)

        client = Client()
        client.force_login(self.admin)
        self.assertRedirects(client.get("/dashboard/"), "/login/", fetch_redirect_response=False)
        mini_response = Client().post(
            "/api/mini/v1/auth/login/",
            json.dumps({"username": "admin", "password": "Demo@2026"}),
            content_type="application/json",
        )
        self.assertEqual(mini_response.status_code, 401)

    @override_settings(DEMO_MODE=True, PUBLIC_DEMO_MODE=True)
    def test_existing_mini_session_is_revoked_after_account_gains_staff_role(self):
        login = Client().post(
            "/api/mini/v1/auth/login/",
            json.dumps({"username": "student", "password": "Demo@2026"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        token = login.json()["data"]["token"]

        self.student.roles.add(self.admin.roles.get(code="LAB_ADMIN"))
        client = Client()
        response = client.get("/api/mini/v1/me/", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(response.json()["error"]["code"], "PUBLIC_DEMO_ACCESS_REVOKED")

        from django.contrib.sessions.backends.db import SessionStore
        self.assertFalse(SessionStore(session_key=token).exists(token))
