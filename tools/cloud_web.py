"""Only the Render reverse proxy may reach this cloud-demo WSGI listener."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.cloud_settings")
from config.wsgi import application
from waitress import serve


if __name__ == "__main__":
    serve(application, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), threads=4, trusted_proxy="*", trusted_proxy_count=1, trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"}, channel_timeout=60)
