"""Regression invariants for the comprehensive audit's confirmed backend defects."""
from datetime import timedelta
from threading import Event, Thread, current_thread
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from . import services as s
from .models import Accessory, AuditEvent, Delivery, DeliveryAttempt, Lab, Resource, User


class AuditInputFixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        self.student = User.objects.get(username='student')
        self.admin = User.objects.get(username='admin')
        self.sys = User.objects.get(username='sysadmin')
        self.eq = Resource.objects.get(code='EQ-OSC-01')

    def resource_fields(self, quantity):
        return dict(code=self.eq.code, name=self.eq.name, kind='EQUIPMENT', lab=self.eq.lab_id,
                    description='模拟合法档案更新', category='示波器', risk='NORMAL', portable='on',
                    accessories='AUDIT-PART,模拟配件,' + quantity, reason='隔离输入回归')

    def test_aud0009_booking_lab_bad_numbers_have_feedback_and_no_results(self):
        self.client.force_login(self.student)
        for value in ['²', '１２', '0', '-1', '1.0', '9223372036854775808', '9' * 5000]:
            with self.subTest(value=value[:25]):
                response = self.client.get('/bookings/', {'lab': value})
                self.assertContains(response, '请选择有效的实验室')
                self.assertEqual(response.context['page'].paginator.count, 0)

    def test_aud0009_booking_valid_lab_still_filters_owned_records(self):
        start = timezone.localtime(timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking = s.create_reservation(self.student, self.eq.pk, start, start + timedelta(hours=1), '模拟有效筛选')
        self.client.force_login(self.student)
        response = self.client.get('/bookings/', {'lab': str(self.eq.lab_id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([x.pk for x in response.context['page']], [booking.pk])
        response = self.client.get('/bookings/', {'lab': str(Lab.objects.exclude(pk=self.eq.lab_id).first().pk)})
        self.assertEqual(response.context['page'].paginator.count, 0)

    def test_aud0009_delivery_bad_identifiers_do_not_attempt_contact(self):
        self.client.force_login(self.sys)
        before = AuditEvent.objects.filter(action='MANUAL_CONTACT').count()
        for value in ['²', '１２', '0', '-1', '1.0', '9223372036854775808', '9' * 5000]:
            with self.subTest(value=value[:25]):
                response = self.client.post('/deliveries/', {'pk': value, 'reason': '模拟原因'}, follow=True)
                self.assertContains(response, '请选择有效投递记录')
        self.assertEqual(AuditEvent.objects.filter(action='MANUAL_CONTACT').count(), before)

    def test_aud0009_accessory_quantities_reject_unicode_and_database_overflow(self):
        self.client.force_login(self.admin)
        original = s.resource_snapshot(self.eq)
        for value in ['', '²', '１２', '0', '-1', '1.0', '2147483648', '9' * 5000]:
            with self.subTest(value=value[:25]):
                response = self.client.post(f'/manage/resource/{self.eq.pk}/', self.resource_fields(value))
                self.assertContains(response, '配件数量须为1至2147483647的十进制正整数')
                self.eq.refresh_from_db()
                self.assertEqual(s.resource_snapshot(self.eq), original)

    def test_aud0009_accessory_valid_integer_and_upper_bound_are_saved(self):
        self.client.force_login(self.admin)
        for value in ['1', '2147483647']:
            with self.subTest(quantity=value):
                response = self.client.post(f'/manage/resource/{self.eq.pk}/', self.resource_fields(value))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(Accessory.objects.get(resource=self.eq, code='AUDIT-PART').quantity, int(value))

    def test_aud0106_report_last_end_date_is_a_field_error_not_server_error(self):
        self.client.force_login(self.admin)
        response = self.client.get('/reports/', {'end': '9999-12-31'})
        self.assertContains(response, '结束日期超出可统计范围，请选择有效日期')
        self.assertContains(response, 'name="end" value="9999-12-31"')
        self.assertEqual(response.context['rows'], [])

    def test_aud0106_default_date_uses_china_date_after_midnight(self):
        from datetime import datetime, timezone as dt_timezone
        self.client.force_login(self.admin)
        with patch('core.views.timezone.now', return_value=datetime(2026, 10, 25, 16, 30, tzinfo=dt_timezone.utc)):
            response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="end" value="2026-10-26"')

    def test_aud0106_regular_report_end_date_keeps_working(self):
        self.client.force_login(self.admin)
        today = timezone.localdate().isoformat()
        response = self.client.get('/reports/', {'start': today, 'end': today})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['error'])
        self.assertTrue(response.context['rows'])


class AuditDeliveryChannelFixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        self.sys = User.objects.get(username='sysadmin')
        self.admin = User.objects.get(username='admin')
        self.student = User.objects.get(username='student')
        self.eq = Resource.objects.get(code='EQ-OSC-01')

    def task(self, lab=None):
        if lab:
            event = AuditEvent.objects.create(actor=self.sys, entity_type='Lab', entity_id=lab.pk,
                                             action='AUDIT_FIX', lab=lab, reason='模拟其他实验室任务')
        else:
            event = s.audit(self.sys, self.eq, 'AUDIT_FIX', reason='模拟渠道回归')
        s.notify(event, [self.student], 'AUDIT_FIX', '隔离模拟通知')
        return Delivery.objects.get(notification__audit=event, channel='MOCK_EXTERNAL' if settings.DEMO_MODE else 'EXTERNAL')

    @override_settings(DEMO_MODE=False)
    def test_aud0107_formal_task_visible_and_manual_contact_is_appended(self):
        task = self.task()
        Delivery.objects.filter(pk=task.pk).update(status='FAILED')
        self.client.force_login(self.sys)
        response = self.client.get('/deliveries/')
        self.assertEqual([x.pk for x in response.context['page']], [task.pk])
        response = self.client.post('/deliveries/', {'pk': str(task.pk), 'reason': '模拟已联系当事人'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action='MANUAL_CONTACT', entity_id=task.pk).exists())

    @override_settings(DEMO_MODE=False)
    def test_aud0107_authorized_lab_admin_can_contact_failed_formal_task(self):
        task = self.task()
        Delivery.objects.filter(pk=task.pk).update(status='FAILED')
        self.client.force_login(self.admin)
        response = self.client.get('/deliveries/')
        self.assertEqual([x.pk for x in response.context['page']], [task.pk])
        response = self.client.post('/deliveries/', {'pk': str(task.pk), 'reason': '模拟管理员线下联系'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(actor=self.admin, action='MANUAL_CONTACT', entity_id=task.pk).exists())

    def test_aud0107_each_mode_only_lists_its_supported_external_channel(self):
        with override_settings(DEMO_MODE=True):
            mock = self.task()
        with override_settings(DEMO_MODE=False):
            formal = self.task()
        self.client.force_login(self.sys)
        for mode, expected in [(True, mock.pk), (False, formal.pk)]:
            with self.subTest(demo_mode=mode), override_settings(DEMO_MODE=mode):
                response = self.client.get('/deliveries/')
                self.assertEqual([x.pk for x in response.context['page']], [expected])

    @override_settings(DEMO_MODE=False)
    def test_aud0107_cross_lab_task_stays_hidden_and_contact_is_denied(self):
        other = Lab.objects.create(code='AUDIT-NO-GRANT', name='模拟未授权实验室', location='模拟位置')
        task = self.task(other)
        self.client.force_login(self.admin)
        response = self.client.get('/deliveries/')
        self.assertEqual(response.context['page'].paginator.count, 0)
        response = self.client.post('/deliveries/', {'pk': str(task.pk), 'reason': '不应保存'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AuditEvent.objects.filter(action='MANUAL_CONTACT').exists())

    @override_settings(DEMO_MODE=False)
    def test_aud0107_student_cannot_access_formal_contact_management(self):
        task = self.task()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/deliveries/').status_code, 403)
        self.assertEqual(self.client.post('/deliveries/', {'pk': str(task.pk), 'reason': '不应保存'}).status_code, 403)


@override_settings(DEMO_MODE=False)
class AuditDeliveryLeaseFixTests(TransactionTestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)
        self.admin = User.objects.get(username='admin')
        self.student = User.objects.get(username='student')
        self.eq = Resource.objects.get(code='EQ-OSC-01')

    def task(self, at, previous_failures=0):
        event = s.audit(self.admin, self.eq, 'AUDIT_FIX', reason='隔离租约模拟', at=at)
        s.notify(event, [self.student], 'AUDIT_FIX', '隔离通知', at)
        task = Delivery.objects.get(channel='EXTERNAL', notification__audit=event)
        for index in range(previous_failures):
            DeliveryAttempt.objects.create(delivery=task, attempt_no=index, outcome='ERROR',
                                           detail='模拟既有失败', attempted_at=at - timedelta(minutes=30-index))
        return task

    def race(self, old_failure, new_failure, old_finishes_first):
        at = timezone.now()
        task = self.task(at, 3)
        entered = {name: Event() for name in ['old', 'new']}
        release = {name: Event() for name in ['old', 'new']}
        outcomes = {}
        errors = []

        def adapter(_task):
            name = current_thread().name
            entered[name].set()
            if not release[name].wait(10):
                raise RuntimeError('模拟线程等待超时')
            if (old_failure if name == 'old' else new_failure):
                raise TimeoutError('模拟提供方错误')
            return {'accepted': True, 'receipt_id': 'simulated-' + name}

        def run(name, when):
            try:
                outcomes[name] = s.process_delivery(task.pk, at=when)
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                connections.close_all()

        threads = {name: Thread(name=name, target=run, args=(name, at + timedelta(seconds=0 if name == 'old' else 30)))
                   for name in ['old', 'new']}
        with patch('core.adapters.external_delivery', side_effect=adapter):
            try:
                threads['old'].start()
                self.assertTrue(entered['old'].wait(10))
                old_deadline = Delivery.objects.get(pk=task.pk).locked_until
                threads['new'].start()
                self.assertTrue(entered['new'].wait(10))
                new_deadline = Delivery.objects.get(pk=task.pk).locked_until
                self.assertEqual(new_deadline - old_deadline, timedelta(seconds=30))
                first = 'old' if old_finishes_first else 'new'
                release[first].set()
                threads[first].join(10)
                self.assertFalse(threads[first].is_alive())
                if first == 'old':
                    current = Delivery.objects.get(pk=task.pk)
                    self.assertEqual(current.status, 'PROCESSING')
                    self.assertEqual(current.locked_until, new_deadline)
                    self.assertEqual(current.attempts.count(), 3)
                last = 'new' if first == 'old' else 'old'
                release[last].set()
                threads[last].join(10)
                self.assertFalse(threads[last].is_alive())
            finally:
                for event in release.values():
                    event.set()
                for thread in threads.values():
                    if thread.ident:
                        thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(outcomes, {'old': False, 'new': True})
        task.refresh_from_db()
        self.assertEqual(task.status, 'FAILED' if new_failure else 'SENT')
        self.assertIsNone(task.locked_until)
        self.assertEqual(task.attempts.count(), 4)
        self.assertEqual(task.attempts.get(attempt_no=3).outcome, 'ERROR' if new_failure else 'SUCCESS')

    def test_aud0112_expiry_boundary_old_failure_cannot_replace_new_success(self):
        self.race(True, False, True)

    def test_aud0112_expiry_boundary_old_success_cannot_replace_new_failure(self):
        self.race(False, True, True)

    def test_aud0112_new_success_then_old_failure_stays_sent(self):
        self.race(True, False, False)

    def test_aud0112_new_failure_then_old_success_stays_failed(self):
        self.race(False, True, False)

    def test_aud0112_same_time_and_before_expiry_cannot_claim_twice(self):
        at = timezone.now()
        task = self.task(at)
        entered, release = Event(), Event()
        outcomes = []

        def adapter(_task):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('模拟线程等待超时')
            return {'accepted': True, 'receipt_id': 'simulated-single-owner'}

        def run():
            try:
                outcomes.append(s.process_delivery(task.pk, at=at))
            finally:
                connections.close_all()

        thread = Thread(target=run)
        with patch('core.adapters.external_delivery', side_effect=adapter) as mock:
            try:
                thread.start()
                self.assertTrue(entered.wait(10))
                self.assertFalse(s.process_delivery(task.pk, at=at))
                self.assertFalse(s.process_delivery(task.pk, at=at + timedelta(seconds=29, microseconds=999999)))
            finally:
                release.set()
                thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(mock.call_count, 1)
        self.assertEqual(outcomes, [True])
        task.refresh_from_db()
        self.assertEqual(task.status, 'SENT')
        self.assertEqual(task.attempts.count(), 1)

    def test_aud0112_unchallenged_slow_owner_can_complete(self):
        at = timezone.now() - timedelta(minutes=2)
        task = self.task(at)
        with patch('core.adapters.external_delivery', return_value={'accepted': True, 'receipt_id': 'simulated-slow-owner'}):
            self.assertTrue(s.process_delivery(task.pk, at=at))
        task.refresh_from_db()
        self.assertEqual(task.status, 'SENT')
        self.assertEqual(task.attempts.count(), 1)
        self.assertFalse(s.process_delivery(task.pk, at=timezone.now()))
