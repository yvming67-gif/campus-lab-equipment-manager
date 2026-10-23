"""Public occupancy intervals without applicant identity or purpose."""
import re
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from .models import Reservation
from .services import ACTIVE, TZ


def resource_schedule(resource, day="", at=None):
    at = at or timezone.now()
    qs = Reservation.objects.filter(resource=resource, status__in=ACTIVE)
    if day:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("占用日期应为年-月-日")
        try:
            start = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), time(), TZ)
            end = start + timedelta(days=1)
        except (ValueError, OverflowError):
            raise ValueError("占用日期无效") from None
        # An unfinished actual use remains relevant even after its planned end.
        qs = qs.filter(Q(starts_at__lt=end, ends_at__gt=start) | Q(status="IN_USE"))
    else:
        qs = qs.filter(Q(ends_at__gt=at) | Q(status="IN_USE"))
    return qs.order_by("starts_at", "pk")
