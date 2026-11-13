"""Resolve lab defaults and optional per-room weekly opening schedules."""
from django.db.models import Q
from datetime import date, timedelta
from django.utils import timezone

from .models import CalendarException, OpeningPeriod


def merge_time_windows(windows):
    """Return sorted time ranges, merging overlaps and exactly adjacent ranges."""
    merged=[]
    for start,end in sorted(windows,key=lambda pair:pair[0]):
        if merged and start<=merged[-1][1]:
            previous_start,previous_end=merged[-1]
            merged[-1]=(previous_start,max(previous_end,end))
        else:
            merged.append((start,end))
    return merged


def weekly_periods(resource, day):
    """Return the effective weekly rows for a resource on a local date.

    A room-specific schedule replaces the lab default for that weekday/date.
    Equipment continues to inherit its lab schedule; fixed equipment is also
    checked against the linked room schedule by the domain service.
    """
    base = list(OpeningPeriod.objects.filter(
        lab_id=resource.lab_id,
        weekday=day.isoweekday(),
        valid_from__lte=day,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day)))
    return effective_weekly_rows(resource, day, base)


def effective_weekly_rows(resource, day, periods):
    base = [row for row in periods if row.lab_id == resource.lab_id
            and row.weekday == day.isoweekday() and row.valid_from <= day
            and (row.valid_to is None or row.valid_to >= day)]
    if resource.kind == "ROOM":
        room_rows = [row for row in base if row.resource_id == resource.pk]
        if room_rows:
            return room_rows
    return [row for row in base if row.resource_id is None]


def weekly_allows(resource, day, start_time, end_time, periods=None):
    periods = weekly_periods(resource, day) if periods is None else periods
    effective = effective_weekly_rows(resource, day, periods)
    if any(row.closed_all_day for row in effective):
        return False
    windows=merge_time_windows((row.starts_at,row.ends_at) for row in effective)
    return any(starts_at<=start_time and ends_at>=end_time for starts_at,ends_at in windows)


def exception_for(resource, day, exceptions=None):
    if exceptions is not None:
        return exceptions.get(resource.lab_id)
    from .models import CalendarException
    return CalendarException.objects.filter(lab_id=resource.lab_id, day=day, active=True).first()


def calendar_allows(resource, start, end, periods=None, exceptions=None):
    local_start = timezone.localtime(start)
    local_end = timezone.localtime(end)
    exception = exception_for(resource, local_start.date(), exceptions)
    if exception:
        return (not exception.closed
                and exception.starts_at <= local_start.time().replace(tzinfo=None)
                and local_end.time().replace(tzinfo=None) <= exception.ends_at)
    return weekly_allows(
        resource,
        local_start.date(),
        local_start.time().replace(tzinfo=None),
        local_end.time().replace(tzinfo=None),
        periods,
    )


def weekly_schedule(resource, as_of=None):
    """Human-readable schedule for the next seven calendar dates.

    This is descriptive only. Reservation and approval use ``calendar_allows``
    again at the point of action.
    """
    as_of = as_of or timezone.localdate()
    return schedule_by_resource([resource], as_of, days=7)[resource.pk]


def schedule_by_resource(resources, as_of, *, days):
    """Read the same calendar for one or many cards using two queries."""
    if not resources:
        return {}
    days = min(days, (date.max - as_of).days + 1)
    end = as_of + timedelta(days=days - 1)
    labs = {resource.lab_id for resource in resources}
    periods_by_lab = {lab: [] for lab in labs}
    periods = list(OpeningPeriod.objects.filter(
        lab_id__in=labs, valid_from__lte=end,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=as_of)).order_by("starts_at"))
    for row in periods:
        periods_by_lab[row.lab_id].append(row)
    exceptions = {
        (row.lab_id, row.day): row for row in CalendarException.objects.filter(
            lab_id__in=labs, active=True, day__range=(as_of, end)
        )
    }
    return {
        resource.pk: _display_schedule(resource, as_of, days, periods_by_lab[resource.lab_id], exceptions)
        for resource in resources
    }


def _display_schedule(resource, as_of, days, periods, exceptions):
    result = []
    for offset in range(days):
        day = as_of + timedelta(days=offset)
        exception = exceptions.get((resource.lab_id, day))
        if exception:
            ranges = [] if exception.closed else [f"{exception.starts_at:%H:%M}—{exception.ends_at:%H:%M}"]
            closed = exception.closed
            note = "单日关闭例外" if closed else "单日开放例外"
        else:
            rows = effective_weekly_rows(resource, day, periods)
            closed = any(row.closed_all_day for row in rows)
            windows=[] if closed else merge_time_windows((row.starts_at,row.ends_at) for row in rows)
            ranges = [f"{starts_at:%H:%M}—{ends_at:%H:%M}" for starts_at,ends_at in windows]
            note = "每周全天关闭" if closed else "每周开放日历"
        result.append({
            "date": day.isoformat(),
            "weekday": "周" + "一二三四五六日"[day.weekday()],
            "ranges": ranges,
            "note": note if ranges or closed else "未登记开放时段",
            "closed": closed,
        })
    return result
