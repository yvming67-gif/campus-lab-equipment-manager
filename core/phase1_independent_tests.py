"""Independent counterexamples for phase one. No production mutations outside test DB."""
import json
import uuid
from pathlib import Path
from datetime import timedelta
from django.contrib.sessions.backends.db import SessionStore
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import User, Resource, Reservation, ReviewNote, AuditEvent, CalendarException, Incident
from . import services as s
from .experience import slot_options, approval_context
from .templatetags.domain_labels import resource_icon

BASE='/api/mini/v1'


class IndependentPhaseOneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo',verbosity=0)

    def setUp(self):
        self.student=User.objects.get(username='student')
        self.other=User.objects.get(username='student2')
        self.admin=User.objects.get(username='admin')
        self.system=User.objects.get(username='sysadmin')
        self.eq=Resource.objects.get(code='EQ-OSC-01')
        self.camera=Resource.objects.get(code='EQ-CAM-01')
        self.now=timezone.localtime().replace(hour=12,minute=15,second=0,microsecond=0)
        self.start=(timezone.localtime()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)

    def token(self,user):
        session=SessionStore();session.update({'channel':'campus-mini-v1','mini_user_id':user.pk})
        session.set_expiry(7200);session.create();return session.session_key

    def post(self,path,body,user=None):
        return self.client.post(BASE+path,json.dumps(body),content_type='application/json',
            HTTP_AUTHORIZATION='Bearer '+self.token(user or self.student))

    def detail(self,booking):
        return self.client.get(BASE+f'/bookings/{booking.pk}/',
            HTTP_AUTHORIZATION='Bearer '+self.token(self.student)).json()['data']

    def pending(self):
        return s.create_reservation(self.student,self.camera.pk,self.start,
            self.start+timedelta(hours=1),'独立审查模拟申请')

    def test_rev01_latest_supplement_survives_history_limit_and_declares_range(self):
        booking=self.pending()
        ReviewNote.objects.bulk_create([ReviewNote(reservation=booking,actor=self.admin,
            kind='REQUEST_INFO',content=f'材料核对-{i:03d}',created_at=self.now+timedelta(seconds=i))
            for i in range(101)])
        body=self.detail(booking)
        self.assertEqual(len(body['supplements']),100)
        self.assertEqual(body['supplements'][-1]['content'],'材料核对-100')
        self.assertEqual(body['supplements_total'],101)
        self.assertTrue(body['supplements_has_more'])
        self.assertNotIn('材料核对-000',[x['content'] for x in body['supplements']])

    def test_rev01_timeline_limit_is_disclosed_and_contains_latest_event(self):
        booking=self.pending()
        for i in range(101):
            s.audit(self.admin,booking,'REQUEST_INFO',reason=f'进度核对-{i:03d}',
                at=booking.created_at+timedelta(seconds=i+1))
        body=self.detail(booking)
        self.assertEqual(len(body['timeline']),100)
        self.assertEqual(body['timeline'][-1]['reason'],'进度核对-100')
        self.assertEqual(body['timeline_total'],102)
        self.assertTrue(body['timeline_has_more'])

    def test_slot_window_last_day_uses_exact_instant_not_end_of_calendar_day(self):
        day=(self.now+timedelta(days=14)).date()
        result=slot_options(self.eq,self.student,day,60,at=self.now)
        by_label={x['label']:x for x in result['items']}
        self.assertTrue(by_label['10:00—11:00']['available'])
        self.assertFalse(by_label['12:00—13:00']['available'])
        self.assertIn('窗口',by_label['12:00—13:00']['reason'])

    def test_calendar_changed_after_candidate_preview_is_not_authorization(self):
        options=slot_options(self.eq,self.student,self.start.date(),60)
        self.assertTrue(next(x for x in options['items'] if x['label']=='10:00—11:00')['available'])
        CalendarException.objects.create(lab=self.eq.lab,day=self.start.date(),closed=True,
            actor=self.system,reason='独立测试临时关闭')
        with self.assertRaisesMessage(s.BusinessError,'该日实验室关闭'):
            s.create_reservation(self.student,self.eq.pk,self.start,self.start+timedelta(hours=1),'过期预览不能授权')
        self.assertFalse(Reservation.objects.exists())

    def test_incident_retry_normalizes_equivalent_timezone_representation(self):
        moment=timezone.localtime()-timedelta(minutes=2)
        key=str(uuid.uuid4())
        body={'resource_id':self.eq.pk,'kind':'FAULT','description':'独立测试故障事实',
            'occurred_at':moment.isoformat(),'request_key':key}
        first=self.post('/incidents/',body)
        equivalent=dict(body,occurred_at=moment.astimezone(timezone.get_fixed_timezone(0)).isoformat())
        retry=self.post('/incidents/',equivalent)
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(retry.status_code,200,retry.content)
        self.assertTrue(retry.json()['data']['replayed'])
        self.assertEqual(Incident.objects.count(),1)

    def test_same_uuid_for_two_actors_returns_each_actors_own_receipt(self):
        key=str(uuid.uuid4())
        body={'resource_id':self.eq.pk,'kind':'FAULT','description':'独立测试无关联异常',
            'occurred_at':(timezone.localtime()-timedelta(minutes=2)).isoformat(),'request_key':key}
        first=self.post('/incidents/',body,self.student)
        second=self.post('/incidents/',body,self.other)
        self.assertEqual((first.status_code,second.status_code),(200,200))
        self.assertNotEqual(first.json()['data']['id'],second.json()['data']['id'])
        for actor,result in [(self.student,first),(self.other,second)]:
            lookup=self.client.get(BASE+f'/receipts/{key}/',HTTP_AUTHORIZATION='Bearer '+self.token(actor)).json()['data']
            self.assertEqual(lookup['result']['id'],result.json()['data']['id'])

    def test_approval_context_is_not_a_capability_after_resource_is_blocked(self):
        booking=self.pending()
        self.assertTrue(approval_context(booking,self.admin)['can_approve'])
        self.camera.service_status='DISABLED';self.camera.save(update_fields=['service_status'])
        with self.assertRaises(s.BusinessError):
            s.approve(self.admin,booking.pk,True,'不能依据旧检查批准',booking.version)
        booking.refresh_from_db();self.assertEqual(booking.status,'PENDING')

    def test_rev03_query_key_named_items_cannot_break_slot_filter_template(self):
        self.client.force_login(self.student)
        response=self.client.get(f'/resources/{self.eq.pk}/',{'items':'x','q':'示波器','kind':'EQUIPMENT'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'选择目标时段')
        self.assertContains(response,'name="q" value="示波器"',html=False)

    def test_rev05_every_resource_category_icon_resolves_to_a_real_symbol(self):
        sprite=(Path(settings.BASE_DIR)/'templates/icons.html').read_text('utf8')
        for resource in Resource.objects.select_related('equipment').all():
            with self.subTest(resource=resource.code):
                self.assertIn('id="'+resource_icon(resource)+'"',sprite)
