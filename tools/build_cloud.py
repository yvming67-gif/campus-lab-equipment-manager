"""Render build: install the pinned cloud dependencies and collect static files."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.cloud_settings")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements-cloud.txt")], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / "manage.py"), "collectstatic", "--noinput"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
