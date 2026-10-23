"""JSON transport for the native WeChat student/teacher client.
All booking state changes use the same transaction services as the web UI.
"""
import hashlib
import json
import re
from functools import wraps
from django import forms
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Exists, OuterRef
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from . import services as s
from .demo_identity import verify_demo_password
from .schedules import resource_schedule
from .domain_labels import event_label, notification_label
from .forms import BookingForm, QueryForm, ActionForm
from .models import User, Lab, Resource, Reservation, Notification, Policy, AuditEvent
from .request_context import correlation_id

SESSION_SECONDS = 7200


class DemoLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(max_length=256, strip=False)


class ApiError(Exception):
    def __init__(self, code, message, status=400, fields=None):
        self.code, self.message, self.status, self.fields = code, message, status, fields or {}


def response(data=None, error=None, status=200):
    body = {"ok": error is None, "request_id": str(correlation_id.get() or "")}
    body["error" if error else "data"] = error if error else data
    result = JsonResponse(body, status=status, json_dumps_params={"ensure_ascii": False})
    result["Cache-Control"] = "no-store"
    return result


def authenticate(request):
    header = request.headers.get("Authorization", "")
    match = re.fullmatch(r"Bearer ([a-z0-9]{32})", header)
    if not match:
        raise ApiError("AUTH_REQUIRED", "请先登录后继续操作", 401)
    session = SessionStore(session_key=match[1])
    user_id = session.get("mini_user_id")
    if session.get("channel") != "campus-mini-v1" or not user_id:
        raise ApiError("SESSION_EXPIRED", "登录已失效，请重新登录", 401)
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user:
        session.delete()
        raise ApiError("SESSION_EXPIRED", "账号已停用，请重新登录", 401)
    if not user.has_role("STUDENT", "TEACHER"):
        session.delete()
        raise ApiError("ROLE_NOT_SUPPORTED", "当前身份请使用网页管理端", 403)
    request.mini_user, request.mini_session = user, session


def endpoint(*methods, public=False):
    def decorate(func):
        @csrf_exempt
        @wraps(func)
        def call(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    raise ApiError("METHOD_NOT_ALLOWED", "不支持此请求方式", 405)
                if not settings.DEMO_MODE and not settings.CAMPUS_MINI_IDENTITY_ADAPTER:
                    raise ApiError("ADAPTER_UNAVAILABLE", "小程序身份适配器尚未启用", 503)
                if not public:
                    authenticate(request)
                return response(func(request, *args, **kwargs))
            except ApiError as error:
                result = response(error={"code": error.code, "message": error.message, "fields": error.fields}, status=error.status)
                if error.status == 405:
                    result["Allow"] = ", ".join(methods)
                return result
            except (Http404, ObjectDoesNotExist):
                return response(error={"code": "NOT_FOUND", "message": "记录不存在或不可访问", "fields": {}}, status=404)
            except s.BusinessError as error:
                return response(error={"code": "RULE_REJECTED", "message": str(error), "fields": {}}, status=409)
            except IntegrityError:
                return response(error={"code": "CONFLICT", "message": "时段或记录已变化，请刷新后重试", "fields": {}}, status=409)
        return call
    return decorate


def payload(request, allowed):
    if request.content_type != "application/json":
        raise ApiError("JSON_REQUIRED", "请求内容必须为JSON", 415)
    if len(request.body) > 16384:
        raise ApiError("BODY_TOO_LARGE", "请求内容过长", 413)
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ApiError("INVALID_JSON", "请求内容不是有效JSON")
    if not isinstance(data, dict):
        raise ApiError("INVALID_JSON", "请求内容必须为对象")
    unknown = set(data) - set(allowed)
    if unknown:
        raise ApiError("INVALID_FIELDS", "请求包含不支持的字段")
    # Form validation expects scalar values and must never stringify a nested object.
    if any(not isinstance(value, (str, int, float, bool, type(None))) for value in data.values()):
        raise ApiError("INVALID_FIELDS", "字段值格式不正确")
    return data


def cleaned(form):
    if not form.is_valid():
        errors = {key: [str(x) for x in value] for key, value in form.errors.items()}
        raise ApiError("VALIDATION_ERROR", "请检查填写的字段", fields=errors)
    return form.cleaned_data


def page_data(request, qs, serializer):
    raw = request.GET.get("page", "1")
    if not re.fullmatch(r"[1-9][0-9]{0,5}", raw):
        raise ApiError("VALIDATION_ERROR", "页码必须为正整数", fields={"page": ["页码无效"]})
    paginator = Paginator(qs, 20)
    page = paginator.get_page(int(raw))
    return {"items": [serializer(x) for x in page.object_list], "page": page.number, "total": paginator.count, "has_more": page.has_next()}


def date_text(value):
    return timezone.localtime(value).isoformat(timespec="seconds") if value else None


def user_data(user):
    return {"id": user.pk, "username": user.username, "display_name": user.display_name,
            "roles": list(user.roles.values("code", "name")), "can_book_room": user.has_role("TEACHER")}


def policy_data():
    policy = Policy.objects.get(pk=1)
    return {key: getattr(policy, key) for key in ("window_days", "slot_minutes", "max_hours", "equipment_quota", "room_quota", "simultaneous_equipment", "grace_minutes")}


def resource_data(resource, times=None, user=None):
    available, label = s.availability(resource, *times, applicant=user) if times else (None, "请选择目标时段")
    data = {"id": resource.pk, "code": resource.code, "name": resource.name, "kind": resource.kind,
            "kind_label": resource.get_kind_display(), "description": resource.description,
            "lab": {"id": resource.lab_id, "name": resource.lab.name, "location": resource.lab.location},
            "service_status": resource.service_status, "service_label": resource.get_service_status_display(),
            "requires_approval": resource.requires_approval, "available": available, "available_label": label}
    if resource.kind == "ROOM":
        data.update(capacity=resource.room.capacity, safety_note=resource.room.safety_note)
    else:
        data.update(category=resource.equipment.category, risk=resource.equipment.get_risk_display(),
                    qualification=resource.equipment.qualification.name if resource.equipment.qualification_id else "无额外培训要求",
                    physical_label=resource.equipment.get_physical_status_display())
    return data


def resources_qs():
    return Resource.objects.select_related("lab", "equipment__qualification", "room").order_by("kind", "code")


def booking_data(booking):
    return {"id": booking.pk, "code": booking.code, "resource_id": booking.resource_id,
            "resource_name": booking.resource.name, "resource_code": booking.resource.code,
            "kind": booking.resource.kind, "lab_name": booking.resource.lab.name,
            "starts_at": date_text(booking.starts_at), "ends_at": date_text(booking.ends_at),
            "status": booking.status, "status_label": booking.get_status_display()+" · 待复核" if booking.review_required else booking.get_status_display(),
            "approval_mode": booking.approval_mode, "purpose": booking.purpose,
            "participants": booking.participants, "version": booking.version, "reason": booking.review_reason if booking.review_required else booking.reason,
            "review_required":booking.review_required,
            "created_at": date_text(booking.created_at),
            "can_cancel": booking.status in s.FUTURE and booking.starts_at > timezone.now()}


def my_bookings(user):
    return Reservation.objects.filter(applicant=user).select_related("resource__lab").order_by("-created_at", "-pk")


@endpoint("POST", public=True)
def login(request):
    if not settings.DEMO_MODE:
        data=payload(request,{"code"})
        if not isinstance(data.get('code'),str) or not 1<=len(data['code'])<=256:raise ApiError('VALIDATION_ERROR','必须提供微信一次性登录凭据')
        from django.utils.module_loading import import_string
        try:identity=import_string(settings.CAMPUS_MINI_IDENTITY_ADAPTER)(code=data['code'])
        except Exception:raise ApiError('ADAPTER_UNAVAILABLE','学校小程序身份服务暂不可用',503)
        user=User.objects.filter(school_no=identity.get('school_no'),is_active=True).first() if isinstance(identity,dict) else None
        if not user or not user.has_role('STUDENT','TEACHER'):raise ApiError('ROLE_NOT_SUPPORTED','无有效本地预约身份',403)
        with transaction.atomic():
            session=SessionStore();session['channel']='campus-mini-v1';session['mini_user_id']=user.pk;session.set_expiry(SESSION_SECONDS);session.create()
            s.audit(user,user,'LOGIN',new={'identity':'SCHOOL','channel':'WECHAT_MINIPROGRAM'},reason='学校小程序适配器核验一次性凭据')
        return {'token':session.session_key,'expires_in':SESSION_SECONDS,'user':user_data(user)}
    data = payload(request, {"username", "password"})
    values = cleaned(DemoLoginForm(data))
    identity = (request.META.get("REMOTE_ADDR", "") + ":" + values["username"]).encode()
    key = "mini-login-" + hashlib.sha256(identity).hexdigest()
    if cache.get(key, 0) >= 6:
        raise ApiError("TOO_MANY_ATTEMPTS", "尝试次数过多，请一分钟后重试", 429)
    user = verify_demo_password(values["username"], values["password"])
    if not user:
        if not cache.add(key, 1, timeout=60):
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=60)
        raise ApiError("INVALID_CREDENTIALS", "模拟账号或演示密码不正确，或账号已停用", 401)
    if not user.has_role("STUDENT", "TEACHER"):
        raise ApiError("ROLE_NOT_SUPPORTED", "管理身份请使用网页管理端", 403)
    cache.delete(key)
    with transaction.atomic():
        session = SessionStore()
        session["channel"] = "campus-mini-v1";session["mini_user_id"] = user.pk
        session.set_expiry(SESSION_SECONDS);session.create()
        s.audit(user, user, "LOGIN", new={"identity": "MOCK", "channel": "WECHAT_MINIPROGRAM"}, reason="小程序模拟身份校验通过")
    return {"token": session.session_key, "expires_in": SESSION_SECONDS, "user": user_data(user)}


@endpoint("POST")
def logout(request):
    request.mini_session.delete()
    return {"signed_out": True}


@endpoint("GET")
def me(request):
    user = request.mini_user;mine = my_bookings(user);now = timezone.now()
    return {"user": user_data(user), "policy": policy_data(), "server_time": date_text(now),
            "overdue": s.overdue_for(user, now), "stats": {
                "pending": mine.filter(status="PENDING").count(), "confirmed": mine.filter(status="CONFIRMED").count(),
                "using": mine.filter(status="IN_USE").count(), "unread": user.notifications.filter(read_at__isnull=True).count(),
                "future_equipment": mine.filter(resource__kind="EQUIPMENT", status__in=s.FUTURE, starts_at__gt=now).count(),
                "future_room": mine.filter(resource__kind="ROOM", status__in=s.FUTURE, starts_at__gt=now).count()}}


@endpoint("GET")
def resources(request):
    values = cleaned(QueryForm(request.GET))
    qs = resources_qs()
    if values["q"]:
        qs = qs.filter(Q(name__icontains=values["q"]) | Q(code__icontains=values["q"]))
    if values["kind"]:
        qs = qs.filter(kind=values["kind"])
    if values["lab"]:
        qs = qs.filter(lab=values["lab"])
    times = (values["starts_at"], values["ends_at"]) if values["starts_at"] else None
    result = page_data(request, qs, lambda item: resource_data(item, times, request.mini_user))
    result["labs"] = list(Lab.objects.order_by("code").values("id", "name", "location"))
    return result


@endpoint("GET")
def resource_detail(request, pk):
    resource = get_object_or_404(resources_qs(), pk=pk)
    values = cleaned(QueryForm(request.GET))
    times = (values["starts_at"], values["ends_at"]) if values["starts_at"] else None
    result = resource_data(resource, times, request.mini_user)
    result["policy"] = policy_data()
    result["can_apply"] = request.mini_user.has_role("TEACHER") if resource.kind == "ROOM" else True
    result["accessories"] = list(resource.accessories.filter(is_active=True).order_by("code").values("code", "name", "quantity"))
    raw_page=request.GET.get("schedule_page","1")
    if not re.fullmatch(r"[1-9][0-9]{0,5}",raw_page):
        raise ApiError("VALIDATION_ERROR","占用列表页码必须为正整数",fields={"schedule_page":["页码无效"]})
    day=request.GET.get("schedule_day","")
    try:schedule=resource_schedule(resource,day)
    except ValueError as error:raise ApiError("VALIDATION_ERROR",str(error),fields={"schedule_day":[str(error)]})
    page=Paginator(schedule,30).get_page(int(raw_page))
    result["schedule"] = [{"starts_at": date_text(x.starts_at), "ends_at": date_text(x.ends_at), "status":x.status,"status_label":x.get_status_display()} for x in page.object_list]
    result.update(schedule_total=page.paginator.count,schedule_page=page.number,schedule_has_more=page.has_next(),schedule_day=day)
    return result


@endpoint("GET", "POST")
def bookings(request):
    if request.method == "POST":
        data = payload(request, {"resource_id", "starts_at", "ends_at", "purpose", "participants", "request_key"})
        try:
            resource_id = forms.IntegerField(min_value=1).clean(data.get("resource_id"))
        except forms.ValidationError:
            raise ApiError("VALIDATION_ERROR", "请选择有效资源", fields={"resource_id": ["资源编号无效"]})
        get_object_or_404(Resource, pk=resource_id)
        values = cleaned(BookingForm(data))
        result = s.create_reservation(request.mini_user, resource_id, **values)
        return booking_data(my_bookings(request.mini_user).get(pk=result.pk))
    qs = my_bookings(request.mini_user)
    status = request.GET.get("status", "")
    if status and status not in dict(Reservation.STATUS):
        raise ApiError("VALIDATION_ERROR", "预约状态无效")
    if status:
        qs = qs.filter(status=status)
    if request.GET.get("q"):
        q = request.GET["q"][:150]
        qs = qs.filter(Q(code__icontains=q) | Q(resource__name__icontains=q) | Q(resource__code__icontains=q))
    return page_data(request, qs, booking_data)


@endpoint("GET")
def booking_detail(request, pk):
    booking = get_object_or_404(my_bookings(request.mini_user), pk=pk)
    result = booking_data(booking)
    events = list(AuditEvent.objects.filter(entity_type="reservation", entity_id=booking.pk).select_related("actor").order_by("-created_at", "-pk")[:100])
    result["timeline"] = [{"id": e.pk, "action": e.action, "label": event_label(e.action), "at": date_text(e.created_at), "reason": e.reason, "actor": e.actor.display_name if e.actor_id else "系统"} for e in reversed(events)]
    return result


@endpoint("POST")
def cancel(request, pk):
    booking = get_object_or_404(my_bookings(request.mini_user), pk=pk)
    data = payload(request, {"reason", "version"})
    values = cleaned(ActionForm(data))
    try:
        version = forms.IntegerField(min_value=1).clean(data.get("version"))
    except forms.ValidationError:
        raise ApiError("VALIDATION_ERROR", "请刷新预约详情后重试", fields={"version": ["缺少有效版本号"]})
    result = s.cancel(request.mini_user, booking.pk, values["reason"], expected=version)
    return booking_data(my_bookings(request.mini_user).get(pk=result.pk))


@endpoint("GET")
def notifications(request):
    qs = Notification.objects.filter(recipient=request.mini_user).annotate(owned_booking=Exists(Reservation.objects.filter(applicant=request.mini_user, pk=OuterRef("audit__entity_id")))).select_related("audit").order_by("-created_at", "-pk")
    def serialize(item):
        return {"id": item.pk, "kind": item.kind, "kind_label":notification_label(item.kind), "content": item.content, "created_at": date_text(item.created_at),
                "read_at": date_text(item.read_at), "booking_id": item.audit.entity_id if item.audit.entity_type == "reservation" and item.owned_booking else None}
    result = page_data(request, qs, serialize)
    result["unread"] = qs.filter(read_at__isnull=True).count()
    return result


@endpoint("POST")
def read_notification(request, pk):
    item = get_object_or_404(Notification, pk=pk, recipient=request.mini_user)
    Notification.objects.filter(pk=item.pk, read_at__isnull=True).update(read_at=timezone.now())
    return {"read": True}


@endpoint("POST")
def read_all(request):
    return {"updated": Notification.objects.filter(recipient=request.mini_user, read_at__isnull=True).update(read_at=timezone.now())}
