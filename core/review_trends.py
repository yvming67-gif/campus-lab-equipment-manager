"""Read-only approval history reconstructed from reservation audit transitions."""
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncDate
from django.urls import reverse

from .models import AuditEvent
from .services import TZ
from .workbench import managed_bookings


ENTER = Q(action="SUBMIT", new_state__status="PENDING")
EXIT = Q(old_state__status="PENDING", new_state__status__in=("CONFIRMED", "REJECTED", "CANCELLED", "EXPIRED"))
DECIDED = EXIT & Q(action__in=("APPROVE", "REJECT"))
OTHER_EXIT = EXIT & ~DECIDED
METRICS = {
    "incoming": ("新增待审", ENTER),
    "decided": ("人工审核", DECIDED),
    "remaining": ("日末积压", None),
}


def day_start(day):
    return datetime.combine(day, time.min, TZ)


def review_history(user):
    bookings = managed_bookings(user).exclude(applicant=user).filter(approval_mode="MANUAL")
    events = AuditEvent.objects.filter(entity_type="reservation", entity_id__in=bookings.values("pk"))
    return bookings, events.filter(ENTER | EXIT)


def approval_trend(user, now):
    current_day = now.astimezone(TZ).date()
    first = current_day - timedelta(days=6)
    start = day_start(first)
    bookings, events = review_history(user)
    counted = events.filter(created_at__lt=now)
    older = counted.filter(created_at__lt=start).aggregate(incoming=Count("pk", filter=ENTER), outgoing=Count("pk", filter=EXIT))
    grouped = counted.filter(created_at__gte=start).order_by().annotate(day=TruncDate("created_at", tzinfo=TZ)).values("day").annotate(
        incoming=Count("pk", filter=ENTER), decided=Count("pk", filter=DECIDED), other_exit=Count("pk", filter=OTHER_EXIT),
    )
    daily = {item["day"]: item for item in grouped}
    carry = older["incoming"] - older["outgoing"]
    coverage = carry >= 0
    days = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        values = daily.get(day, {"incoming": 0, "decided": 0, "other_exit": 0})
        carry += values["incoming"] - values["decided"] - values["other_exit"]
        coverage &= carry >= 0
        days.append({**values, "day": day, "today": day == current_day, "remaining": carry})
    audited_submission = counted.filter(ENTER, entity_id=OuterRef("pk"))
    audited_exit = counted.filter(EXIT, entity_id=OuterRef("pk"))
    before_now = bookings.filter(created_at__lt=now)
    pending = Q(status="PENDING")
    incomplete = before_now.filter(
        ~Exists(audited_submission) | (pending & Exists(audited_exit)) | (~pending & ~Exists(audited_exit))
    ).exists()
    coverage &= not incomplete and carry == before_now.filter(pending).count()

    peak_count = max(1, *(max(row["incoming"], row["decided"]) for row in days))
    peak_backlog = max(1, *(row["remaining"] for row in days))
    for index, row in enumerate(days):
        row["incoming_height"] = round(100 * row["incoming"] / peak_count)
        row["decided_height"] = round(100 * row["decided"] / peak_count)
        row["incoming_url"] = _day_url(row["day"], "incoming")
        row["decided_url"] = _day_url(row["day"], "decided")
        row["remaining_url"] = _day_url(row["day"], "remaining")
        row["x"] = index * 100 + 50
        row["y"] = round(96 - 88 * row["remaining"] / peak_backlog)
    return {"days": days, "coverage": coverage, "as_of": now}


def _day_url(day, metric):
    return reverse("review-trend-detail") + "?" + urlencode({"day": day.isoformat(), "metric": metric})


def approval_detail(user, day, metric, now):
    bookings, events = review_history(user)
    start = day_start(day)
    end = min(now, start + timedelta(days=1))
    if metric == "remaining":
        before_end = events.filter(created_at__lt=end)
        entered = before_end.filter(ENTER).values("entity_id")
        left = before_end.filter(EXIT).values("entity_id")
        return bookings.filter(pk__in=entered).exclude(pk__in=left).order_by("created_at", "pk")
    return events.filter(METRICS[metric][1], created_at__gte=start, created_at__lt=end).order_by("created_at", "pk")
