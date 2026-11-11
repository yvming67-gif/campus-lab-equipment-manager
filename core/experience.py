"""Read-only presentation checks; transactional services remain authoritative.

SlotSnapshot lives for a single call. It replaces repeated reads, not the rules
in services.validate / coordination.check_dependency. No result is persisted.
"""
from datetime import date, datetime, time, timedelta
import re

from django.db.models import Q
from django.utils import timezone

from .models import (CalendarException, OpeningPeriod, Policy, Reservation,
                     SafetyAcknowledgement, SafetyProcedure, UserQualification)
from . import services as s


def text_time(value):
    return timezone.localtime(value, s.TZ).isoformat(timespec="seconds")


class SlotSnapshot:
    """Facts for one applicant, resource, date and instant, with bounded reads."""
    def __init__(self, resource, user, day, at):
        self.resource, self.user, self.day, self.at = resource, user, day, at
        self.roles = set(user.roles.values_list("code", flat=True))
        a = datetime.combine(day, time(), s.TZ); z = a + timedelta(days=1)
        self.future = list(Reservation.objects.filter(applicant=user,
            status__in=s.FUTURE, starts_at__gt=at).values(
                "pk", "resource__kind", "starts_at", "ends_at", "status"))
        self.personal = list(Reservation.objects.filter(applicant=user,
            resource__kind=resource.kind, status__in=s.ACTIVE,
            starts_at__lt=z, ends_at__gt=a).values("pk", "starts_at", "ends_at"))
        self.resource_rows = list(Reservation.objects.filter(resource=resource,
            status__in=s.ACTIVE, starts_at__lt=z, ends_at__gt=a).values(
                "pk", "starts_at", "ends_at"))
        self.pending_rooms = list(Reservation.objects.filter(applicant=user,
            resource__kind="ROOM", status__in=s.FUTURE, starts_at__lt=z,
            ends_at__gt=a).values("pk", "starts_at", "ends_at")) if resource.kind == "ROOM" else []
        self.dependency_rows = []
        labs = {resource.lab_id}
        if resource.kind == "ROOM":
            dependencies = Reservation.objects.filter(resource__equipment__access_room=resource,
                resource__equipment__portable=False, status__in=s.ACTIVE,
                starts_at__lt=z, ends_at__gt=a)
            self.dependency_rows = list(dependencies.values("starts_at", "ends_at", "applicant_id"))
        elif not resource.equipment.portable and resource.equipment.access_room_id:
            room = resource.equipment.access_room
            labs.add(room.lab_id)
            self.dependency_rows = list(Reservation.objects.filter(resource=room,
                status__in=s.ACTIVE, starts_at__lt=z, ends_at__gt=a).values(
                    "starts_at", "ends_at", "applicant_id"))
        self.exceptions = {x.lab_id: x for x in CalendarException.objects.filter(
            lab_id__in=labs, day=day, active=True)}
        self.openings = list(OpeningPeriod.objects.filter(lab_id__in=labs,
            weekday=day.isoweekday(), valid_from__lte=day).filter(
                Q(valid_to__isnull=True) | Q(valid_to__gte=day)))
        self.qualifications = list(UserQualification.objects.filter(user=user,
            revoked=False, issued_at__lte=at).values("qualification_id", "expires_at"))
        today = timezone.localdate()
        self.required_safety_ids = list(SafetyProcedure.objects.filter(
            status="PUBLISHED", requires_acknowledgement=True,
            effective_from__lte=today, review_due_date__gte=today,
        ).filter(Q(resource_id=resource.pk) | Q(lab_scope_id=resource.lab_id))
          .values_list("pk", flat=True))
        self.acknowledged_safety_ids = set(SafetyAcknowledgement.objects.filter(
            user=user, procedure_id__in=self.required_safety_ids
        ).values_list("procedure_id", flat=True)) if self.required_safety_ids else set()
        self.user_overdue = s.overdue_for(user, at)
        self.resource_overdue = s.overdue_resource(resource, at)

    def ensure(self, user, resource, at, start, end):
        if (user.pk != self.user.pk or resource.pk != self.resource.pk or at != self.at
                or start.astimezone(s.TZ).date() != self.day
                or end.astimezone(s.TZ).date() != self.day):
            raise ValueError("候选时段快照与当前校验范围不一致")

    def has_role(self, *codes):
        return self.user.is_active and bool(self.roles.intersection(codes))

    def exception(self, resource, day):
        if day != self.day:
            raise ValueError("候选时段快照已超出日期范围")
        return self.exceptions.get(resource.lab_id)

    def opening_allows(self, resource, start, end):
        a, b = start.astimezone(s.TZ), end.astimezone(s.TZ)
        from .opening_calendar import weekly_allows
        return weekly_allows(resource,a.date(),a.time().replace(tzinfo=None),b.time().replace(tzinfo=None),self.openings)

    @staticmethod
    def overlaps(rows, start, end, exclude=None):
        return [x for x in rows if x.get("pk") != exclude and x["starts_at"] < end and x["ends_at"] > start]

    def dependency_conflict(self, start, end, applicant):
        return any(x["starts_at"] < end and x["ends_at"] > start
                   and (applicant is None or x["applicant_id"] != applicant.pk)
                   for x in self.dependency_rows)

    def qualified(self, qualification_id, end):
        return any(x["qualification_id"] == qualification_id and
                   (x["expires_at"] is None or x["expires_at"] >= end)
                   for x in self.qualifications)

    def needs_safety_ack(self, user, resource):
        if user.pk != self.user.pk or resource.pk != self.resource.pk:
            raise ValueError("安全确认快照与当前用户或资源不一致")
        return bool(set(self.required_safety_ids) - self.acknowledged_safety_ids)

    def future_count(self, kind, exclude):
        return sum(x["resource__kind"] == kind and x["pk"] != exclude for x in self.future)

    def resource_conflict(self, start, end, exclude):
        return bool(self.overlaps(self.resource_rows, start, end, exclude))

    def personal_intervals(self, start, end, exclude):
        return [(x["starts_at"], x["ends_at"]) for x in self.overlaps(self.personal, start, end, exclude)]

    def pending_room_conflict(self, start, end, exclude):
        return bool(self.overlaps(self.pending_rooms, start, end, exclude))


def slot_options(resource, user, day, duration_minutes=60, at=None):
    at = at or timezone.now()
    if isinstance(day, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("候选日期应为年-月-日")
        try: day = date.fromisoformat(day)
        except ValueError: raise ValueError("候选日期无效") from None
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("请选择有效的候选日期")
    policy = Policy.objects.get(pk=1)
    if not re.fullmatch(r"[1-9]\d{0,3}", str(duration_minutes)):
        raise ValueError("时长须为正整数分钟")
    duration_minutes = int(duration_minutes)
    if duration_minutes > policy.max_hours * 60 or duration_minutes % policy.slot_minutes:
        raise ValueError(f"时长须符合{policy.slot_minutes}分钟粒度且不超过{policy.max_hours}小时")
    local_day = at.astimezone(s.TZ).date()
    if not local_day <= day <= (at + timedelta(days=policy.window_days)).astimezone(s.TZ).date():
        raise ValueError(f"候选日期须位于未来{policy.window_days}天预约窗口内")
    snapshot = SlotSnapshot(resource, user, day, at)
    duration = timedelta(minutes=duration_minutes)
    opening = snapshot.exceptions.get(resource.lab_id)
    if opening:
        windows = [] if opening.closed else [(opening.starts_at, opening.ends_at)]
    else:
        from .opening_calendar import effective_weekly_rows
        effective = effective_weekly_rows(resource, day, snapshot.openings)
        windows = [] if any(row.closed_all_day for row in effective) else [
            (row.starts_at, row.ends_at) for row in effective
        ]
    candidates = set()
    for opens, closes in windows:
        a = datetime.combine(day, opens, s.TZ); z = datetime.combine(day, closes, s.TZ)
        minute = a.hour * 60 + a.minute
        rounded = ((minute + policy.slot_minutes - 1) // policy.slot_minutes) * policy.slot_minutes
        a = datetime.combine(day, time(), s.TZ) + timedelta(minutes=rounded)
        # The same validator remains responsible for policy precision and time limits.
        while a + duration <= z and len(candidates) < 96:
            candidates.add(a); a += timedelta(minutes=policy.slot_minutes)
    items = []
    for start in sorted(candidates):
        end = start + duration
        draft = Reservation(applicant=user, resource=resource, starts_at=start,
                            ends_at=end, participants=1, purpose="候选时段只读校验")
        try:
            s.validate(draft, policy, at, snapshot=snapshot)
            available, reason = True, "当前检查通过，提交时再次核验"
        except s.BusinessError as error:
            available, reason = False, str(error)
        items.append({"starts_at": text_time(start), "ends_at": text_time(end),
                      "label": f"{start:%H:%M}—{end:%H:%M}",
                      "available": available, "reason": reason})
    return {"day": day.isoformat(), "duration_minutes": duration_minutes,
            "checked_at": text_time(at), "items": items,
            "notice": "本人资格与资源时段的当前快照；人数、最终冲突和规则在提交及批准时重验。待审核不占资源时段。"
                      if items else "当日没有容纳所选时长的开放时段。"}


def next_step(booking, at=None):
    at = at or timezone.now(); resource = booking.resource
    location = (resource.equipment.handover_location if resource.kind == "EQUIPMENT" else "") or resource.lab.location or "交接地点尚未登记，请联系实验室管理员"
    if booking.status == "PENDING":
        title, role, action = "等待审核", "对应实验室管理员", "查看审核通知；待审核期间可补充说明。时段尚未保留。"
    elif booking.status == "CONFIRMED" and booking.review_required:
        title, role, action = "暂停交接，等待复核", "对应实验室管理员", "查看复核说明并联系管理员；复核结束前不能领取或入场。"
    elif booking.status == "CONFIRMED":
        deadline = booking.late_start_until or booking.starts_at + timedelta(minutes=Policy.objects.get(pk=1).grace_minutes)
        if at >= booking.ends_at or at > deadline:
            title, role, action = "计划交接时间已过", "申请人与对应实验室管理员", "联系管理员核对实际情况和后续安排；本页不代表已经领取或入场。"
        else:
            title, role = "等待现场领取" if resource.kind == "EQUIPMENT" else "等待现场入场", "申请人与对应实验室管理员"
            action = "按预约时段到场，由管理员核对身份、设备标签、配件及实际状态后办理借出。" if resource.kind == "EQUIPMENT" else "由负责教师按时到场，管理员核对身份、人数和场地状态后登记开始。"
    elif booking.status == "IN_USE":
        title = "使用已超期，请联系管理员" if at > booking.ends_at else "使用中，等待归还" if resource.kind == "EQUIPMENT" else "使用中，等待离场检查"
        role = "实际使用人与对应实验室管理员"
        action = "携带设备和全部配件办理实际归还；损坏或缺件应如实报告。" if resource.kind == "EQUIPMENT" else "结束使用后由管理员检查场地并登记实际结束；出现异常如实报告。"
    else:
        title, role, action = "本次预约已结束", "申请人", "查看历史凭据；如需再次使用，请重新查询并申请。"
    return {"title": title, "responsible_role": role, "action": action, "location": location}


def approval_context(booking, actor, at=None):
    at = at or timezone.now()
    s.check_manager(actor, booking.resource)
    s.require(actor.pk != booking.applicant_id, "不能审批本人申请")
    checks = []
    def check(key, label, operation):
        try:
            detail = operation(); checks.append({"key": key, "label": label, "ok": True, "detail": detail or "当前检查通过"})
        except s.BusinessError as error:
            checks.append({"key": key, "label": label, "ok": False, "detail": str(error)})
    policy = Policy.objects.get(pk=1)
    check("state", "申请状态", lambda: s.require(booking.status == "PENDING", "申请已不处于待审核状态"))
    check("time", "时段与开放安排", lambda: s.validate_time(booking.resource, booking.starts_at, booking.ends_at, policy, at))
    def quota():
        count = Reservation.objects.filter(applicant=booking.applicant, resource__kind=booking.resource.kind,
            status__in=s.FUTURE, starts_at__gt=at).exclude(pk=booking.pk).count()
        limit = policy.room_quota if booking.resource.kind == "ROOM" else policy.equipment_quota
        s.require(booking.starts_at <= at or count < limit, f"其他未来同类预约{count}项，额度上限{limit}项")
        return f"其他未来同类预约{count}项，本申请批准后仍按上限{limit}项核验"
    check("quota", "个人未来预约配额", quota)
    def conflict():
        qs = Reservation.objects.filter(resource=booking.resource, status__in=s.ACTIVE,
            starts_at__lt=booking.ends_at, ends_at__gt=booking.starts_at).exclude(pk=booking.pk)
        s.require(not qs.exists(), "该资源时段已有已确认或使用中的预约")
    check("conflict", "同资源时间冲突", conflict)
    check("eligibility", "资格、资源限制及完整业务规则", lambda: s.validate(booking, policy, at, exclude=booking.pk))
    return {"checks": checks, "checked_at": text_time(at),
            "waiting_minutes": max(0, int((at-booking.created_at).total_seconds()//60)),
            "can_approve": all(x["ok"] for x in checks),
            "notice": "只读检查快照，不保留资源时段；提交审核决定时由业务服务重新核验。"}
