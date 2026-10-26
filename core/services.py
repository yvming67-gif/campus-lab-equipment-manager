import hashlib, json, uuid
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.db import transaction, IntegrityError
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from .models import *
from .request_context import correlated,correlation_id

TZ = ZoneInfo("Asia/Shanghai")
ACTIVE = ["CONFIRMED", "IN_USE"]
FUTURE = ["PENDING", "CONFIRMED"]
class BusinessError(ValueError): pass

def require(ok, message):
    if not ok: raise BusinessError(message)

def positive_integer(value, maximum=9223372036854775807):
    """Parse bounded ASCII decimal input before passing it to ORM/int fields."""
    text=str(value)
    if not text or len(text)>len(str(maximum)) or not text.isascii() or not text.isdecimal():return None
    number=int(text)
    return number if 0<number<=maximum else None

def manager(actor, resource):
    return actor.has_role("LAB_ADMIN") and LabGrant.objects.filter(user=actor, lab=resource.lab).exists()

def check_manager(actor, resource):
    require(manager(actor, resource), "无此实验室业务管理权限")

def lab_managers(resource):
    return User.objects.filter(is_active=True, roles__code="LAB_ADMIN", labgrant__lab=resource.lab).distinct()

def json_state(v):
    return json.loads(json.dumps(v, default=str, ensure_ascii=False))

def audit(actor, obj, action, old=None, new=None, reason="", at=None, dedupe_key=None):
    lab_id = getattr(obj, "lab_id", None)
    if isinstance(obj, Delivery): lab_id = obj.notification.audit.lab_id
    if isinstance(obj, Reservation): lab_id = obj.resource.lab_id
    if isinstance(obj, Incident): lab_id = obj.resource.lab_id
    if isinstance(obj, WorkOrder): lab_id = obj.incident.resource.lab_id
    return AuditEvent.objects.create(actor=actor, actor_kind="USER" if actor else "SYSTEM", entity_type=obj._meta.model_name, entity_id=obj.pk, action=action, old_state=json_state(old or {}), new_state=json_state(new or {}), reason=reason or action, request_id=correlation_id.get() or uuid.uuid4(), lab_id=lab_id, created_at=at or timezone.now(), dedupe_key=dedupe_key)

def notify(event, recipients, kind, content, at=None):
    at = at or timezone.now()
    for user in {u.pk: u for u in recipients if u and u.is_active}.values():
        item, created = Notification.objects.get_or_create(audit=event, recipient=user, kind=kind, defaults={"content": content, "created_at": at})
        if created:
            local = Delivery.objects.create(notification=item, channel="IN_APP", status="SENT", next_attempt_at=at)
            DeliveryAttempt.objects.create(delivery=local, attempt_no=0, outcome="SUCCESS", detail="站内通知已保存", attempted_at=at)
            Delivery.objects.create(notification=item, channel="MOCK_EXTERNAL" if settings.DEMO_MODE else "EXTERNAL", next_attempt_at=at)

def _locked_user_resource(user_id, resource_id):
    user = User.objects.select_for_update(no_key=True).get(pk=user_id)
    resource = Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=resource_id)
    return user, resource

def _booking_lock(pk):
    initial = Reservation.objects.only("applicant_id", "resource_id").get(pk=pk)
    user, resource = _locked_user_resource(initial.applicant_id, initial.resource_id)
    booking = Reservation.objects.select_for_update().select_related("applicant", "resource__lab").get(pk=pk)
    booking.resource = resource
    return booking, user, resource

def finish_booking(booking,actor,fields,at):
    if booking.review_required:
        old={'review_required':True,'review_reason':booking.review_reason}
        booking.review_required=False;booking.review_reason=''
        audit(actor,booking,'REVIEW_ENDED',old,{'review_required':False,'status':booking.status},'预约终止，结束当前复核标志；历史保留',at)
    booking.save(update_fields=list(set(fields+['review_required','review_reason'])))

def _version(booking, expected):
    if expected is not None:
        try: value = int(expected)
        except (ValueError, TypeError, OverflowError): raise BusinessError("版本号必须为正整数，请刷新后重试")
        require(value > 0 and booking.version == value, "记录已变化，请刷新后重试")

def overdue_for(user, at):
    equipment = Loan.objects.filter(reservation__applicant=user, return_record__isnull=True, due_at__lt=at).exists()
    room = RoomUse.objects.filter(reservation__applicant=user, ended_at__isnull=True, reservation__ends_at__lt=at).exists()
    return equipment or room

def overdue_resource(resource, at):
    if resource.kind == "EQUIPMENT":
        return Loan.objects.filter(reservation__resource=resource, return_record__isnull=True, due_at__lt=at).exists()
    return RoomUse.objects.filter(reservation__resource=resource, ended_at__isnull=True, reservation__ends_at__lt=at).exists()

def maximum_overlap(start, end, intervals):
    events = [(start, 1), (end, -1)]
    for a, b in intervals:
        a, b = max(start, a), min(end, b)
        if a < b: events.extend([(a, 1), (b, -1)])
    current = best = 0
    for _, change in sorted(events, key=lambda e: (e[0], e[1])):
        current += change; best = max(best, current)
    return best

def validate_time(resource, start, end, policy, at):
    require(timezone.is_aware(start) and timezone.is_aware(end), "时间必须具有时区")
    require(start < end and start >= at, "开始必须早于结束，且不得早于当前时间")
    require(end <= at + timedelta(days=policy.window_days), f"起止时段须位于未来{policy.window_days}天预约窗口内")
    require(end - start <= timedelta(hours=policy.max_hours), f"单次最长使用{policy.max_hours}小时")
    a, b = start.astimezone(TZ), end.astimezone(TZ)
    require(a.date() == b.date(), "日间预约不能跨日")
    require(all(x.minute % policy.slot_minutes == 0 and x.second == 0 and x.microsecond == 0 for x in [a,b]), f"起止时间必须符合{policy.slot_minutes}分钟粒度")
    from .coordination import check_calendar
    if not check_calendar(resource,start,end):
        require(OpeningPeriod.objects.filter(lab=resource.lab, weekday=a.isoweekday(), valid_from__lte=a.date(), starts_at__lte=a.time().replace(tzinfo=None), ends_at__gte=b.time().replace(tzinfo=None)).filter(Q(valid_to__isnull=True)|Q(valid_to__gte=a.date())).exists(), "目标时段超出实验室开放时间")


def validate(booking, policy, at, exclude=None, time_at=None):
    user, resource = booking.applicant, booking.resource
    require(user.is_active, "账号已停用")
    require(user.has_role("TEACHER") if resource.kind == "ROOM" else user.has_role("STUDENT", "TEACHER"), "仅教师可预约场地；设备需学生或教师身份")
    start, end = booking.starts_at, booking.ends_at
    validate_time(resource, start, end, policy, time_at if time_at is not None else at)
    require(resource.lab.is_active and resource.service_status == "NORMAL", "资源停用、维护或异常隔离，不可预约")
    from .coordination import check_dependency
    check_dependency(resource,start,end,user)
    require(not overdue_for(user, at), "存在逾期未结束使用，不能新预约或获得确认")
    require(not overdue_resource(resource, at), "资源存在超期未归还或未结束使用，暂不能承诺新预约")
    if resource.kind == "EQUIPMENT":
        qid = resource.equipment.qualification_id
        if qid:
            require(UserQualification.objects.filter(user=user, qualification_id=qid, revoked=False, issued_at__lte=at).filter(Q(expires_at__isnull=True) | Q(expires_at__gte=end)).exists(), "缺少覆盖预约时段的有效培训资格")
    else:
        require(1 <= booking.participants <= resource.room.capacity, "人数必须在场地容量范围内")
    future = Reservation.objects.filter(applicant=user, resource__kind=resource.kind, status__in=FUTURE, starts_at__gt=at)
    if exclude: future = future.exclude(pk=exclude)
    quota = policy.room_quota if resource.kind == "ROOM" else policy.equipment_quota
    # Only a candidate still in the future consumes a future quota slot.
    # Reviewing a current commitment within its original handover grace period
    # must not count it as a fourth future reservation.
    if start > at:
        require(future.count() < quota, f"未来此类预约额度已满（最多{quota}项）")
    conflict = Reservation.objects.filter(resource=resource, status__in=ACTIVE, starts_at__lt=end, ends_at__gt=start)
    personal = Reservation.objects.filter(applicant=user, resource__kind=resource.kind, status__in=ACTIVE, starts_at__lt=end, ends_at__gt=start)
    if exclude: conflict = conflict.exclude(pk=exclude); personal = personal.exclude(pk=exclude)
    require(not conflict.exists(), "该资源时段已被确认占用，存在时间冲突")
    if resource.kind == "ROOM":
        pending = Reservation.objects.filter(applicant=user, resource__kind="ROOM", status__in=FUTURE, starts_at__lt=end, ends_at__gt=start)
        if exclude: pending = pending.exclude(pk=exclude)
        require(not personal.exists() and not pending.exists(), "同一教师同一时段只能负责一间场地")
    else:
        require(maximum_overlap(start, end, personal.values_list("starts_at", "ends_at")) <= policy.simultaneous_equipment, f"同一时段最多使用{policy.simultaneous_equipment}台或套设备")

def availability(resource, start, end, at=None, applicant=None):
    at = at or timezone.now()
    try:
        validate_time(resource, start, end, Policy.objects.get(pk=1), at)
    except BusinessError as error:
        return False, str(error)
    if not resource.lab.is_active or resource.service_status != "NORMAL": return False, resource.get_service_status_display()
    if overdue_resource(resource, at): return False, "超期使用待处理"
    from .coordination import check_dependency
    try:check_dependency(resource,start,end,applicant)
    except BusinessError as error:return False,str(error)
    if Reservation.objects.filter(resource=resource, status__in=ACTIVE, starts_at__lt=end, ends_at__gt=start).exists(): return False, "时段已占用"
    return True, "时段可预约（仅资源时段；提交时另核验本人资格）"

def _fingerprint(resource, start, end, purpose, participants):
    raw = json.dumps([resource.pk, start.isoformat(), end.isoformat(), purpose, participants], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()

@correlated
@transaction.atomic
def create_reservation(actor, resource_id, starts_at, ends_at, purpose, participants=1, request_key=None, at=None):
    at = at or timezone.now();key = request_key or uuid.uuid4()
    try: key = uuid.UUID(str(key))
    except (ValueError, TypeError): raise BusinessError("请求识别号无效")
    user, resource = _locked_user_resource(actor.pk, resource_id)
    require(bool(purpose.strip()), "用途不能为空")
    fp = _fingerprint(resource, starts_at, ends_at, purpose.strip(), participants)
    original = Reservation.objects.filter(applicant=user, request_key=key).first()
    if original:
        require(original.request_fingerprint == fp, "同一请求号不能提交不同内容")
        return original
    from .coordination import resource_profile
    policy = Policy.objects.get(pk=1)
    manual = resource.requires_approval
    booking = Reservation(applicant=user, resource=resource, starts_at=starts_at, ends_at=ends_at, purpose=purpose.strip(), participants=participants, request_key=key, request_fingerprint=fp, approval_mode="MANUAL" if manual else "AUTO", status="PENDING" if manual else "CONFIRMED", policy_snapshot=dict(policy.snapshot(), resource_profile=resource_profile(resource)), created_at=at)
    validate(booking, policy, at)
    booking.save()
    event = audit(actor, booking, "SUBMIT", new={"status":booking.status, "mode":booking.approval_mode}, reason="提交资源预约", at=at)
    if manual: notify(event, list(lab_managers(resource)), "APPROVAL_REQUEST", f"预约{booking.code}待审核", at)
    else:
        ApprovalDecision.objects.create(reservation=booking, actor=None, mode="AUTO", approved=True, reason="普通设备规则检查通过，系统自动确认", decided_at=at)
        notify(event, [user], "AUTO_CONFIRM", f"预约{booking.code}已自动确认", at)
    return booking

@correlated
@transaction.atomic
def approve(actor, booking_id, approved, reason, expected=None, at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id)
    check_manager(actor, resource);require(actor.pk != user.pk, "不能审批本人申请")
    if booking.status in ["CONFIRMED", "REJECTED"] and hasattr(booking,"decision") and booking.decision.actor_id==actor.pk and booking.decision.approved==approved:
        require(reason==booking.decision.reason,"已有审批结果，本次理由不同；请追加复核记录")
        return booking
    _version(booking,expected);require(booking.status=="PENDING", "只有待审核申请可以作出最终决定")
    require(bool(reason.strip()), "审核理由不能为空")
    if approved:validate(booking,Policy.objects.get(pk=1),at,exclude=booking.pk)
    old=booking.status;booking.status="CONFIRMED" if approved else "REJECTED";booking.reason=reason;booking.review_required=False;booking.review_reason="";booking.version+=1;booking.save(update_fields=["status","reason","version","review_required","review_reason"])
    ApprovalDecision.objects.create(reservation=booking,actor=actor,mode="MANUAL",approved=approved,reason=reason,decided_at=at)
    event=audit(actor,booking,"APPROVE" if approved else "REJECT",{"status":old},{"status":booking.status},reason,at)
    notify(event,[user],"APPROVAL_RESULT",f"预约{booking.code}：{booking.get_status_display()}。{reason}",at)
    return booking

@correlated
@transaction.atomic
def review_note(actor, booking_id, content, evidence_ref="", at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id)
    require(booking.status=="PENDING", "仅待审核申请可以补充材料")
    is_owner=actor.pk==user.pk
    if not is_owner:check_manager(actor,resource);require(actor.pk!=user.pk,"不能审批本人")
    require(bool(content.strip()),"补充意见或说明不能为空")
    kind="PROVIDE_INFO" if is_owner else "REQUEST_INFO"
    note=ReviewNote.objects.create(reservation=booking,actor=actor,kind=kind,content=content,evidence_ref=evidence_ref,created_at=at)
    event=audit(actor,booking,kind,{"status":"PENDING"},{"status":"PENDING","note_id":note.pk},content,at)
    notify(event,list(lab_managers(resource)) if is_owner else [user],kind,f"预约{booking.code}有新的补充说明",at)
    return note

@correlated
@transaction.atomic
def cancel(actor, booking_id, reason, expected=None, at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id)
    require(actor.pk==user.pk,"只能取消本人预约")
    if booking.status=="CANCELLED":return booking
    _version(booking,expected);require(booking.status in FUTURE and at < booking.starts_at,"只能取消尚未开始且未使用的预约")
    require(bool(reason.strip()),"取消原因不能为空")
    old=booking.status;booking.status="CANCELLED";booking.reason=reason;booking.version+=1;finish_booking(booking,actor,["status","reason","version"],at)
    event=audit(actor,booking,"CANCEL",{"status":old},{"status":booking.status},reason,at)
    notify(event,[user]+list(lab_managers(resource)),"CANCEL",f"预约{booking.code}已取消：{reason}",at);return booking

def _check_start(booking, resource, at):
    policy=Policy.objects.get(pk=1)
    require(booking.applicant.is_active, "申请人账号已停用，不能开始使用")
    require(booking.applicant.has_role("TEACHER") if resource.kind=="ROOM" else booking.applicant.has_role("STUDENT","TEACHER"), "申请人当前角色不具备使用资格")
    require(booking.status=="CONFIRMED", "预约尚未确认或已经结束")
    require(not booking.review_required, "预约暂停交接，请先完成复核："+booking.review_reason)
    if resource.requires_approval and booking.approval_mode=="AUTO":
        from .coordination import resource_profile
        latest=booking.rechecks.filter(accepted=True).order_by('-created_at','-pk').first()
        require(latest is not None and latest.snapshot==resource_profile(resource),"资源已受限，需管理员重新复核")
    from .coordination import check_calendar,check_dependency
    check_calendar(resource,booking.starts_at,booking.ends_at)
    check_dependency(resource,booking.starts_at,booking.ends_at,booking.applicant,at=at,check_actual=True)
    deadline=booking.late_start_until or booking.starts_at+timedelta(minutes=policy.grace_minutes)
    require(booking.starts_at<=at<booking.ends_at and at<=deadline, f"未到使用时间或已超过{policy.grace_minutes}分钟领取/入场宽限")
    require(resource.service_status=="NORMAL" and resource.lab.is_active,"资源异常、维护或停用，不能开始使用")
    require(not overdue_for(booking.applicant,at),"存在逾期未结束使用，不能再次领取或入场")
    if resource.kind=="EQUIPMENT" and resource.equipment.qualification_id:
        require(UserQualification.objects.filter(user=booking.applicant,qualification_id=resource.equipment.qualification_id,revoked=False,issued_at__lte=at).filter(Q(expires_at__isnull=True)|Q(expires_at__gte=booking.ends_at)).exists(),"培训资格已失效")

def _times(arrived, started, handed, at):
    require(all(timezone.is_aware(x) for x in [arrived,started,handed]),"交接时间必须有时区")
    require(arrived<=started<=handed<=at,"到场、开始办理及实际交付时间顺序错误")

@correlated
@transaction.atomic
def checkout(actor, booking_id, school_no, tag, accessories_ok=True, condition="完好", arrived_at=None, service_started_at=None, at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id);check_manager(actor,resource)
    require(resource.kind=="EQUIPMENT","该操作只用于设备借出")
    require(school_no==user.school_no and tag==resource.code,"借用人身份或设备标签不匹配")
    if hasattr(booking,"loan"):
        original=booking.loan
        require(accessories_ok and condition==original.condition_out,"借出已记录，本次检查内容不同；请追加异常或更正说明")
        if arrived_at is not None:require(arrived_at==original.arrived_at,"到场时间与原借出凭据不一致")
        if service_started_at is not None:require(service_started_at==original.service_started_at,"办理时间与原借出凭据不一致")
        return original
    _check_start(booking,resource,at)
    require(not Loan.objects.filter(reservation__resource=resource,return_record__isnull=True).exists(),"设备仍有未归还借出")
    require(Loan.objects.filter(reservation__applicant=user,return_record__isnull=True).count()<Policy.objects.get(pk=1).simultaneous_equipment,f"同一人最多同时借{Policy.objects.get(pk=1).simultaneous_equipment}台或套设备")
    if not accessories_ok:
        _new_incident(actor,resource,"MISSING","借出核验发现缺件或异常："+condition,at,booking,confirmed=True)
        return None
    arrived_at=arrived_at or at;service_started_at=service_started_at or at;_times(arrived_at,service_started_at,at,at)
    loan=Loan.objects.create(reservation=booking,operator=actor,arrived_at=arrived_at,service_started_at=service_started_at,handed_over_at=at,due_at=booking.ends_at,tag_snapshot=resource.code,condition_out=condition)
    HandoverItem.objects.bulk_create([HandoverItem(loan=loan,accessory=a,code_snapshot=a.code,name_snapshot=a.name,expected_qty=a.quantity,out_qty=a.quantity,out_condition="核对完好") for a in resource.accessories.filter(is_active=True)])
    EquipmentDetail.objects.filter(resource=resource).update(physical_status="BORROWED")
    booking.status="IN_USE";booking.version+=1;booking.save(update_fields=["status","version"])
    event=audit(actor,booking,"CHECKOUT",{"status":"CONFIRMED","physical":"IN_STOCK"},{"status":"IN_USE","physical":"BORROWED","loan":loan.code},condition,at)
    notify(event,[user],"CHECKOUT",f"设备已借出，凭据{loan.code}",at);return loan

@correlated
@transaction.atomic
def return_equipment(actor, booking_id, tag, note, damaged=False, missing=False, fault=False, return_quantities=None, returned_at=None, at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id);check_manager(actor,resource)
    require(resource.kind=="EQUIPMENT" and hasattr(booking,"loan"),"无对应设备借出记录，请登记待核对事项")
    loan=booking.loan
    if hasattr(loan,"return_record"):
        original=loan.return_record
        effective_missing=bool(missing) or (return_quantities is not None and any(return_quantities.get(i.pk,i.out_qty)<i.out_qty for i in loan.items.all()))
        require(tag==loan.tag_snapshot and note==original.note and bool(damaged)==original.damaged and effective_missing==original.missing and bool(fault)==original.fault,"归还已记录，本次内容不同；请追加异常或异议，不可覆盖原事实")
        if returned_at is not None:require(returned_at==original.returned_at,"归还时间与原凭据不同")
        if return_quantities is not None:
            require(all(return_quantities.get(i.pk,i.out_qty)==i.return_qty for i in loan.items.all()),"配件数量与原归还凭据不同")
        return original
    require(tag==loan.tag_snapshot,"设备标签不匹配，暂停归还核对")
    returned_at=returned_at or at;require(loan.handed_over_at<=returned_at<=at,"实际接收时间不合法")
    require(bool(note.strip()),"归还检查说明不能为空")
    record=ReturnRecord.objects.create(loan=loan,operator=actor,returned_at=returned_at,damaged=damaged,missing=missing,fault=fault,note=note)
    for item in loan.items.all():
        qty=(return_quantities or {}).get(item.pk,item.out_qty)
        require(0<=qty<=item.out_qty,"归还配件数量不合法")
        item.return_qty=qty;item.return_condition="缺件" if qty<item.out_qty else "已核对";item.save(update_fields=["return_qty","return_condition"])
        if qty<item.out_qty:missing=True
    if missing and not record.missing:record.missing=True;record.save(update_fields=["missing"])
    EquipmentDetail.objects.filter(resource=resource).update(physical_status="IN_STOCK")
    booking.status="COMPLETED";booking.version+=1;booking.save(update_fields=["status","version"])
    event=audit(actor,booking,"RETURN",{"status":"IN_USE"},{"status":"COMPLETED","return":record.code,"overdue_seconds":record.overdue_seconds},note,at)
    for yes,kind in [(damaged,"DAMAGE"),(missing,"MISSING"),(fault,"FAULT")]:
        if yes:_new_incident(actor,resource,kind,note,at,booking,confirmed=True)
    _refresh_service(resource,actor,"归还后复核服务限制",at)
    notify(event,[user],"RETURN",f"归还已登记，凭据{record.code}；逾期{record.overdue_seconds//60}分钟",at);return record

@correlated
@transaction.atomic
def start_room(actor, booking_id, school_no, participants, note, arrived_at=None, at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id);check_manager(actor,resource)
    require(resource.kind=="ROOM","该操作只用于场地入场")
    require(school_no==user.school_no,"场地负责人身份不匹配")
    if hasattr(booking,"room_use"):
        original=booking.room_use
        require(participants==original.actual_participants and note==original.start_note,"入场已登记，本次内容不同，请追加说明")
        if arrived_at is not None:require(arrived_at==original.arrived_at,"到场时间与原凭据不同")
        return original
    _check_start(booking,resource,at)
    require(1<=participants<=resource.room.capacity,"实际人数超过场地容量或无效")
    require(not RoomUse.objects.filter(reservation__resource=resource,ended_at__isnull=True).exists(),"场地上次使用尚未结束")
    require(not RoomUse.objects.filter(reservation__applicant=user,ended_at__isnull=True).exists(),"同一教师不能同时负责两间正在使用的场地")
    arrived_at=arrived_at or at;require(arrived_at<=at,"到场时间不能在未来")
    use=RoomUse.objects.create(reservation=booking,started_by=actor,arrived_at=arrived_at,started_at=at,actual_participants=participants,start_note=note)
    booking.status="IN_USE";booking.version+=1;booking.save(update_fields=["status","version"])
    event=audit(actor,booking,"ROOM_START",{"status":"CONFIRMED"},{"status":"IN_USE","participants":participants},note,at)
    notify(event,[user],"ROOM_START",f"场地{resource.name}已登记开始使用",at);return use

@correlated
@transaction.atomic
def end_room(actor, booking_id, note, abnormal=False, ended_at=None, at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id);check_manager(actor,resource)
    require(resource.kind=="ROOM" and hasattr(booking,"room_use"),"无对应场地开始记录")
    use=booking.room_use
    if use.ended_at:
        require(note==use.end_note and bool(abnormal)==use.abnormal,"离场已登记，本次内容不同，请追加说明")
        if ended_at is not None:require(ended_at==use.ended_at,"结束时间与原凭据不同")
        return use
    ended_at=ended_at or at;require(use.started_at<=ended_at<=at,"实际结束时间不合法");require(bool(note.strip()),"场地检查说明不能为空")
    use.ended_at=ended_at;use.ended_by=actor;use.end_note=note;use.abnormal=abnormal;use.save(update_fields=["ended_at","ended_by","end_note","abnormal"])
    booking.status="COMPLETED";booking.version+=1;booking.save(update_fields=["status","version"])
    event=audit(actor,booking,"ROOM_END",{"status":"IN_USE"},{"status":"COMPLETED","abnormal":abnormal},note,at)
    if abnormal:_new_incident(actor,resource,"FAULT",note,at,booking,confirmed=True)
    _refresh_service(resource,actor,"场地结束后复核限制",at)
    notify(event,[user],"ROOM_END",f"场地{resource.name}使用结束已登记",at);return use


def _refresh_service(resource, actor, reason, at):
    if resource.manual_hold: desired=resource.manual_hold
    elif WorkOrder.objects.filter(incident__resource=resource).exclude(status="CLOSED").exists():desired="MAINTENANCE"
    elif resource.incidents.filter(status__in=["PENDING","CONFIRMED"],blocks_service=True).exists():desired="ISOLATED"
    else:desired="NORMAL"
    previous=resource.service_periods.filter(ends_at__isnull=True).first()
    if resource.service_status==desired and previous:return
    old=resource.service_status;resource.service_status=desired;resource.updated_at=at;resource.save(update_fields=["service_status","updated_at"])
    event=audit(actor,resource,"SERVICE_STATUS",{"service_status":old},{"service_status":desired},reason,at)
    if previous and previous.starts_at>=at:
        previous.status=desired;previous.audit=event;previous.save(update_fields=["status","audit"])
    else:
        if previous:previous.ends_at=at;previous.save(update_fields=["ends_at"])
        ServicePeriod.objects.create(resource=resource,audit=event,status=desired,starts_at=at if previous else resource.created_at)

def _new_incident(actor,resource,kind,description,at,booking=None,confirmed=False):
    if booking:require(booking.resource_id==resource.pk,"异常关联的预约与资源不匹配")
    incident=Incident.objects.create(resource=resource,reporter=actor,reservation=booking,kind=kind,description=description,occurred_at=at,reported_at=at,status="CONFIRMED" if confirmed else "PENDING",blocks_service=confirmed or manager(actor,resource) or bool(booking and booking.status=="IN_USE"),confirmed_by=actor if confirmed else None)
    event=audit(actor,incident,"REPORT_INCIDENT",new={"status":incident.status},reason=description,at=at)
    _refresh_service(resource,actor,"报障按关联使用和授权分级处理",at)
    if incident.blocks_service:
        from .coordination import flag_affected
        flag_affected(Reservation.objects.filter(resource=resource,status__in=FUTURE),actor,'资源报障待核实：'+description,at)
        for current in Reservation.objects.filter(resource=resource,status="IN_USE"):
            ev=audit(actor,current,"SAFETY_CONTACT",{"status":"IN_USE"},{"status":"IN_USE"},description,at)
            notify(ev,[current.applicant]+list(lab_managers(resource)),"RECALL","资源出现异常，请立即联系管理员；实际使用事实保留。",at)
    notify(event,list(lab_managers(resource)),"INCIDENT",f"资源{resource.name}异常{incident.code}待处理",at)
    return incident

@correlated
@transaction.atomic
def report_incident(actor,resource_id,kind,description,booking_id=None,at=None,occurred_at=None):
    at=at or timezone.now();resource=Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=resource_id)
    require(actor.is_active and actor.has_role("STUDENT","TEACHER","LAB_ADMIN"),"无异常说明权限")
    require(kind in ["DAMAGE","MISSING","FAULT"] and bool(description.strip()),"异常类型或现象不能为空")
    booking=Reservation.objects.get(pk=booking_id) if booking_id else None
    if booking:require(booking.resource_id==resource.pk and (booking.applicant_id==actor.pk or manager(actor,resource)),"不能关联他人预约或其他资源")
    item=_new_incident(actor,resource,kind,description,at,booking,confirmed=False)
    if occurred_at:
        require(occurred_at<=at,"发现时间不得晚于当前时间")
        item.occurred_at=occurred_at;item.save(update_fields=["occurred_at"])
    return item

@correlated
@transaction.atomic
def verify_incident(actor,incident_id,action,conclusion,at=None):
    at=at or timezone.now();original=Incident.objects.get(pk=incident_id);resource=Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=original.resource_id);check_manager(actor,resource)
    item=Incident.objects.select_for_update().get(pk=incident_id)
    require(bool(conclusion.strip()),"核实或处置理由不能为空")
    action=action.upper()
    require(action in ["CONFIRM","DISMISS","RESOLVE"],"核实动作无效")
    require(item.status not in ["DISMISSED","RESOLVED"],"异常已经结案，补充情况请另行追加")
    if action=="RESOLVE":require(not item.work_orders.exclude(status="CLOSED").exists(),"仍有开放工单，不能直接结案")
    old=item.status;item.status={"CONFIRM":"CONFIRMED","DISMISS":"DISMISSED","RESOLVE":"RESOLVED"}[action]
    if action=="DISMISS":require(not item.work_orders.exclude(status="CLOSED").exists(),"有开放工单，不能直接排除异常")
    item.confirmed_by=actor;item.conclusion=conclusion;item.blocks_service=action=="CONFIRM"
    if action!="CONFIRM":item.resolved_at=at
    item.save(update_fields=["status","confirmed_by","conclusion","resolved_at","blocks_service"])
    event=audit(actor,item,"VERIFY_INCIDENT",{"status":old},{"status":item.status},conclusion,at)
    _refresh_service(resource,actor,conclusion,at)
    from .coordination import flag_affected
    if action=="CONFIRM":flag_affected(Reservation.objects.filter(resource=resource,status__in=FUTURE),actor,'已核实资源异常：'+conclusion,at)
    else:
        for b in Reservation.objects.filter(resource=resource,status__in=FUTURE,review_required=True):
            notify(event,[b.applicant]+list(lab_managers(resource)),"ARRANGEMENT_IMPACT","资源异常已处置，请管理员复核后恢复交接，不自动覆盖原安排。",at)
    notify(event,[item.reporter],"INCIDENT_RESULT",f"异常{item.code}：{item.get_status_display()}。{conclusion}",at);return item

@correlated
@transaction.atomic
def create_work_order(actor,incident_id,technician_id,reason,at=None):
    at=at or timezone.now();initial=Incident.objects.select_related("resource").get(pk=incident_id);resource=Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=initial.resource_id);check_manager(actor,resource)
    item=Incident.objects.select_for_update().get(pk=incident_id);require(item.status=="CONFIRMED","异常需先由管理员确认")
    tech=User.objects.get(pk=technician_id);require(tech.has_role("TECHNICIAN"),"被指派人不是有效维修人员")
    require(bool(reason.strip()),"工单原因不能为空")
    original=item.work_orders.exclude(status="CLOSED").first()
    if original:
        require(original.assigned_to_id==tech.pk,"工单已存在，变更维修人员请使用重新指派")
        return original
    work=WorkOrder.objects.create(incident=item,created_by=actor,assigned_to=tech,created_at=at)
    WorkOrderEvent.objects.create(work_order=work,actor=actor,old_status="",new_status="OPEN",note=reason,created_at=at)
    event=audit(actor,work,"CREATE_WORK",new={"status":"OPEN","technician":tech.school_no},reason=reason,at=at)
    _refresh_service(resource,actor,"创建维修工单",at)
    for booking in Reservation.objects.select_for_update().filter(resource=resource,status__in=FUTURE):
        old=booking.status;booking.status="CANCELLED";booking.reason=f"维护影响：{work.code}";booking.version+=1;finish_booking(booking,actor,["status","reason","version"],at)
        change=audit(actor,booking,"MAINTENANCE_CANCEL",{"status":old},{"status":"CANCELLED"},booking.reason,at)
        notify(change,[booking.applicant],"MAINTENANCE_IMPACT",f"预约{booking.code}因维护取消",at)
    for booking in Reservation.objects.filter(resource=resource,status="IN_USE"):
        change=audit(actor,booking,"RECALL",{"status":"IN_USE"},{"status":"IN_USE"},f"工单{work.code}要求联系使用人召回/结束；不伪记归还",at)
        notify(change,[booking.applicant],"RECALL",f"资源出现异常，请联系管理员处理。工单{work.code}",at)
    notify(event,[tech,item.reporter],"WORK_CREATED",f"工单{work.code}已创建并指派",at);return work

@correlated
@transaction.atomic
def update_work(actor,work_id,action,result,at=None):
    at=at or timezone.now();initial=WorkOrder.objects.select_related("incident").get(pk=work_id)
    resource=Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=initial.incident.resource_id)
    work=WorkOrder.objects.select_for_update().select_related("incident").get(pk=work_id)
    require(actor.has_role("TECHNICIAN") and work.assigned_to_id==actor.pk,"只能处理本人被指派的维修任务")
    require(bool(result.strip()),"维修进展或结果不能为空")
    require(action in ["START","PROGRESS","SUBMIT"],"维修动作无效")
    if action=="START":require(work.status=="OPEN","只有待处理工单可以接单");desired="IN_PROGRESS"
    elif action=="PROGRESS":require(work.status=="IN_PROGRESS","工单不在处理中");desired="IN_PROGRESS"
    else:require(work.status=="IN_PROGRESS","工单不在处理中");desired="AWAITING_ACCEPTANCE"
    old=work.status;work.status=desired;work.result=result
    if action=="SUBMIT":work.finished_at=at
    work.save(update_fields=["status","result","finished_at"])
    WorkOrderEvent.objects.create(work_order=work,actor=actor,old_status=old,new_status=desired,note=result,created_at=at)
    event=audit(actor,work,action,{"status":old},{"status":desired},result,at)
    notify(event,list(lab_managers(resource)),"WORK_PROGRESS",f"工单{work.code}：{work.get_status_display()}",at);return work

@correlated
@transaction.atomic
def accept_work(actor,work_id,passed,reason,at=None):
    at=at or timezone.now();initial=WorkOrder.objects.select_related("incident").get(pk=work_id);resource=Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=initial.incident.resource_id);check_manager(actor,resource)
    work=WorkOrder.objects.select_for_update().select_related("incident").get(pk=work_id)
    require(actor.pk!=work.assigned_to_id,"维修人员不能验收自己的维修结果")
    performed_repair = AuditEvent.objects.filter(
        entity_type=work._meta.model_name, entity_id=work.pk, actor=actor,
        action__in=["START", "PROGRESS", "SUBMIT"],
    ).exists()
    require(not performed_repair,"曾参与本工单维修的人员不能验收自己的维修结果")
    require(work.status=="AWAITING_ACCEPTANCE","工单尚未提交验收")
    require(bool(reason.strip()),"验收检查及理由不能为空")
    old=work.status;work.status="CLOSED" if passed else "IN_PROGRESS"
    if passed:work.accepted_by=actor;work.accepted_at=at
    work.save(update_fields=["status","accepted_by","accepted_at"])
    WorkOrderEvent.objects.create(work_order=work,actor=actor,old_status=old,new_status=work.status,note=reason,created_at=at)
    event=audit(actor,work,"ACCEPT" if passed else "REJECT_ACCEPTANCE",{"status":old},{"status":work.status},reason,at)
    if passed and not work.incident.work_orders.exclude(status="CLOSED").exists():
        item=work.incident;old_block=item.blocks_service;item.status="RESOLVED";item.blocks_service=False;item.resolved_at=at;item.conclusion=reason;item.save(update_fields=["status","blocks_service","resolved_at","conclusion"])
        audit(actor,item,"INCIDENT_RESOLVED",{"status":"CONFIRMED","blocks_service":old_block},{"status":"RESOLVED","blocks_service":False},reason,at)
    _refresh_service(resource,actor,"维修验收后检查全部限制",at)
    notify(event,[work.assigned_to,work.incident.reporter],"ACCEPTANCE",f"工单{work.code}：{work.get_status_display()}；资源{resource.get_service_status_display()}",at);return work

@correlated
@transaction.atomic
def expire_booking(booking_id,at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id);p=Policy.objects.get(pk=1)
    expired=(booking.status=="PENDING" and at>=booking.starts_at) or (booking.status=="CONFIRMED" and (at>=booking.ends_at or at>(booking.late_start_until or booking.starts_at+timedelta(minutes=p.grace_minutes))))
    if not expired:return False
    old=booking.status;booking.status="EXPIRED";booking.reason=f"到开始仍未批准或超过{p.grace_minutes}分钟未领取/入场";booking.version+=1;finish_booking(booking,None,["status","reason","version"],at)
    event=audit(None,booking,"EXPIRE",{"status":old},{"status":"EXPIRED"},booking.reason,at)
    notify(event,[user]+list(lab_managers(resource)),"EXPIRE",f"预约{booking.code}已失效",at);return True

@correlated
@transaction.atomic
def reminder(booking_id,kind,at=None):
    at=at or timezone.now();booking,user,resource=_booking_lock(booking_id)
    if booking.status!="IN_USE":return False
    due=booking.ends_at
    if kind=="DUE_SOON":
        if not due-timedelta(hours=1)<=at<=due:return False
        key=f"due:{booking.pk}:{due.isoformat()}"
    else:
        if at<=due:return False
        key=f"overdue:{booking.pk}:{at.astimezone(TZ).date().isoformat()}"
    if AuditEvent.objects.filter(dedupe_key=key).exists():return False
    event=audit(None,booking,kind,{"status":"IN_USE"},{"status":"IN_USE"},"应归还前1小时提醒" if kind=="DUE_SOON" else "每日逾期提醒",at,key)
    notify(event,[user]+(list(lab_managers(resource)) if kind=="OVERDUE" else []),kind,f"预约{booking.code}："+("即将到期" if kind=="DUE_SOON" else "使用已逾期，请联系管理员结束"),at);return True

@correlated
def process_delivery(delivery_id,at=None):
    at=at or timezone.now()
    with transaction.atomic():
        task=Delivery.objects.select_for_update(skip_locked=True).filter(pk=delivery_id).first()
        if not task or task.status in ["SENT","FAILED"] or task.next_attempt_at>at or (task.status=="PROCESSING" and task.locked_until and task.locked_until>at):return False
        lease_until=at+timedelta(seconds=30)
        task.status="PROCESSING";task.locked_until=lease_until;task.save(update_fields=["status","locked_until"])
    detail="";fail=False
    if task.channel=="MOCK_EXTERNAL" and settings.DEMO_MODE:
        fail=Policy.objects.get(pk=1).mock_delivery_failure and not MockReceipt.objects.filter(delivery_id=delivery_id).exists()
        if not fail:MockReceipt.objects.get_or_create(delivery_id=delivery_id,defaults={"received_at":at})
        detail="模拟服务暂不可用" if fail else "模拟提供方按稳定投递键接收"
    elif task.channel=="EXTERNAL" and not settings.DEMO_MODE:
        try:
            from .adapters import external_delivery
            receipt=external_delivery(task)
            # Do not persist provider payloads, addresses or credentials.
            detail="真实渠道接受投递；回执摘要="+hashlib.sha256(str(receipt['receipt_id']).encode()).hexdigest()[:16]
        except Exception as error:
            fail=True;detail="真实渠道未确认接受："+type(error).__name__
    else:
        fail=True;detail="渠道与演示/正式模式不匹配，安全拒绝"
    with transaction.atomic():
        task=Delivery.objects.select_for_update().get(pk=delivery_id)
        # A legitimate takeover happens at/after the previous deadline, so its
        # deadline is strictly later. Match the exact claimed deadline as well
        # as state: a stale provider callback cannot finish another worker's
        # lease, even when that worker is also currently PROCESSING.
        if task.status!="PROCESSING" or task.locked_until!=lease_until:return False
        attempt=task.attempts.count()
        if attempt>3:return False
        DeliveryAttempt.objects.create(delivery=task,attempt_no=attempt,outcome="ERROR" if fail else "SUCCESS",detail=detail,attempted_at=at)
        task.retry_count=max(0,attempt);task.locked_until=None
        if not fail:task.status="SENT";task.last_error=""
        elif attempt==3:task.status="FAILED";task.last_error="已耗尽3次重试，需人工联系并追加联系记录"
        else:task.status="RETRY";task.next_attempt_at=at+timedelta(minutes=[1,5,15][attempt]);task.last_error=detail
        task.save(update_fields=["status","retry_count","locked_until","next_attempt_at","last_error"])
    return True

def run_jobs(at=None):
    at=at or timezone.now();counts={"expired":0,"reminders":0,"deliveries":0}
    for pk in list(Reservation.objects.filter(status__in=FUTURE,starts_at__lte=at).values_list("pk",flat=True)):
        if expire_booking(pk,at):counts["expired"]+=1
    for pk in list(Reservation.objects.filter(status="IN_USE").values_list("pk",flat=True)):
        for kind in ["DUE_SOON","OVERDUE"]:
            if reminder(pk,kind,at):counts["reminders"]+=1
    ids=list(Delivery.objects.filter(channel__in=["MOCK_EXTERNAL","EXTERNAL"],status__in=["PENDING","RETRY","PROCESSING"],next_attempt_at__lte=at).values_list("pk",flat=True)[:200])
    for pk in ids:
        if process_delivery(pk,at):counts["deliveries"]+=1
    from .operations import escalations
    counts["escalations"]=escalations(at)
    JobHeartbeat.objects.update_or_create(name="business-jobs",defaults={"last_success":at,"error_code":""})
    return counts

@correlated
@transaction.atomic
def save_policy(actor,data,reason,at=None):
    require(actor.has_role("SYS_ADMIN"),"仅系统管理员可维护参数")
    at=at or timezone.now();policy=Policy.objects.select_for_update().get(pk=1);require(bool(reason.strip()),"参数变更依据不能为空")
    old=policy.snapshot()
    for field in ["window_days","slot_minutes","max_hours","equipment_quota","room_quota","simultaneous_equipment","grace_minutes"]:
        value=int(data.get(field,getattr(policy,field)));require(value>0 and value<=365,"参数需为合法正整数")
        if field=="slot_minutes":require(value in [15,30,60],"时间粒度仅支持15、30或60分钟")
        setattr(policy,field,value)
    policy.mock_delivery_failure=bool(data.get("mock_delivery_failure",False));policy.version+=1;policy.updated_at=at;policy.save()
    PolicyRevision.objects.create(policy=policy,version=policy.version,data=policy.snapshot(),reason=reason,actor=actor,created_at=at)
    audit(actor,policy,"POLICY_CHANGE",old,policy.snapshot(),reason,at);return policy

@correlated
@transaction.atomic
def set_resource_hold(actor,resource_id,hold,reason,at=None):
    at=at or timezone.now();resource=Resource.objects.select_for_update(no_key=True).select_related("lab").get(pk=resource_id);check_manager(actor,resource)
    require(hold in ["","DISABLED","SCRAPPED"] and bool(reason.strip()),"资源限制或依据无效")
    old=resource.manual_hold;resource.manual_hold=hold;resource.save(update_fields=["manual_hold"]);audit(actor,resource,"MANUAL_HOLD",{"hold":old},{"hold":hold},reason,at)
    _refresh_service(resource,actor,reason,at)
    if hold:
        for b in Reservation.objects.select_for_update().filter(resource=resource,status__in=FUTURE):
            old=b.status;b.status="CANCELLED";b.reason=reason;b.version+=1;finish_booking(b,actor,["status","reason","version"],at)
            ev=audit(actor,b,"RESOURCE_HOLD_CANCEL",{"status":old},{"status":"CANCELLED"},reason,at);notify(ev,[b.applicant],"MAINTENANCE_IMPACT",reason,at)
    return resource

def resource_snapshot(resource):
    state={"name":resource.name,"description":resource.description,"lab_id":resource.lab_id,"kind":resource.kind,"code":resource.code}
    if resource.kind=="EQUIPMENT":
        state["equipment"]={"category":resource.equipment.category,"risk":resource.equipment.risk,"qualification_id":resource.equipment.qualification_id,"portable":resource.equipment.portable,"access_room_id":resource.equipment.access_room_id,"handover_location":resource.equipment.handover_location}
        state["accessories"]=list(resource.accessories.order_by("code").values("code","name","quantity","is_active"))
    else:
        state["room"]={"capacity":resource.room.capacity,"safety_note":resource.room.safety_note}
    return state

@correlated
@transaction.atomic
def save_resource(actor, data, resource_id=None, at=None):
    at=at or timezone.now(); lab=data["lab"]
    require(actor.has_role("LAB_ADMIN") and LabGrant.objects.filter(user=actor,lab=lab).exists(),"无此实验室资源维护权限")
    require(bool(data["reason"].strip()),"必须说明变更依据")
    parsed=[]
    for line in data.get("accessories","").splitlines():
        if not line.strip():continue
        cells=[x.strip() for x in line.split(",")]
        require(len(cells)==3,"配件须按 编码,名称,正整数数量 填写")
        quantity=positive_integer(cells[2],2147483647)
        require(quantity is not None,"配件数量须为1至2147483647的十进制正整数")
        require(len(cells[0])<=32 and len(cells[1])<=80,"配件编号或名称过长");parsed.append(cells)
    require(len({x[0] for x in parsed})==len(parsed),"配件编码重复")
    if resource_id:
        r=Resource.objects.select_for_update(no_key=True).get(pk=resource_id);check_manager(actor,r)
        require(r.kind==data["kind"] and r.code==data["code"] and r.lab_id==lab.pk,"已有资源的编号、类型和所属实验室不可变更；请新建资源")
        old=resource_snapshot(r)
        if r.kind=="ROOM":require(not Reservation.objects.filter(resource=r,status__in=ACTIVE,participants__gt=data.get("capacity",0)).exists(),"容量小于已确认预约人数，请先协调预约")
        r.name=data["name"];r.description=data["description"];r.updated_at=at;r.save(update_fields=["name","description","updated_at"])
    else:
        require(not Resource.objects.filter(code=data["code"]).exists(),"资源编号已存在")
        r=Resource.objects.create(code=data["code"],name=data["name"],kind=data["kind"],lab=lab,description=data["description"],created_at=at,updated_at=at);old={}
    if r.kind=="EQUIPMENT":
        require(bool(data.get("category")),"设备类别必填")
        if not data.get("portable",True):
            room=data.get("access_room")
            require(room and room.kind=="ROOM" and room.lab_id==r.lab_id,"固定设备须关联同实验室场地")
        EquipmentDetail.objects.update_or_create(resource=r,defaults={"category":data["category"],"risk":data["risk"],"qualification":data.get("qualification"),"portable":data.get("portable",True),"access_room":data.get("access_room"),"handover_location":data.get("handover_location","")})
        r.accessories.update(is_active=False)
        for code,name,quantity in parsed:Accessory.objects.update_or_create(resource=r,code=code,defaults={"name":name,"quantity":int(quantity),"is_active":True})
    else:
        require(bool(data.get("capacity")),"场地容量必填");RoomDetail.objects.update_or_create(resource=r,defaults={"capacity":data["capacity"]})
    ev=audit(actor,r,"RESOURCE_EDIT" if resource_id else "RESOURCE_CREATE",old,resource_snapshot(Resource.objects.get(pk=r.pk)),data["reason"],at)
    if not resource_id:ServicePeriod.objects.create(resource=r,audit=ev,status="NORMAL",starts_at=at)
    elif r.kind=="EQUIPMENT":
        new=resource_snapshot(Resource.objects.get(pk=r.pk))
        keys=["risk","qualification_id","portable","access_room_id"]
        if any(old["equipment"].get(k)!=new["equipment"].get(k) for k in keys):
            from .coordination import flag_affected
            flag_affected(Reservation.objects.filter(resource=r,status__in=FUTURE),actor,'设备分级、资格或取放依赖发生变化',at)
    return r

@correlated
@transaction.atomic
def grant_user(actor,data,at=None):
    require(actor.has_role("SYS_ADMIN"),"仅系统管理员可维护授权");at=at or timezone.now()
    user=User.objects.select_for_update(no_key=True).get(pk=data["user"].pk)
    require(bool(data["reason"].strip()),"授权依据必填")
    require(user.pk!=actor.pk or (data["active"] and any(r.code=="SYS_ADMIN" for r in data["roles"])),"不得撤销自己的系统管理权限")
    old={"qualifications":list(UserQualification.objects.filter(user=user).values("qualification_id","issued_at","expires_at","revoked")),"roles":list(user.roles.values_list("code",flat=True)),"labs":list(LabGrant.objects.filter(user=user).values_list("lab_id",flat=True)),"active":user.is_active}
    UserRole.objects.filter(user=user).delete()
    UserRole.objects.bulk_create([UserRole(user=user,role=r,granted_by=actor,granted_at=at) for r in data["roles"]])
    LabGrant.objects.filter(user=user).delete();LabGrant.objects.bulk_create([LabGrant(user=user,lab=lab,granted_at=at) for lab in data["labs"]])
    q=data.get("qualification")
    if q:
        require(data["revoke"] or (data.get("expires_at") and data["expires_at"]>at),"有效培训资格必须填写未来到期时间")
        UserQualification.objects.update_or_create(user=user,qualification=q,defaults={"expires_at":data.get("expires_at"),"revoked":data["revoke"],"granted_by":actor,"issued_at":at})
    user.is_active=data["active"];user.save(update_fields=["is_active"])
    audit(actor,user,"AUTHORIZATION_CHANGE",old,{"roles":[r.code for r in data["roles"]],"labs":[l.pk for l in data["labs"]],"active":user.is_active,"qualification":str(q or ""),"revoked":data.get("revoke"),"expires_at":data.get("expires_at")},data["reason"],at)
    from .coordination import flag_affected
    affected=[]
    for b in Reservation.objects.filter(applicant=user,status__in=FUTURE).select_related("resource__equipment"):
        ok=user.has_role("TEACHER") if b.resource.kind=="ROOM" else user.has_role("STUDENT","TEACHER")
        if ok and b.resource.kind=="EQUIPMENT" and b.resource.equipment.qualification_id:
            ok=UserQualification.objects.filter(user=user,qualification_id=b.resource.equipment.qualification_id,revoked=False,issued_at__lte=at).filter(Q(expires_at__isnull=True)|Q(expires_at__gte=b.ends_at)).exists()
        if not ok:affected.append(b.pk)
    flag_affected(Reservation.objects.filter(pk__in=affected),actor,'账号、角色或培训资格变更：'+data["reason"],at)
    return user

@correlated
@transaction.atomic
def change_opening(actor,data,at=None):
    require(actor.has_role("SYS_ADMIN"),"仅系统管理员维护开放时间");at=at or timezone.now()
    lab=Lab.objects.select_for_update().get(pk=data["lab"].pk);day=data["weekday"];effective=data["effective"]
    require(effective>at.astimezone(TZ).date(),"开放时间变更须从明天或以后生效，保留历史统计口径")
    require(data["starts_at"]<data["ends_at"],"关闭时间必须晚于开放时间")
    require(not OpeningPeriod.objects.filter(lab=lab,weekday=day,valid_from__gte=effective).exists(),"该日或之后已有开放日历版本，请选择更晚日期")
    # 不隐式取消已承诺的预约；变更前要求人工协调冲突。
    for b in Reservation.objects.filter(resource__lab=lab,status__in=ACTIVE,ends_at__date__gte=effective):
        a,z=b.starts_at.astimezone(TZ),b.ends_at.astimezone(TZ)
        if a.isoweekday()==day:require(data["starts_at"]<=a.time().replace(tzinfo=None) and z.time().replace(tzinfo=None)<=data["ends_at"],"变更会影响已确认预约，请先协调处理")
    old=list(OpeningPeriod.objects.filter(lab=lab,weekday=day,valid_to__isnull=True).values("starts_at","ends_at","valid_from"))
    OpeningPeriod.objects.filter(lab=lab,weekday=day,valid_to__isnull=True).update(valid_to=effective-timedelta(days=1))
    obj=OpeningPeriod.objects.create(lab=lab,weekday=day,starts_at=data["starts_at"],ends_at=data["ends_at"],valid_from=effective)
    ev=audit(actor,lab,"OPENING_CHANGE",{"periods":old},{"weekday":day,"from":effective,"starts_at":data["starts_at"],"ends_at":data["ends_at"]},data["reason"],at)
    for uid in Reservation.objects.filter(resource__lab=lab,status="PENDING",starts_at__date__gte=effective).values_list("applicant_id",flat=True).distinct():notify(ev,[User.objects.get(pk=uid)],"POLICY_IMPACT","开放时间将调整，待审核预约会按新日历复核",at)
    return obj

def incident_access(actor,item):
    return actor.pk==item.reporter_id or (item.reservation_id and item.reservation.applicant_id==actor.pk) or manager(actor,item.resource) or (actor.has_role("TECHNICIAN") and item.work_orders.filter(assigned_to=actor).exists())

@correlated
@transaction.atomic
def upload_evidence(actor,incident_id,upload):
    import os
    from django.core.files.base import ContentFile
    item=Incident.objects.select_related("resource__lab").get(pk=incident_id)
    require(incident_access(actor,item),"无权访问该异常证据")
    require(upload.size<=5*1024*1024,"附件最大5MB")
    blob=upload.read();suffix=os.path.splitext(upload.name)[1].lower()
    valid=(suffix==".png" and blob.startswith(b"\x89PNG\r\n\x1a\n")) or (suffix in [".jpg",".jpeg"] and blob.startswith(b"\xff\xd8\xff")) or (suffix==".pdf" and blob.startswith(b"%PDF-"))
    require(valid,"附件格式不合法，仅支持具有正确文件头的 PNG、JPEG、PDF")
    from .evidence_validation import readable
    require(readable(blob,suffix),"附件结构损坏、加密或超出页数/像素限制，无法作为可读证据保存")
    ev=Evidence(incident=item,uploaded_by=actor,original_name=os.path.basename(upload.name)[:160],sha256=hashlib.sha256(blob).hexdigest())
    ev.file.save(uuid.uuid4().hex+suffix,ContentFile(blob),save=False)
    try:ev.save();audit(actor,item,"EVIDENCE_UPLOAD",new={"evidence_id":ev.pk,"sha256":ev.sha256},reason="追加异常证据")
    except Exception:ev.file.delete(save=False);raise
    return ev

@correlated
@transaction.atomic
def record_manual_contact(actor,delivery_id,reason):
    task=Delivery.objects.select_for_update().select_related("notification__audit").get(pk=delivery_id)
    lab_id=task.notification.audit.lab_id
    require(actor.has_role("SYS_ADMIN") or (actor.has_role("LAB_ADMIN") and lab_id and LabGrant.objects.filter(user=actor,lab_id=lab_id).exists()),"无此通知任务处理权限")
    require(task.status=="FAILED" and bool(reason.strip()),"仅失败任务可追加人工联系结果")
    event=audit(actor,task,"MANUAL_CONTACT",{"status":task.status},{"status":task.status,"contact":reason},reason)
    return event
