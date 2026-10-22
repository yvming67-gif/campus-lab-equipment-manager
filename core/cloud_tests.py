"""HTTPS cloud-profile integration using an isolated test database, not Neon."""
from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase, TransactionTestCase


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

    def test_internal_http_probe_reports_database_failure_instead_of_healthy_redirect(self):
        from unittest.mock import patch
        from django.db import DatabaseError
        client = self.client_for_cloud()
        with patch("config.health.connection.cursor", side_effect=DatabaseError("isolated failure")):
            response = client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")


class CloudJobRecoveryTests(TransactionTestCase):
    def test_worker_reconnects_after_terminated_scan_and_catchup_is_not_duplicated(self):
        if settings.SETTINGS_MODULE != "config.cloud_settings":
            from unittest import SkipTest
            raise SkipTest("This integration suite requires the explicit cloud profile")
        from datetime import timedelta
        from io import StringIO
        from unittest.mock import patch
        import psycopg
        from django.db import connection
        from django.utils import timezone
        from core import services
        from core.models import AuditEvent, Delivery, JobHeartbeat, Reservation, Resource, User

        self.addCleanup(connection.close)
        call_command("seed_demo", if_empty=True, stdout=StringIO())
        start = timezone.localtime(timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking = services.create_reservation(User.objects.get(username="student"), Resource.objects.get(code="EQ-OSC-01").pk, start, start + timedelta(hours=1), "断连恢复隔离验证")
        scan_at = start + timedelta(hours=2)
        scans = 0
        sleeps = 0
        snapshots = []

        class StopAfterThreeScans(Exception):
            pass

        def scan():
            nonlocal scans
            scans += 1
            if scans == 1:
                # Terminate only this isolated test worker's connection. The scan
                # then encounters a real server-side disconnect, not a mock error.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    worker_pid = cursor.fetchone()[0]
                with psycopg.connect(**connection.get_connection_params(), autocommit=True) as killer:
                    self.assertTrue(killer.execute("SELECT pg_terminate_backend(%s)", [worker_pid]).fetchone()[0])
            return services.run_jobs(scan_at)

        def sleep(seconds):
            nonlocal sleeps
            sleeps += 1
            heartbeat = JobHeartbeat.objects.get(name="business-jobs")
            if sleeps == 1:
                self.assertIsNotNone(heartbeat.last_error_at)
                self.assertTrue(heartbeat.error_code)
                self.assertIsNone(heartbeat.last_success)
            else:
                self.assertEqual(heartbeat.last_success, scan_at)
                self.assertEqual(heartbeat.error_code, "")
                self.assertEqual(Reservation.objects.get(pk=booking.pk).status, "EXPIRED")
                snapshots.append((AuditEvent.objects.filter(action="EXPIRE").count(), Delivery.objects.count()))
            if sleeps == 3:
                raise StopAfterThreeScans

        with patch("core.management.commands.run_jobs.run_jobs", side_effect=scan), patch("core.management.commands.run_jobs.time.sleep", side_effect=sleep), self.assertLogs("campus.jobs", level="ERROR"), self.assertRaises(StopAfterThreeScans):
            call_command("run_jobs", loop=True, stdout=StringIO())
        self.assertEqual(scans, 3)
        self.assertEqual(snapshots[0][0], 1)
        self.assertEqual(snapshots[0], snapshots[1])
