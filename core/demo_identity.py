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
from .models import Role, User, UserRole

PUBLIC_DEMO_ROLE_CODES = frozenset({"STUDENT", "TEACHER"})
ROLE_TASKS = {
    "STUDENT": "查设备、申请借用、跟进进度",
    "TEACHER": "安排课程实验、申请教室和设备",
    "LAB_ADMIN": "审核申请、办理交接、验收维修",
    "TECHNICIAN": "处理指派工单、提交维修结果",
    "SYS_ADMIN": "维护人员授权、参数和开放日历",
    "DIRECTOR": "查看中心报表与审计记录",
}
PRIVATE_APPLICANTS = {
    "student_lab": ("S-DEMO-LAB", "STUDENT", "模拟实验学生"),
    "teacher_lab": ("T-DEMO-LAB", "TEACHER", "模拟实验教师"),
}
PRIVATE_IDENTITIES = {
    **{username: (school_no, role) for username, (school_no, role, _) in PRIVATE_APPLICANTS.items()},
    "admin": ("A-DEMO-01", "LAB_ADMIN"),
    "tech": ("M-DEMO-01", "TECHNICIAN"),
    "sysadmin": ("Y-DEMO-01", "SYS_ADMIN"),
    "director": ("D-DEMO-01", "DIRECTOR"),
}
PRIVATE_SESSION = "course_demo_private"
PRIVATE_MAX_AGE = 4 * 60 * 60


def configured_private_password(username):
    if not settings.DEMO_MODE or not settings.PUBLIC_DEMO_MODE:
        return ""
    password = settings.CAMPUS_DEMO_PRIVATE_PASSWORDS.get(username, "")
    return password if 24 <= len(password) <= 128 else ""


def provision_private_applicants():
    for username, (school_no, role_code, display_name) in PRIVATE_APPLICANTS.items():
        if not configured_private_password(username):
            continue
        role = Role.objects.filter(code=role_code).first()
        if not role or User.objects.filter(school_no=school_no).exclude(username=username).exists():
            continue
        user, created = User.objects.get_or_create(
            username=username, defaults={
                "school_no": school_no, "display_name": display_name, "password": make_password(None),
            },
        )
        if created:
            UserRole.objects.create(user=user, role=role)


def private_password(user):
    if not user.is_active or user.is_staff or user.is_superuser:
        return ""
    identity = PRIVATE_IDENTITIES.get(user.username)
    password = configured_private_password(user.username)
    if (identity and password and user.school_no == identity[0]
            and {role.code for role in user.roles.all()} == {identity[1]}):
        return password
    return ""


def web_demo_accounts():
    accounts = User.objects.filter(is_active=True).order_by("pk").prefetch_related("roles")
    visible = []
    for user in accounts:
        if not (public_demo_user_allowed(user) or private_password(user)):
            continue
        user.role_tasks = "；".join(ROLE_TASKS[role.code] for role in user.roles.all() if role.code in ROLE_TASKS)
        visible.append(user)
    return visible


def authenticate_private_demo(username, submitted):
    if username not in PRIVATE_IDENTITIES or not 24 <= len(submitted) <= 128:
        return None, ""
    user = User.objects.filter(username=username, is_active=True).first()
    password = private_password(user) if user else ""
    if password and constant_time_compare(submitted, password):
        token = signing.dumps(user.pk, key=password, salt=PRIVATE_SESSION)
        return user, token
    return None, ""


def valid_private_demo_token(user, token):
    password = private_password(user)
    if not password or not isinstance(token, str):
        return False
    try:
        return signing.loads(token, key=password, fallback_keys=[], salt=PRIVATE_SESSION,
                             max_age=PRIVATE_MAX_AGE) == user.pk
    except signing.BadSignature:
        return False


def public_demo_user_allowed(user):
    """Allow only active accounts whose complete role set is public-safe."""
    if not settings.PUBLIC_DEMO_MODE:
        return True
    if not user.is_active or user.username in PRIVATE_IDENTITIES:
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
    """Require current account credentials for private sessions in the public demo."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (settings.DEMO_MODE and settings.PUBLIC_DEMO_MODE and request.user.is_authenticated
                and not public_demo_user_allowed(request.user)
                and not valid_private_demo_token(request.user, request.session.get(PRIVATE_SESSION))):
            logout(request)
            return redirect("login")
        return self.get_response(request)
