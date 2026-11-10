import json

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from .demo_identity import public_demo_accounts, public_demo_user_allowed, verify_demo_password
from .models import User


class PublicDemoIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.student = User.objects.get(username="student")
        cls.admin = User.objects.get(username="admin")
        cls.teacher = User.objects.get(username="teacher")

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
        self.assertNotIn(self.admin, public_demo_accounts())

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
