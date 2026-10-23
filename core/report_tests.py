"""Numeric report regressions with isolated, explicitly simulated history."""
from datetime import datetime, time, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import services as s
from .coordination import calendar_exception
from .models import (CalendarException, Loan, OpeningPeriod, Reservation,
                     Resource, ReturnRecord, RoomUse, ServicePeriod, User)
from .reports import utilization


class CalendarReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        self.resource = Resource.objects.get(code="EQ-OSC-01")
        self.room = Resource.objects.get(code="ROOM-101")
        self.admin = User.objects.get(username="admin")
        self.sysadmin = User.objects.get(username="sysadmin")
        self.student = User.objects.get(username="student")
        self.teacher = User.objects.get(username="teacher")
        self.day = timezone.localdate() + timedelta(days=1)

    def moment(self, hour, minute=0, *, day=None):
        return datetime.combine(day or self.day, time(hour, minute), s.TZ)

    def exception(self, *, closed=False, start=10, end=12, day=None):
        return calendar_exception(
            self.sysadmin, self.resource.lab_id, day or self.day, closed,
            None if closed else time(start), None if closed else time(end),
            "报表回归模拟日历",
        )

    def report(self, resource=None, start=None, end=None):
        start = start or self.moment(8)
        end = end or self.moment(20)
        # Advance only the report observation clock; these are simulated facts.
        return utilization([resource or self.resource], start, end, at=end)[0]

    def booking(self, resource=None):
        resource = resource or self.resource
        return Reservation.objects.create(
            applicant=self.teacher if resource.kind == "ROOM" else self.student,
            resource=resource, starts_at=self.moment(9), ends_at=self.moment(15),
            purpose="报表模拟历史凭据", participants=1, status="COMPLETED",
            approval_mode="MANUAL" if resource.kind == "ROOM" else "AUTO",
            request_fingerprint="report-regression",
        )

    def loan(self, returned_at=None):
        booking = self.booking()
        loan = Loan.objects.create(
            reservation=booking, operator=self.admin,
            arrived_at=self.moment(9), service_started_at=self.moment(9),
            handed_over_at=self.moment(9, 30), due_at=booking.ends_at,
            tag_snapshot=self.resource.code, condition_out="模拟完好",
        )
        if returned_at:
            ReturnRecord.objects.create(
                loan=loan, operator=self.admin, returned_at=returned_at,
                note="模拟归还",
            )
        return loan

    def test_aud0078_closed_day_has_no_service_or_usage_hours(self):
        self.loan(returned_at=self.moment(11, 30))
        self.exception(closed=True)
        row = self.report()
        self.assertEqual((row["capacity"], row["booked"], row["actual"]), (0, 0, 0))
        self.assertIsNone(row["rate"])
        self.assertFalse(s.availability(self.resource, self.moment(10), self.moment(11))[0])

    def test_aud0078_shortened_day_clips_actual_and_planned_equally(self):
        self.loan(returned_at=self.moment(11, 30))
        self.exception(start=10, end=12)
        row = self.report()
        self.assertEqual((row["capacity"], row["booked"], row["actual"], row["rate"]), (2, 2, 1.5, 75))

    def test_aud0078_extra_opening_replaces_an_absent_weekly_period(self):
        OpeningPeriod.objects.filter(lab=self.resource.lab, weekday=self.day.isoweekday()).delete()
        self.assertEqual(self.report()["capacity"], 0)
        self.exception(start=9, end=12)
        self.assertEqual(self.report()["capacity"], 3)
        self.assertTrue(s.availability(self.resource, self.moment(10), self.moment(11))[0])

    def test_normal_weekly_opening_is_unchanged(self):
        self.assertEqual(self.report()["capacity"], 12)

    def test_inactive_exception_does_not_override_weekly_opening(self):
        record = self.exception(closed=True)
        CalendarException.objects.filter(pk=record.pk).update(active=False)
        self.assertEqual(self.report()["capacity"], 12)

    def test_only_current_exception_version_is_used(self):
        first = self.exception(closed=True)
        self.exception(start=9, end=12)
        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertEqual(self.report()["capacity"], 3)

    def test_exception_still_intersects_normal_service_history(self):
        self.loan(returned_at=self.moment(14))
        self.exception(start=10, end=14)
        original = self.resource.service_periods.get()
        original.ends_at = self.moment(11)
        original.save(update_fields=["ends_at"])
        hold = s.audit(self.admin, self.resource, "SERVICE_CHANGE", new={"status": "MAINTENANCE"}, reason="模拟维护开始", at=self.moment(11))
        resume = s.audit(self.admin, self.resource, "SERVICE_CHANGE", new={"status": "NORMAL"}, reason="模拟维护结束", at=self.moment(12))
        ServicePeriod.objects.create(resource=self.resource, status="MAINTENANCE", starts_at=self.moment(11), ends_at=self.moment(12), audit=hold)
        ServicePeriod.objects.create(resource=self.resource, status="NORMAL", starts_at=self.moment(12), audit=resume)
        row = self.report()
        self.assertEqual((row["capacity"], row["booked"], row["actual"], row["rate"]), (3, 3, 3, 100))

    def test_resource_creation_and_report_window_clip_exception(self):
        self.exception(start=9, end=15)
        Resource.objects.filter(pk=self.resource.pk).update(created_at=self.moment(11))
        self.resource.refresh_from_db()
        self.assertEqual(self.report(start=self.moment(10), end=self.moment(12))["capacity"], 1)

    def test_multi_day_report_keeps_exception_local_and_end_exclusive(self):
        self.exception(closed=True)
        end = self.moment(0, day=self.day + timedelta(days=2))
        self.assertEqual(self.report(start=self.moment(0), end=end)["capacity"], 12)

    def test_room_usage_is_clipped_by_the_same_exception(self):
        booking = self.booking(self.room)
        RoomUse.objects.create(
            reservation=booking, started_by=self.admin, arrived_at=self.moment(9),
            started_at=self.moment(9), actual_participants=1, start_note="模拟入场",
            ended_by=self.admin, ended_at=self.moment(11), end_note="模拟离场",
        )
        self.exception(start=10, end=12)
        row = self.report(self.room)
        self.assertEqual((row["capacity"], row["booked"], row["actual"], row["rate"]), (2, 2, 1, 50))

    def test_open_loan_is_counted_only_to_report_observation(self):
        self.loan()
        self.exception(start=10, end=12)
        row = self.report(end=self.moment(11))
        self.assertEqual((row["capacity"], row["actual"], row["rate"]), (1, 1, 100))

    def test_weekly_history_versions_are_preserved_without_exception(self):
        weekday = self.day.isoweekday()
        opening = OpeningPeriod.objects.get(lab=self.resource.lab, weekday=weekday)
        opening.valid_to = self.day - timedelta(days=1)
        opening.save(update_fields=["valid_to"])
        OpeningPeriod.objects.create(lab=self.resource.lab, weekday=weekday, starts_at=time(10), ends_at=time(14), valid_from=self.day)
        self.assertEqual(self.report()["capacity"], 4)

    def test_exactly_31_days_remains_valid(self):
        start = self.moment(8)
        self.report(start=start, end=start + timedelta(days=31))

    def test_aud0081_more_than_31_days_is_rejected_without_truncation(self):
        start = self.moment(8)
        with self.assertRaisesMessage(s.BusinessError, "31天"):
            self.report(start=start, end=start + timedelta(days=31, seconds=1))

    def test_invalid_or_future_interval_is_rejected(self):
        start = self.moment(8)
        with self.assertRaises(s.BusinessError):
            utilization([self.resource], start, start, at=start)
        with self.assertRaises(s.BusinessError):
            utilization([self.resource], start, self.moment(12), at=self.moment(11))
