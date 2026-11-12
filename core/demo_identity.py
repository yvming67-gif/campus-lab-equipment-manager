"""Shared course-only identity adapter. Never authenticates school credentials."""
import json
import os
import secrets
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth import logout
from django.core import signing
from django.shortcuts import redirect
from .models import User

PUBLIC_DEMO_ROLE_CODES = frozenset({"STUDENT", "TEACHER"})
REVIEW_IDENTITIES = {
    "admin": ("A-DEMO-01", "LAB_ADMIN", "实验室管理员"),
    "tech": ("M-DEMO-01", "TECHNICIAN", "维修人员"),
    "sysadmin": ("Y-DEMO-01", "SYS_ADMIN", "系统管理员"),
    "director": ("D-DEMO-01", "DIRECTOR", "实验教学中心主任"),
}
REVIEW_SESSION = "course_demo_review"
REVIEW_MAX_AGE = 4 * 60 * 60


def review_key():
    key = getattr(settings, "CAMPUS_REVIEW_CODE", "")
    return key if settings.DEMO_MODE and settings.PUBLIC_DEMO_MODE and 24 <= len(key) <= 128 else ""


def review_identity(user):
    if not user.is_active or user.is_staff or user.is_superuser:
        return None
    identity = REVIEW_IDENTITIES.get(user.username)
    if identity and user.school_no == identity[0] and set(user.roles.values_list("code", flat=True)) == {identity[1]}:
        return identity
    return None


def review_accounts():
    return [
        (user, identity[2])
        for user in User.objects.filter(username__in=REVIEW_IDENTITIES).order_by("pk")
        if (identity := review_identity(user))
    ]


def grant_review_session(request, user, key):
    request.session[REVIEW_SESSION] = signing.dumps(user.pk, key=key, salt=REVIEW_SESSION)


def has_review_session(request):
    key = review_key()
    if not key or not review_identity(request.user):
        return False
    try:
        return signing.loads(request.session.get(REVIEW_SESSION, ""), key=key, fallback_keys=[],
                             salt=REVIEW_SESSION, max_age=REVIEW_MAX_AGE) == request.user.pk
    except signing.BadSignature:
        return False


def public_demo_user_allowed(user):
    """Allow only active accounts whose complete role set is public-safe."""
    if not settings.PUBLIC_DEMO_MODE:
        return True
    if not user.is_active:
        return False
    role_codes = set(user.roles.values_list("code", flat=True))
    return bool(role_codes & PUBLIC_DEMO_ROLE_CODES) and role_codes <= PUBLIC_DEMO_ROLE_CODES


def public_demo_accounts():
    accounts = User.objects.filter(is_active=True).order_by("pk")
    if not settings.PUBLIC_DEMO_MODE:
        return accounts
    return [user for user in accounts if public_demo_user_allowed(user)]


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
    """Revoke already-issued shared-demo staff sessions after public-mode activation."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (settings.DEMO_MODE and settings.PUBLIC_DEMO_MODE and request.user.is_authenticated
                and not public_demo_user_allowed(request.user) and not has_review_session(request)):
            logout(request)
            return redirect("login")
        return self.get_response(request)
