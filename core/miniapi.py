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
from .demo_identity import public_demo_user_allowed, verify_demo_password
from .schedules import resource_schedule
from .domain_labels import event_label, notification_label
from .forms import BookingForm, QueryForm, ActionForm, IncidentForm
from .models import User, Lab, Resource, Reservation, Notification, Policy, AuditEvent, UserQualification, SafetyProcedure, Campus, Building
from .experience import slot_options, next_step
from . import safety as safety_service
from .opening_calendar import schedule_by_resource, weekly_schedule
from .catalog import filter_resources
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
    if not public_demo_user_allowed(user):
        session.delete()
        raise ApiError("PUBLIC_DEMO_ACCESS_REVOKED", "当前账号不再具备公开演示访问权限，请重新登录", 401)
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


def page_data(request, qs, serializer, *, prepare=None):
    raw = request.GET.get("page", "1")
    if not re.fullmatch(r"[1-9][0-9]{0,5}", raw):
        raise ApiError("VALIDATION_ERROR", "页码必须为正整数", fields={"page": ["页码无效"]})
    paginator = Paginator(qs, 20)
    page = paginator.get_page(int(raw))
    items = list(page.object_list)
    if prepare:
        serializer = prepare(items)
    return {"items": [serializer(x) for x in items], "page": page.number, "total": paginator.count, "has_more": page.has_next()}


def date_text(value):
    return timezone.localtime(value).isoformat(timespec="seconds") if value else None


def user_data(user):
    return {"id": user.pk, "username": user.username, "display_name": user.display_name,
            "roles": list(user.roles.values("code", "name")), "can_book_room": user.has_role("TEACHER")}


def policy_data():
    policy = Policy.objects.get(pk=1)
    return {key: getattr(policy, key) for key in ("window_days", "slot_minutes", "max_hours", "equipment_quota", "room_quota", "simultaneous_equipment", "grace_minutes")}


def resource_data(resource, times=None, user=None, *, opening_schedule=None, opening_summary=None):
    available, label = s.availability(resource, *times, applicant=user) if times else (None, "请选择目标时段")
    reservation_open, reservation_block_reason = s.resource_reservation_state(resource)
    data = {"id": resource.pk, "code": resource.code, "name": resource.name, "kind": resource.kind,
            "kind_label": resource.get_kind_display(), "description": resource.description,
            "lab": {"id": resource.lab_id, "name": resource.lab.name, "location": resource.lab.location, "active": resource.lab.is_active},
            "location_label": resource.location_label, "location": resource.location_display,
            "service_status": resource.service_status, "service_label": s.resource_service_label(resource),
            "can_reserve": reservation_open, "reservation_block_reason": reservation_block_reason,
            "requires_approval": resource.requires_approval, "available": available, "available_label": label}
    if resource.kind == "ROOM":
        data.update(capacity=resource.room.capacity, space_category=resource.room.space_category,
                    campus=resource.room.building.campus.name if resource.room.building_id else "",
                    building=resource.room.building.name if resource.room.building_id else "",
                    floor_label=resource.room.floor_label, room_number=resource.room.room_number,
                    facilities=resource.room.facilities,
                    opening_schedule=weekly_schedule(resource) if opening_schedule is None else opening_schedule,
                    opening_summary=opening_summary)
    else:
        data.update(category=resource.equipment.category, risk=resource.equipment.get_risk_display(),
                    qualification=resource.equipment.qualification.name if resource.equipment.qualification_id else "无额外培训要求",
                    physical_label=resource.equipment.get_physical_status_display(),
                    portable=resource.equipment.portable,
                    handover_location=resource.equipment.handover_location)
    current_ids = {item.pk for item in safety_service.current_procedures(resource)}
    safety_rows = []
    for procedure in safety_service.latest_published_procedures(resource):
        safety_rows.append({
            "id": procedure.pk, "version": procedure.version, "title": procedure.title,
            "scope": "MANAGEMENT_UNIT" if procedure.lab_scope_id else "RESOURCE",
            "scope_label": procedure.scope_label,
            "risk_categories": procedure.risk_categories, "applicable_users": procedure.applicable_users,
            "prerequisites": procedure.prerequisites, "required_ppe": procedure.required_ppe,
            "pre_use_checks": procedure.pre_use_checks, "operating_cautions": procedure.operating_cautions,
            "prohibited_actions": procedure.prohibited_actions, "incident_route": procedure.incident_route,
            "source_title": procedure.source_title, "source_reference": procedure.source_reference,
            "source_version": procedure.source_version,
            "effective_from": procedure.effective_from.isoformat() if procedure.effective_from else None,
            "review_due_date": procedure.review_due_date.isoformat() if procedure.review_due_date else None,
            "requires_acknowledgement": procedure.requires_acknowledgement,
            "is_current": procedure.pk in current_ids,
            "acknowledged": safety_service.acknowledged(user, procedure),
        })
    data["safety_procedures"] = safety_rows
    # Keep the previous singular fields for old mini-program builds while new
    # clients render every applicable scope.
    primary = next((item for item in safety_rows if item["scope"] == "RESOURCE"),
                   safety_rows[0] if safety_rows else None)
    data["safety_procedure"] = primary
    data["safety_current"] = bool(primary and primary["is_current"])
    data["safety_acknowledged"] = bool(primary and primary["acknowledged"])
    return data


def resources_qs():
    return Resource.objects.select_related("lab", "equipment__qualification", "equipment__access_room__room__building__campus", "room__building__campus").order_by("kind", "code")


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
            "can_cancel": booking.status in s.FUTURE and booking.starts_at > timezone.now(),
            "can_supplement": booking.status == "PENDING", "can_report": True}


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
    upcoming=mine.filter(Q(status='IN_USE')|Q(status='CONFIRMED',ends_at__gt=now)).order_by('starts_at')[:3]
    qualifications=UserQualification.objects.filter(user=user).select_related('qualification').order_by('qualification__code')
    return {"user": user_data(user), "policy": policy_data(), "server_time": date_text(now),
            "upcoming":[booking_data(b) for b in upcoming],
            "qualifications":[{"code":q.qualification.code,"name":q.qualification.name,"expires_at":date_text(q.expires_at),"valid":not q.revoked and q.issued_at<=now and (q.expires_at is None or q.expires_at>=now)} for q in qualifications],
            "overdue": s.overdue_for(user, now), "stats": {
                "pending": mine.filter(status="PENDING").count(), "confirmed": mine.filter(status="CONFIRMED").count(),
                "using": mine.filter(status="IN_USE").count(), "unread": user.notifications.filter(read_at__isnull=True).count(),
                "future_equipment": mine.filter(resource__kind="EQUIPMENT", status__in=s.FUTURE, starts_at__gt=now).count(),
                "future_room": mine.filter(resource__kind="ROOM", status__in=s.FUTURE, starts_at__gt=now).count()}}


@endpoint("GET")
def resources(request):
    values = cleaned(QueryForm(request.GET))
    qs = filter_resources(resources_qs(), values)
    times = (values["starts_at"], values["ends_at"]) if values["starts_at"] else None
    opening_date = timezone.localtime(times[0]).date() if times else timezone.localdate()

    def prepare_catalog(items):
        rooms = [item for item in items if item.kind == "ROOM"]
        today = timezone.localdate()
        schedules = schedule_by_resource(rooms, today, days=7)
        summaries = schedules if opening_date == today else schedule_by_resource(rooms, opening_date, days=1)
        return lambda item: resource_data(item, times, request.mini_user,
            opening_schedule=schedules.get(item.pk), opening_summary=summaries.get(item.pk, [None])[0])

    result = page_data(request, qs, None, prepare=prepare_catalog)
    result["labs"] = list(Lab.objects.order_by("code").values("id", "name", "location"))
    result["campuses"] = list(Campus.objects.filter(is_active=True).order_by("code").values("id", "name"))
    result["buildings"] = list(Building.objects.filter(is_active=True,campus__is_active=True).order_by("campus__code","code").values("id","campus_id","name"))
    return result


@endpoint("GET")
def resource_detail(request, pk):
    resource = get_object_or_404(resources_qs(), pk=pk)
    values = cleaned(QueryForm(request.GET))
    times = (values["starts_at"], values["ends_at"]) if values["starts_at"] else None
    result = resource_data(resource, times, request.mini_user)
    result["policy"] = policy_data()
    result["can_apply"] = request.mini_user.has_role("TEACHER") if resource.kind == "ROOM" else True
    if resource.kind == "EQUIPMENT":
        accessory_rows = list(resource.accessories.order_by("code").values("code", "name", "quantity", "is_active"))
        result["accessories"] = [
            {"code": item["code"], "name": item["name"], "quantity": item["quantity"]}
            for item in accessory_rows if item["is_active"]
        ]
    else:
        result["accessories"] = []
    raw_page=request.GET.get("schedule_page","1")
    if not re.fullmatch(r"[1-9][0-9]{0,5}",raw_page):
        raise ApiError("VALIDATION_ERROR","占用列表页码必须为正整数",fields={"schedule_page":["页码无效"]})
    day=request.GET.get("schedule_day","")
    try:schedule=resource_schedule(resource,day)
    except ValueError as error:raise ApiError("VALIDATION_ERROR",str(error),fields={"schedule_day":[str(error)]})
    page=Paginator(schedule,30).get_page(int(raw_page))
    result["schedule"] = [{"starts_at": date_text(x.starts_at), "ends_at": date_text(x.ends_at), "status":x.status,"status_label":x.get_status_display()} for x in page.object_list]
    result.update(schedule_total=page.paginator.count,schedule_page=page.number,schedule_has_more=page.has_next(),schedule_day=day)
    if "slot_day" in request.GET:
        try:
            result["slot_options"] = slot_options(resource,request.mini_user,
                request.GET["slot_day"],request.GET.get("duration_minutes","60"))
        except (ValueError,s.BusinessError) as error:
            raise ApiError("VALIDATION_ERROR",str(error),fields={"slot_day":[str(error)]}) from None
    return result


@endpoint("POST")
def safety_ack(request, pk, procedure_id):
    resource = get_object_or_404(Resource.objects.select_related("lab"), pk=pk)
    procedure = get_object_or_404(SafetyProcedure.objects.select_related("resource", "lab_scope"), pk=procedure_id, status="PUBLISHED")
    item = safety_service.acknowledge(request.mini_user, procedure.pk, resource=resource)
    return {"acknowledged": True, "version": procedure.version,
            "acknowledged_at": date_text(item.acknowledged_at),
            "notice": "此阅读确认不代表培训、考核或进入许可"}


@endpoint("GET", "POST")
def bookings(request):
    if request.method == "POST":
        data = payload(request, {"resource_id", "starts_at", "ends_at", "purpose", "participants", "request_key", "expected_approval_mode"})
        try:
            resource_id = forms.IntegerField(min_value=1).clean(data.get("resource_id"))
        except forms.ValidationError:
            raise ApiError("VALIDATION_ERROR", "请选择有效资源", fields={"resource_id": ["资源编号无效"]})
        get_object_or_404(Resource, pk=resource_id)
        try:
            expected_mode=forms.ChoiceField(choices=[("AUTO","规则通过后自动确认"),("MANUAL","提交后人工审核")],required=False).clean(data.get("expected_approval_mode"))
        except forms.ValidationError as error:
            raise ApiError("VALIDATION_ERROR", "预约确认信息无效，请刷新后重新提交", fields={"expected_approval_mode": error.messages})
        form_data={key:value for key,value in data.items() if key!="expected_approval_mode"}
        values = cleaned(BookingForm(form_data))
        values["expected_approval_mode"]=expected_mode or None
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
    result["next_step"] = next_step(booking)
    event_rows = AuditEvent.objects.filter(entity_type="reservation", entity_id=booking.pk)
    events = list(event_rows.select_related("actor").order_by("-created_at", "-pk")[:100])
    result["timeline_total"] = event_rows.count()
    result["timeline_has_more"] = result["timeline_total"] > len(events)
    result["timeline"] = [{"id": e.pk, "action": e.action, "label": event_label(e.action), "at": date_text(e.created_at), "reason": e.reason, "actor": e.actor.display_name if e.actor_id else "系统"} for e in reversed(events)]
    note_rows = booking.review_notes.select_related("actor")
    notes = list(note_rows.order_by("-created_at","-pk")[:100])
    result["supplements_total"] = note_rows.count()
    result["supplements_has_more"] = result["supplements_total"] > len(notes)
    result["supplements"] = [{"id": x.pk, "kind": x.kind, "content": x.content,
        "created_at": date_text(x.created_at), "actor": x.actor.display_name}
        for x in reversed(notes)]
    return result


def _request_key(value):
    try:
        return str(forms.UUIDField().clean(value))
    except forms.ValidationError:
        raise ApiError("VALIDATION_ERROR","缺少有效的操作请求编号",fields={"request_key":["请重新打开操作表单"]}) from None


@transaction.atomic
def _mini_action(actor, request_key, action, data, execute):
    """Atomic transport receipt using an existing immutable, unique audit key.

    The actor gate serializes same-user retries, including cross-resource ones.
    Only a hash and the minimal receipt are stored, never credentials or bodies.
    Business validation/audits/notifications are still owned by the services.
    """
    current = User.objects.select_for_update(no_key=True).get(pk=actor.pk)
    s.require(current.has_role("STUDENT","TEACHER"),"账号或预约身份已失效")
    key = f"mini-action:{current.pk}:{request_key}"
    fingerprint = hashlib.sha256(json.dumps([action,data],sort_keys=True,
        ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
    receipt = AuditEvent.objects.filter(dedupe_key=key).first()
    if receipt:
        s.require(receipt.new_state.get("fingerprint")==fingerprint,"同一请求编号不能提交不同操作或内容")
        return dict(receipt.new_state["result"],replayed=True)
    result = execute()
    s.audit(current,current,"MINI_ACTION_RECEIPT",
        new={"fingerprint":fingerprint,"result":result},
        reason="小程序操作已提交，重复请求返回原回执",dedupe_key=key)
    return dict(result,replayed=False)


@endpoint("POST")
def supplement(request, pk):
    booking = get_object_or_404(my_bookings(request.mini_user),pk=pk)
    data = payload(request,{"reason","request_key"})
    values = cleaned(ActionForm(data));key = _request_key(data.get("request_key"))
    def execute():
        note = s.review_note(request.mini_user,booking.pk,values["reason"])
        return {"booking_id":booking.pk,"note_id":note.pk,"status":"PENDING"}
    return _mini_action(request.mini_user,key,"supplement",
        {"booking_id":booking.pk,"reason":values["reason"]},execute)


@endpoint("POST")
def incidents(request):
    data = payload(request,{"resource_id","reservation","kind","description","occurred_at","request_key"})
    key = _request_key(data.get("request_key"))
    resource_id = s.positive_integer(data.get("resource_id"))
    if resource_id is None:
        raise ApiError("VALIDATION_ERROR","请选择有效资源",fields={"resource_id":["资源编号无效"]})
    booking_id = data.get("reservation")
    if booking_id not in (None,"") and s.positive_integer(booking_id) is None:
        raise ApiError("VALIDATION_ERROR","关联预约编号无效",fields={"reservation":["请选择本人的预约"]})
    form_data = dict(data,resource=resource_id)
    form = IncidentForm(form_data)
    form.fields["reservation"].queryset = my_bookings(request.mini_user)
    values = cleaned(form)
    normalized = {"resource_id":values["resource"].pk,
        "reservation":values["reservation"].pk if values["reservation"] else None,
        "kind":values["kind"],"description":values["description"],
        "occurred_at":date_text(values["occurred_at"])}
    def execute():
        item = s.report_incident(request.mini_user,normalized["resource_id"],
            normalized["kind"],normalized["description"],normalized["reservation"],
            occurred_at=values["occurred_at"])
        return {"id":item.pk,"code":item.code,"status":item.status,
            "status_label":item.get_status_display(),"blocks_service":item.blocks_service,
            "resource_id":item.resource_id,"reservation_id":item.reservation_id}
    return _mini_action(request.mini_user,key,"incident",normalized,execute)


@endpoint("GET")
def receipt(request, request_key):
    """Explicit result lookup after a client timeout; absent is not proof of failure."""
    key = _request_key(request_key)
    booking=Reservation.objects.filter(applicant=request.mini_user,request_key=key).select_related("resource__lab").first()
    if booking:
        return {"found":True,"kind":"booking","result":booking_data(booking),"notice":"已找到原预约回执"}
    item = AuditEvent.objects.filter(dedupe_key=f"mini-action:{request.mini_user.pk}:{key}",
        actor=request.mini_user,action="MINI_ACTION_RECEIPT").first()
    return {"found":item is not None,"kind":"action" if item else None,"result":item.new_state["result"] if item else None,
            "notice":"未找到回执不代表提交失败；仍可使用原请求编号与原内容核对重试。" if item is None else "已找到原操作回执"}


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
    unread=qs.filter(read_at__isnull=True).count()
    if request.GET.get('unread')=='1':qs=qs.filter(read_at__isnull=True)
    def serialize(item):
        return {"id": item.pk, "kind": item.kind, "kind_label":notification_label(item.kind), "content": item.content, "created_at": date_text(item.created_at),
                "read_at": date_text(item.read_at), "booking_id": item.audit.entity_id if item.audit.entity_type == "reservation" and item.owned_booking else None}
    result = page_data(request, qs, serialize)
    result["unread"] = unread
    return result


@endpoint("POST")
def read_notification(request, pk):
    item = get_object_or_404(Notification, pk=pk, recipient=request.mini_user)
    Notification.objects.filter(pk=item.pk, read_at__isnull=True).update(read_at=timezone.now())
    return {"read": True}


@endpoint("POST")
def read_all(request):
    return {"updated": Notification.objects.filter(recipient=request.mini_user, read_at__isnull=True).update(read_at=timezone.now())}
