"""Read-only work queues. State transitions remain in the transaction services."""
from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import Count, Q, Window
from django.urls import reverse
from django.utils import timezone
from .models import AuditEvent, LabGrant, Reservation


REVIEW_WINDOWS = {
    "priority": "优先处理（1 小时内）",
    "soon": "即将开始（1–2 小时）",
    "future": "全部可处理待审",
}


def managed_bookings(user, queryset=None):
    queryset = Reservation.objects.all() if queryset is None else queryset
    labs = LabGrant.objects.filter(user=user).values("lab_id")
    return queryset.filter(resource__lab_id__in=labs)


def managed_reviews(user, queryset=None):
    return managed_bookings(user, queryset).filter(status="PENDING").exclude(applicant=user)


def review_window_conditions(now):
    one_hour = now + timedelta(hours=1)
    two_hours = now + timedelta(hours=2)
    return {
        "priority": Q(starts_at__gt=now, starts_at__lte=one_hour),
        "soon": Q(starts_at__gt=one_hour, starts_at__lte=two_hours),
        "future": Q(starts_at__gt=now),
    }


def review_snapshot(user, now):
    conditions = review_window_conditions(now)
    # Window aggregates and preview rows come from a single PostgreSQL statement.
    totals = {key: Window(Count("pk", filter=condition)) for key, condition in conditions.items()}
    rows = list(managed_reviews(user).filter(conditions["future"])
                .select_related("resource__lab")
                .only("starts_at", "ends_at", "created_at", "status", "resource__name", "resource__lab__name")
                .annotate(**totals).order_by("starts_at", "pk")[:5])
    counts = {key: getattr(rows[0], key) if rows else 0 for key in REVIEW_WINDOWS}
    numbers = [
        {
            "key": key, "label": label, "count": counts[key],
            "percent": round(counts[key] * 100 / counts["future"]) if counts["future"] else 0,
            "url": reverse("bookings") + "?" + urlencode({"queue": "review", "review_window": key, "sort": "starts"}),
        }
        for key, label in REVIEW_WINDOWS.items()
    ]
    preview = []
    for row in rows:
        remaining = row.starts_at - now
        if remaining > timedelta(hours=2):
            break
        preview.append({
            "booking": row,
            "label": "优先处理" if remaining <= timedelta(hours=1) else "即将开始",
            "priority": remaining <= timedelta(hours=1),
            "wait_minutes": max(0, (now - row.created_at) // timedelta(minutes=1)),
        })
    return {"numbers": numbers, "rows": preview, "updated_at": now}


def acceptance_queue(qs, user):
    performed = AuditEvent.objects.filter(entity_type='workorder',actor=user,action__in=['START','PROGRESS','SUBMIT']).values('entity_id')
    return qs.filter(incident__resource__lab__labgrant__user=user,status='AWAITING_ACCEPTANCE').exclude(assigned_to=user).exclude(pk__in=performed).distinct()


def booking_queue(qs, name, now=None):
    now = now or timezone.now()
    rules = {
        'review': Q(status='PENDING'),
        'recheck': Q(status='CONFIRMED', review_required=True),
        'handover': Q(status='CONFIRMED', starts_at__date=timezone.localdate(now), review_required=False),
        'overdue': Q(status='IN_USE', ends_at__lt=now),
    }
    return qs.filter(rules[name]) if name in rules else qs


HANDOVER_QUEUES = {
    "handover": ("今日待交接", "已确认且今天开始的预约"),
    "overdue": ("超期未结束", "已超出结束时间，仍在使用"),
    "recheck": ("待复核承诺", "条件变化后，交接前需复核"),
}


def handover_snapshot(user, now):
    managed = managed_bookings(user)
    groups = []
    for key, (label, note) in HANDOVER_QUEUES.items():
        queue = booking_queue(managed, key, now)
        if key == "recheck":
            queue = queue.exclude(applicant=user)
        rows = list(queue.select_related("resource__lab")
                    .only("starts_at", "ends_at", "resource__name", "resource__lab__name")
                    .annotate(queue_count=Window(Count("pk"))).order_by("starts_at", "pk")[:3])
        groups.append({
            "key": key, "label": label, "note": note, "rows": rows,
            "count": rows[0].queue_count if rows else 0,
            "url": reverse("bookings") + "?" + urlencode({"queue": key, "sort": "starts"}),
        })
    return groups


def tasks_for(user, incidents, work):
    cards = []
    if user.has_role('LAB_ADMIN'):
        pending = incidents.filter(resource__lab__labgrant__user=user, status='PENDING').distinct()
        cards.append({'name': '异常待核实', 'count': pending.count(), 'note': '核实事实后决定隔离、处置或维修', 'url': reverse('incidents')+'?queue=managed&status=PENDING'})
        acceptance = acceptance_queue(work,user)
        cards.append({'name': '维修待验收', 'count': acceptance.count(), 'note': '执行人与验收人分离', 'url': reverse('work')+'?queue=acceptance&status=AWAITING_ACCEPTANCE'})
    if user.has_role('TECHNICIAN'):
        cards.append({'name': '我的维修任务', 'count': work.filter(assigned_to=user, status__in=['OPEN','IN_PROGRESS']).count(),
                      'note': '更新处理过程，提交后等待独立验收', 'url': reverse('work')+'?status=ACTIVE'})
    return cards
