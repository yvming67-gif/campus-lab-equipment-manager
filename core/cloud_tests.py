"""HTTPS cloud-profile integration using an isolated test database, not Neon."""
from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase


class CloudProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        if settings.SETTINGS_MODULE != "config.cloud_settings":
            from unittest import SkipTest
            raise SkipTest("This integration suite requires the explicit cloud profile")
        call_command("seed_demo", if_empty=True, verbosity=0)

    def client_for_cloud(self):
        return Client(enforce_csrf_checks=True, HTTP_HOST="cloud-test.example")

    def test_health_uses_database_and_does_not_expose_credentials(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
            self.assertTrue(cursor.fetchone()[0])
        response = self.client_for_cloud().get("/health/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertNotIn("DATABASE_URL", response.content.decode())

    def test_https_csrf_identity_callback_and_protected_pages(self):
        client = self.client_for_cloud()
        client.get("/login/", secure=True)
        token = client.cookies["csrftoken"].value
        response = client.post("/login/", {"csrfmiddlewaretoken": token}, secure=True, HTTP_ORIGIN="https://cloud-test.example")
        self.assertRedirects(response, "/identity/", fetch_redirect_response=False)
        client.get("/identity/", secure=True)
        response = client.post("/identity/", {"username": "student", "password": "Demo@2026", "csrfmiddlewaretoken": client.cookies["csrftoken"].value}, secure=True, HTTP_ORIGIN="https://cloud-test.example")
        self.assertEqual(response.status_code, 302)
        callback = client.get(response["Location"], secure=True)
        self.assertEqual(callback.status_code, 302)
        page = client.get("/resources/", secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.wsgi_request.user.is_authenticated)
        self.assertEqual(client.get("/manage/", secure=True).status_code, 403)

    def test_http_redirects_and_cross_origin_post_is_rejected(self):
        client = self.client_for_cloud()
        self.assertEqual(client.get("/login/").status_code, 301)
        client.get("/login/", secure=True)
        response = client.post("/login/", {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}, secure=True, HTTP_ORIGIN="https://untrusted.example")
        self.assertEqual(response.status_code, 403)

    def test_cloud_reinitialization_preserves_disabled_accounts(self):
        from .models import User
        user = User.objects.get(username="student2")
        user.is_active = False
        user.save()
        call_command("seed_demo", if_empty=True, verbosity=0)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
