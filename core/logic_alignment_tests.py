"""Counterexamples for the analysis-to-behaviour evidence boundary.

These tests use simulated accounts and isolated Django test transactions.
They do not assert that a real school has adopted the course policy.
"""
import json
from datetime import timedelta
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from . import services as s, coordination as c
from .models import (User, Resource, Reservation, ApprovalDecision,
                     UserQualification, ReservationReview, Role, UserRole, LabGrant)


class LogicAlignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    def setUp(self):
        self.student = User.objects.get(username='student')
        self.other = User.objects.get(username='student2')
        self.admin = User.objects.get(username='admin')
        self.eq = Resource.objects.get(code='EQ-OSC-01')
        self.start = timezone.localtime(timezone.now()+timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        self.end = self.start+timedelta(hours=1)

    def book(self, user=None, resource=None, start=None):
        start = start or self.start
        return s.create_reservation(user or self.student,
                                    (resource or self.eq).pk,
                                    start, start+timedelta(hours=1), '模拟实验')

    def test_ac04_missing_resource_is_404_in_web_and_mini(self):
        before = Reservation.objects.count()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/resources/999999999/').status_code, 404)
        result = self.client.post('/api/mini/v1/auth/login/',
            json.dumps({'username':'student', 'password':'Demo@2026'}),
            content_type='application/json')
        self.assertEqual(result.status_code, 200)
        token = result.json()['data']['token']
        response = self.client.get('/api/mini/v1/resources/999999999/',
                                  HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Reservation.objects.count(), before)

    def test_ac05_availability_is_not_personal_training_eligibility(self):
        restricted = Resource.objects.get(code='EQ-ROB-01')
        UserQualification.objects.filter(user=self.student).update(revoked=True)
        available, label = s.availability(restricted, self.start, self.end,
                                         applicant=self.student)
        self.assertTrue(available)
        self.assertIn('提交时另核验本人资格', label)
        with self.assertRaisesMessage(s.BusinessError, '培训资格'):
            self.book(resource=restricted)
        self.assertFalse(Reservation.objects.filter(applicant=self.student).exists())

    def test_ac51_history_filters_apply_to_bookings_and_owner(self):
        wanted = self.book()
        camera = Resource.objects.get(code='EQ-CAM-01')
        self.book(resource=camera, start=self.start+timedelta(days=1))
        self.book(user=self.other, start=self.start+timedelta(hours=2))
        self.client.force_login(self.student)
        response = self.client.get('/bookings/', {
            'q':self.eq.code, 'lab':self.eq.lab_id,
            'day':self.start.date().isoformat(), 'status':'CONFIRMED'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([b.pk for b in response.context['bookings']], [wanted.pk])

    def test_ac25_unrelated_report_does_not_isolate_until_confirmed(self):
        incident = s.report_incident(self.student, self.eq.pk, 'FAULT', '模拟观察')
        self.eq.refresh_from_db()
        self.assertEqual((incident.status, incident.blocks_service,
                          self.eq.service_status), ('PENDING',False,'NORMAL'))
        s.verify_incident(self.admin, incident.pk, 'CONFIRM', '模拟复核属实')
        incident.refresh_from_db(); self.eq.refresh_from_db()
        self.assertEqual((incident.status, incident.blocks_service,
                          self.eq.service_status), ('CONFIRMED',True,'ISOLATED'))

    def test_ac65_recheck_appends_without_replacing_initial_decision(self):
        booking = self.book()
        decision = ApprovalDecision.objects.get(reservation=booking)
        original = (decision.pk, decision.mode, decision.actor_id,
                    decision.decided_at, decision.reason)
        c.flag_affected(Reservation.objects.filter(pk=booking.pk), self.admin,
                        '模拟条件复核', timezone.now())
        review = c.recheck(self.admin, booking.pk, True, '当前条件满足',
                           at=self.start+timedelta(minutes=5))
        booking.refresh_from_db(); decision.refresh_from_db()
        self.assertFalse(booking.review_required)
        self.assertEqual((booking.status,booking.starts_at,booking.ends_at),
                         ('CONFIRMED',self.start,self.end))
        self.assertEqual((decision.pk,decision.mode,decision.actor_id,
                          decision.decided_at,decision.reason), original)
        self.assertEqual(ReservationReview.objects.filter(reservation=booking).count(),1)
        self.assertTrue(review.accepted)

    def test_ac66_failed_recheck_keeps_commitment_until_explicit_cancel(self):
        booking = self.book()
        c.flag_affected(Reservation.objects.filter(pk=booking.pk), self.admin,
                        '模拟条件复核', timezone.now())
        late = self.start+timedelta(minutes=31)
        with self.assertRaisesMessage(s.BusinessError, '领取截止'):
            c.recheck(self.admin, booking.pk, True, '请求通过', at=late)
        booking.refresh_from_db()
        self.assertEqual((booking.status,booking.review_required),('CONFIRMED',True))
        self.assertFalse(ReservationReview.objects.filter(reservation=booking).exists())
        c.recheck(self.admin, booking.pk, False, '不能执行，明确取消', at=late)
        booking.refresh_from_db()
        self.assertEqual((booking.status,booking.review_required),('CANCELLED',False))
        self.assertEqual(ApprovalDecision.objects.filter(reservation=booking).count(),1)

    def test_former_repair_executor_cannot_accept_after_reassignment(self):
        """Changing the assignee must not erase the actual executor's history."""
        technician = User.objects.get(username='tech')
        UserRole.objects.create(user=technician, role=Role.objects.get(code='LAB_ADMIN'))
        LabGrant.objects.get_or_create(user=technician, lab=self.eq.lab)
        replacement = User.objects.create_user(username='logic-tech', school_no='SIM-TECH-NEW')
        UserRole.objects.create(user=replacement, role=Role.objects.get(code='TECHNICIAN'))
        incident = s.report_incident(self.admin, self.eq.pk, 'FAULT', '模拟故障')
        s.verify_incident(self.admin, incident.pk, 'CONFIRM', '核实')
        work = s.create_work_order(self.admin, incident.pk, technician.pk, '安排维修')
        s.update_work(technician, work.pk, 'START', '开始检测')
        s.update_work(technician, work.pk, 'PROGRESS', '已参与实际处理')
        c.reassign_work(self.admin, work.pk, replacement.pk, '后续接手')
        s.update_work(replacement, work.pk, 'SUBMIT', '修复提交')
        with self.assertRaisesMessage(s.BusinessError, '维修'):
            s.accept_work(technician, work.pk, True, '原执行人不应验收')
        work.refresh_from_db()
        self.assertEqual(work.status, 'AWAITING_ACCEPTANCE')
        s.accept_work(self.admin, work.pk, True, '独立复测通过')
        work.refresh_from_db()
        self.assertEqual(work.status, 'CLOSED')

    def test_current_recheck_does_not_add_to_full_future_category_quota(self):
        """A commitment at its original start no longer consumes a future slot."""
        teacher = User.objects.get(username='teacher')
        room = Resource.objects.get(code='ROOM-101')
        for user, resource in [(self.student, self.eq), (teacher, room)]:
            with self.subTest(kind=resource.kind):
                booking = s.create_reservation(user, resource.pk, self.start,
                    self.end, '模拟旧预约', at=self.start-timedelta(days=1))
                if booking.status == 'PENDING':
                    s.approve(self.admin, booking.pk, True, '模拟场地审核',
                              at=self.start-timedelta(hours=1))
                c.flag_affected(Reservation.objects.filter(pk=booking.pk),
                                self.admin, '模拟条件复核', self.start)
                now = self.start+timedelta(minutes=5)
                for days in [1, 2, 3]:
                    start = self.start+timedelta(days=days)
                    s.create_reservation(user, resource.pk, start,
                        start+timedelta(hours=1), '合法未来预约', at=now)
                future = Reservation.objects.filter(applicant=user,
                    resource__kind=resource.kind, status__in=s.FUTURE,
                    starts_at__gt=now)
                self.assertEqual(future.count(), 3)
                review = c.recheck(self.admin, booking.pk, True,
                                  '原宽限内，当前条件满足', at=now)
                booking.refresh_from_db()
                self.assertTrue(review.accepted)
                self.assertEqual((booking.status, booking.review_required),
                                 ('CONFIRMED', False))
                self.assertEqual(future.count(), 3)
