"""Shared course-only identity adapter. Never authenticates school credentials."""
import json
import os
import secrets
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from .models import User


def verify_demo_password(username, password):
    if not settings.DEMO_MODE:
        return None
    path = settings.LOCAL / "mock-identity.json"
    if not path.exists():
        temporary = path.with_name(".mock-identity-" + secrets.token_hex(8) + ".tmp")
        try:
            temporary.write_text(json.dumps({u.username: make_password("Demo@2026") for u in User.objects.all()}), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    hashes = json.loads(path.read_text("utf-8"))
    user = User.objects.filter(username=username, is_active=True).first()
    if user and check_password(password, hashes.get(username, "!")):
        return user
    return None
