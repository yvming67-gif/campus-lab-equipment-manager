"""Cloud configuration and process lifecycle checks; no real cloud credentials."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.cloud_environment import database, hostname, required, storage_options
from tools.start_cloud import supervise


class CloudConfigurationTests(unittest.TestCase):
    def test_database_decodes_credentials_and_requires_tls(self):
        d = database({"DATABASE_URL": "postgresql://demo:p%40ss%3Aword@db.example/neondb?sslmode=require&channel_binding=require"})
        self.assertEqual(d["PASSWORD"], "p@ss:word")
        self.assertEqual(d["OPTIONS"]["channel_binding"], "require")
        self.assertEqual(d["OPTIONS"]["sslmode"], "require")
        self.assertEqual(d["PORT"], "5432")
        self.assertTrue(d["DISABLE_SERVER_SIDE_CURSORS"])

    def test_bad_database_configuration_never_echoes_credentials(self):
        for value in ["sqlite:///local.db", "postgresql://u:secret-marker@db/x?sslmode=disable", "postgresql://u:secret-marker@db/x?sslmode=require&sslmode=disable", "postgresql://u:secret-marker@db:bad/x", "postgresql://u:secret-marker@db/x#fragment"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as result:
                    database({"DATABASE_URL": value})
                self.assertNotIn("secret-marker", str(result.exception))

    def test_hostname_cannot_be_wildcard_or_url(self):
        for value in ["*", ".example.com", "https://app.example", "app.example/path", "app..example"]:
            with self.assertRaises(ValueError):
                hostname(value)
        self.assertEqual(hostname("Demo.onrender.com"), "demo.onrender.com")

    def test_missing_runtime_secret_is_not_replaced_with_ephemeral_key(self):
        with self.assertRaisesRegex(ValueError, "DJANGO_SECRET_KEY"):
            required({}, "DJANGO_SECRET_KEY", 32)

    def test_storage_preserves_private_objects_and_uses_explicit_credentials(self):
        env = dict(AWS_ENDPOINT_URL_S3="https://branch.storage.example", AWS_ACCESS_KEY_ID="mock-key", AWS_SECRET_ACCESS_KEY="mock-secret", AWS_REGION="us-east-2", CAMPUS_STORAGE_BUCKET="campus-lab-evidence")
        opts = storage_options(env)
        self.assertIsNone(opts["default_acl"])
        self.assertTrue(opts["querystring_auth"])
        self.assertFalse(opts["file_overwrite"])
        self.assertIsNone(opts["custom_domain"])
        for endpoint in ["http://bucket.example", "https://u:secret@bucket.example", "https://bucket.example/path", "https://bucket.example?q=1"]:
            with self.assertRaises(ValueError):
                storage_options(dict(env, AWS_ENDPOINT_URL_S3=endpoint))

    def test_cloud_profile_and_collectstatic_without_remote_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            env = os.environ.copy()
            env.update(DJANGO_SETTINGS_MODULE="config.cloud_settings", CAMPUS_LOCAL_DIR=folder, DJANGO_SECRET_KEY="test-cloud-"*6, MOCK_IDP_KEY="test-identity-"*6, DEMO_MODE="1", DATABASE_URL="postgresql://u:p@db.example/neondb?sslmode=require", RENDER_EXTERNAL_HOSTNAME="cloud-test.example", ALLOWED_HOSTS="", AWS_ENDPOINT_URL_S3="https://branch.storage.example", AWS_ACCESS_KEY_ID="mock-key", AWS_SECRET_ACCESS_KEY="mock-secret", AWS_REGION="us-east-2", CAMPUS_STORAGE_BUCKET="campus-lab-evidence")
            code = "import django; django.setup(); from django.conf import settings as s; assert not s.DEBUG and s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE; assert s.ALLOWED_HOSTS==['cloud-test.example']; assert s.DATABASES['default']['HOST']=='db.example'; from django.core.management import call_command; call_command('collectstatic',interactive=False,verbosity=0); from django.contrib.staticfiles.storage import staticfiles_storage; assert staticfiles_storage.url('app.css').startswith('/static/')"
            result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_worker_stops_sibling_instead_of_leaving_false_ready_web(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/"late.txt"
            sleeper = "import time,pathlib; time.sleep(4); pathlib.Path(" + repr(str(marker)) + ").write_text('orphan')"
            start = time.monotonic()
            code = supervise([[sys.executable, "-c", sleeper], [sys.executable, "-c", "raise SystemExit(7)"]])
            self.assertEqual(code, 7)
            self.assertLess(time.monotonic()-start, 3)
            self.assertFalse(marker.exists())

    def test_waitress_accepts_the_render_proxy_configuration(self):
        from waitress.adjustments import Adjustments
        config = Adjustments(host="0.0.0.0", port=10000, threads=4, trusted_proxy="*", trusted_proxy_count=1, trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"})
        self.assertEqual(config.trusted_proxy_headers, {"x-forwarded-for", "x-forwarded-proto"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
