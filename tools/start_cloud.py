"""Run web and business jobs as one supervised, free-tier demonstration service."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def bootstrap():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.cloud_settings")
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection
    from django.core.files.storage import default_storage
    if not settings.DEMO_MODE or settings.DEBUG:
        raise RuntimeError("Cloud demo requires simulated identity and DEBUG=0")
    # Session-level lock spans migrations and initialization during rolling deploys.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", [1602240221, 20261022])
        try:
            call_command("migrate", interactive=False, verbosity=1)
            call_command("seed_demo", if_empty=True)
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [1602240221, 20261022])
    default_storage.connection.meta.client.head_bucket(Bucket=default_storage.bucket_name)
    call_command("run_jobs")  # Catch up missed deadlines after a platform sleep.
    connection.close()


def supervise(commands):
    children = []
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    previous = {s: signal.signal(s, stop) for s in [signal.SIGINT, signal.SIGTERM]}
    result = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT))
        while not stopped:
            for child in children:
                code = child.poll()
                if code is not None:
                    result = code or 1
                    stopped = True
                    break
            if not stopped:
                time.sleep(0.5)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 8
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for s, handler in previous.items():
            signal.signal(s, handler)
    return result


def main():
    bootstrap()
    return supervise([[sys.executable, str(ROOT/"tools/cloud_web.py")], [sys.executable, str(ROOT/"manage.py"), "run_jobs", "--loop"]])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Do not echo a connection string, credential or remote error response.
        print("Cloud startup failed; error type=" + type(error).__name__ + ". Check required variables, database migrations and private storage access.", file=sys.stderr)
        raise SystemExit(1)
