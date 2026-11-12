"""Shared course-only identity adapter. Never authenticates school credentials."""
import json
import os
import secrets
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth import logout
from django.core import signing
from django.shortcuts import redirect
from django.utils.crypto import constant_time_compare
from .models import User

PUBLIC_DEMO_ROLE_CODES = frozenset({"STUDENT", "TEACHER"})
DEMO_STAFF_IDENTITIES = {
    "admin": ("A-DEMO-01", "LAB_ADMIN"),
    "tech": ("M-DEMO-01", "TECHNICIAN"),
    "sysadmin": ("Y-DEMO-01", "SYS_ADMIN"),
    "director": ("D-DEMO-01", "DIRECTOR"),
}
STAFF_SESSION = "course_demo_staff"
STAFF_MAX_AGE = 4 * 60 * 60


def staff_password(user):
    if not settings.DEMO_MODE or not settings.PUBLIC_DEMO_MODE:
        return ""
    if not user.is_active or user.is_staff or user.is_superuser:
        return ""
    identity = DEMO_STAFF_IDENTITIES.get(user.username)
    password = settings.CAMPUS_DEMO_STAFF_PASSWORDS.get(user.username, "")
    if (identity and 24 <= len(password) <= 128 and user.school_no == identity[0]
            and {role.code for role in user.roles.all()} == {identity[1]}):
        return password
    return ""


def web_demo_accounts():
    accounts = User.objects.filter(is_active=True).order_by("pk").prefetch_related("roles")
    if not settings.PUBLIC_DEMO_MODE:
        return accounts
    return [
        user for user in accounts
        if public_demo_user_allowed(user) or staff_password(user)
    ]


def authenticate_staff_demo(username, submitted):
    if username not in DEMO_STAFF_IDENTITIES or not 24 <= len(submitted) <= 128:
        return None, ""
    user = User.objects.filter(username=username, is_active=True).first()
    password = staff_password(user) if user else ""
    if password and constant_time_compare(submitted, password):
        token = signing.dumps(user.pk, key=password, salt=STAFF_SESSION)
        return user, token
    return None, ""


def valid_staff_demo_token(user, token):
    password = staff_password(user)
    if not password or not isinstance(token, str):
        return False
    try:
        return signing.loads(token, key=password, fallback_keys=[], salt=STAFF_SESSION,
                             max_age=STAFF_MAX_AGE) == user.pk
    except signing.BadSignature:
        return False


def public_demo_user_allowed(user):
    """Allow only active accounts whose complete role set is public-safe."""
    if not settings.PUBLIC_DEMO_MODE:
        return True
    if not user.is_active:
        return False
    role_codes = {role.code for role in user.roles.all()}
    return bool(role_codes & PUBLIC_DEMO_ROLE_CODES) and role_codes <= PUBLIC_DEMO_ROLE_CODES


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
    if user and public_demo_user_allowed(user) and check_password(password, hashes.get(username, "!")):
        return user
    return None


class PublicDemoSessionMiddleware:
    """Require a current staff credential for privileged sessions in the public demo."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (settings.DEMO_MODE and settings.PUBLIC_DEMO_MODE and request.user.is_authenticated
                and not public_demo_user_allowed(request.user)
                and not valid_staff_demo_token(request.user, request.session.get(STAFF_SESSION))):
            logout(request)
            return redirect("login")
        return self.get_response(request)
