import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from django.test import TestCase, Client, override_settings
from django.core.cache import cache
from django.core.management import call_command
from django.contrib.sessions.models import Session
from django.utils import timezone
from .models import User, Resource, Reservation, Notification, AuditEvent, UserRole, Policy
from . import services as s

BASE = '/api/mini/v1'


class MiniApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_dir = tempfile.TemporaryDirectory(prefix='campus-mini-test-')
        assert Path(cls.private_dir.name).resolve().parent == Path(tempfile.gettempdir()).resolve()
        cls.addClassCleanup(cls.private_dir.cleanup)
        config = override_settings(LOCAL=Path(cls.private_dir.name))
        config.enable(); cls.addClassCleanup(config.disable)

    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.student = User.objects.get(username='student')
        self.other = User.objects.get(username='student2')
        self.admin = User.objects.get(username='admin')
        self.eq = Resource.objects.get(code='EQ-OSC-01')
        self.restricted = Resource.objects.get(code='EQ-ROB-01')
        self.room = Resource.objects.get(code='ROOM-101')
        self.start = timezone.localtime(timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        self.end = self.start+timedelta(hours=1)

    def login(self, username='student', password='Demo@2026'):
        r = self.client.post(BASE+'/auth/login/', json.dumps({'username':username,'password':password}), content_type='application/json')
        self.assertEqual(r.status_code,200,r.content)
        return r.json()['data']['token']

    def get(self, path, token=None, data=None):
        return self.client.get(BASE+path, data or {}, HTTP_AUTHORIZATION='Bearer '+(token or self.login()))

    def post(self, path, data, token=None):
        return self.client.post(BASE+path, json.dumps(data), content_type='application/json', HTTP_AUTHORIZATION='Bearer '+(token or self.login()))

    def values(self, resource=None, **extra):
        value={'resource_id':(resource or self.eq).pk,'starts_at':self.start.strftime('%Y-%m-%dT%H:%M:%S'),'ends_at':self.end.strftime('%Y-%m-%dT%H:%M:%S'),'purpose':'模拟课程实验','participants':1,'request_key':str(uuid.uuid4())}
        value.update(extra);return value

    def test_login_returns_scoped_expiring_session_without_cookie(self):
        token=self.login();session=Session.objects.get(session_key=token)
        self.assertEqual(session.get_decoded()['channel'],'campus-mini-v1')
        self.assertNotIn('_auth_user_id',session.get_decoded())
        self.assertLessEqual((session.expire_date-timezone.now()).total_seconds(),7200)
        self.assertFalse(self.student.has_usable_password())

    def test_incorrect_login_rejected(self):
        r=self.client.post(BASE+'/auth/login/',json.dumps({'username':'student','password':'wrong'}),content_type='application/json')
        self.assertEqual(r.status_code,401);self.assertNotIn('token',r.json().get('data',{}))

    def test_login_throttle(self):
        for _ in range(6):
            self.client.post(BASE+'/auth/login/',json.dumps({'username':'student','password':'wrong'}),content_type='application/json')
        r=self.client.post(BASE+'/auth/login/',json.dumps({'username':'student','password':'Demo@2026'}),content_type='application/json')
        self.assertEqual(r.status_code,429)

    def test_management_identity_is_not_client_identity(self):
        r=self.client.post(BASE+'/auth/login/',json.dumps({'username':'admin','password':'Demo@2026'}),content_type='application/json')
        self.assertEqual(r.status_code,403)

    @override_settings(DEMO_MODE=False)
    def test_mock_login_disabled_outside_demo(self):
        self.assertEqual(self.client.post(BASE+'/auth/login/',{},content_type='application/json').status_code,503)

    def test_cookie_session_does_not_authorize_api(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(BASE+'/me/').status_code,401)

    def test_expired_session_rejected(self):
        token=self.login();Session.objects.filter(session_key=token).update(expire_date=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.get('/me/',token).status_code,401)

    def test_revoked_role_invalidates_access(self):
        token=self.login();UserRole.objects.filter(user=self.student).delete()
        self.assertEqual(self.get('/me/',token).status_code,403)

    def test_deactivated_account_invalidates_access(self):
        token=self.login();User.objects.filter(pk=self.student.pk).update(is_active=False)
        self.assertEqual(self.get('/me/',token).status_code,401)

    def test_logout_revokes_token(self):
        token=self.login();self.assertEqual(self.post('/auth/logout/',{},token).status_code,200)
        self.assertEqual(self.get('/me/',token).status_code,401)

    def test_api_does_not_remove_web_csrf_protection(self):
        self.assertEqual(self.client.post('/login/',{}).status_code,403)

    def test_response_is_not_cacheable_and_has_request_id(self):
        r=self.get('/me/');self.assertEqual(r['Cache-Control'],'no-store');uuid.UUID(r.json()['request_id'])

    def test_wrong_http_method_returns_json(self):
        r=self.get('/auth/login/');self.assertEqual(r.status_code,405);self.assertEqual(r['Allow'],'POST')

    def test_resource_search_and_lab_filter(self):
        r=self.get('/resources/',data={'q':'示波器','kind':'EQUIPMENT','lab':self.eq.lab_id})
        self.assertEqual(r.status_code,200)
        self.assertTrue(all('示波器' in x['name'] for x in r.json()['data']['items']))

    def test_invalid_query_time_reports_field_errors(self):
        r=self.get('/resources/',data={'starts_at':'not-a-time'})
        self.assertEqual(r.status_code,400);self.assertIn('starts_at',r.json()['error']['fields'])

    def test_resource_detail_hides_other_applicant(self):
        s.create_reservation(self.other,self.eq.pk,self.start,self.end,'其他人的用途')
        r=self.get('/resources/'+str(self.eq.pk)+'/',data={'starts_at':self.start.strftime('%Y-%m-%dT%H:%M'),'ends_at':self.end.strftime('%Y-%m-%dT%H:%M')})
        data=r.json()['data'];self.assertFalse(data['available']);self.assertEqual(len(data['schedule']),1)
        self.assertNotIn('applicant',json.dumps(data));self.assertNotIn('其他人的用途',json.dumps(data,ensure_ascii=False))

    def test_ordinary_booking_and_duplicate_share_one_record(self):
        token=self.login();values=self.values();a=self.post('/bookings/',values,token);b=self.post('/bookings/',values,token)
        self.assertEqual(a.status_code,200,a.content);self.assertEqual(a.json()['data']['status'],'CONFIRMED')
        self.assertEqual(a.json()['data']['id'],b.json()['data']['id']);self.assertEqual(Reservation.objects.count(),1)

    def test_reusing_key_with_different_payload_rejected(self):
        token=self.login();values=self.values();self.post('/bookings/',values,token);values['purpose']='不同用途'
        self.assertEqual(self.post('/bookings/',values,token).status_code,409);self.assertEqual(Reservation.objects.count(),1)

    def test_restricted_requests_do_not_occupy_until_web_approval(self):
        token=self.login();a=self.post('/bookings/',self.values(self.restricted),token).json()['data']
        teacher=self.login('teacher');b=self.post('/bookings/',self.values(self.restricted),teacher).json()['data']
        self.assertEqual((a['status'],b['status']),('PENDING','PENDING'))
        web=Client();web.force_login(self.admin)
        r=web.post(f"/bookings/{a['id']}/review/",{'choice':'approve','version':a['version'],'reason':'模拟培训与用途已核对'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.get(f"/bookings/{a['id']}/",token).json()['data']['status'],'CONFIRMED')
        self.assertTrue(any('已确认' in n['content'] for n in self.get('/notifications/',token).json()['data']['items']))
        with self.assertRaises(s.BusinessError):s.approve(self.admin,b['id'],True,'第二个批准存在冲突')

    def test_teacher_room_pending_and_student_room_blocked(self):
        r=self.post('/bookings/',self.values(self.room),self.login('teacher'))
        self.assertEqual(r.json()['data']['status'],'PENDING')
        self.assertEqual(self.post('/bookings/',self.values(self.room)).status_code,409)

    def test_conflict_rejected_without_extra_business_records(self):
        s.create_reservation(self.other,self.eq.pk,self.start,self.end,'已确认占用')
        r=self.post('/bookings/',self.values());self.assertEqual(r.status_code,409);self.assertEqual(Reservation.objects.count(),1)

    def test_maintenance_blocks_api_booking(self):
        Resource.objects.filter(pk=self.eq.pk).update(service_status='MAINTENANCE')
        self.assertEqual(self.post('/bookings/',self.values()).status_code,409)

    def test_missing_resource_rejected(self):
        self.assertEqual(self.post('/bookings/',self.values(resource_id=999999)).status_code,404)

    def test_invalid_time_and_missing_idempotency_fields(self):
        for extra in [{'starts_at':'wrong'},{'ends_at':self.start.strftime('%Y-%m-%dT%H:%M')},{'request_key':''}]:
            self.assertEqual(self.post('/bookings/',self.values(**extra)).status_code,400)
        self.assertFalse(Reservation.objects.exists())

    def test_actor_and_status_injection_not_accepted(self):
        self.assertEqual(self.post('/bookings/',self.values(applicant_id=self.other.pk,status='CONFIRMED')).status_code,400)
        self.assertFalse(Reservation.objects.exists())

    def test_nested_json_and_non_object_are_rejected(self):
        self.assertEqual(self.post('/bookings/',{'purpose':{'a':1}}).status_code,400)
        self.assertEqual(self.post('/bookings/',[]).status_code,400)

    def test_non_json_body_is_rejected(self):
        r=self.client.post(BASE+'/bookings/',self.values(),HTTP_AUTHORIZATION='Bearer '+self.login())
        self.assertEqual(r.status_code,415)

    def test_future_quota_matches_web_service(self):
        token=self.login()
        for resource in Resource.objects.filter(kind='EQUIPMENT',equipment__risk='NORMAL')[:3]:
            self.assertEqual(self.post('/bookings/',self.values(resource),token).status_code,200)
        self.assertEqual(self.post('/bookings/',self.values(self.restricted),token).status_code,409)

    def test_foreign_booking_is_neither_visible_nor_cancellable(self):
        booking=s.create_reservation(self.other,self.eq.pk,self.start,self.end,'其他人')
        token=self.login();self.assertEqual(self.get('/bookings/',token).json()['data']['total'],0)
        self.assertEqual(self.get(f'/bookings/{booking.pk}/',token).status_code,404)
        self.assertEqual(self.post(f'/bookings/{booking.pk}/cancel/',{'reason':'非法取消','version':1},token).status_code,404)

    def test_cancel_releases_slot_and_creates_audit(self):
        token=self.login();item=self.post('/bookings/',self.values(),token).json()['data']
        r=self.post(f"/bookings/{item['id']}/cancel/",{'reason':'课程调整','version':item['version']},token)
        self.assertEqual(r.json()['data']['status'],'CANCELLED')
        self.assertTrue(AuditEvent.objects.filter(action='CANCEL',entity_id=item['id']).exists())
        self.assertEqual(s.create_reservation(self.other,self.eq.pk,self.start,self.end,'再次预约').status,'CONFIRMED')

    def test_stale_cancel_version_rejected(self):
        token=self.login();item=self.post('/bookings/',self.values(),token).json()['data']
        self.assertEqual(self.post(f"/bookings/{item['id']}/cancel/",{'reason':'过期页面','version':99},token).status_code,409)

    def test_started_booking_cannot_be_cancelled(self):
        token=self.login();item=self.post('/bookings/',self.values(),token).json()['data']
        Reservation.objects.filter(pk=item['id']).update(starts_at=timezone.now()-timedelta(minutes=1))
        self.assertEqual(self.post(f"/bookings/{item['id']}/cancel/",{'reason':'已开始','version':1},token).status_code,409)

    def test_only_owned_notifications_can_be_read(self):
        booking=s.create_reservation(self.other,self.eq.pk,self.start,self.end,'其他人')
        other_notice=Notification.objects.filter(recipient=self.other).first()
        self.assertEqual(self.post(f'/notifications/{other_notice.pk}/read/',{}).status_code,404)
        self.assertEqual(self.get('/notifications/').json()['data']['total'],0)

    def test_notification_read_all_is_idempotent_and_scoped(self):
        token=self.login();self.post('/bookings/',self.values(),token)
        self.assertGreater(self.get('/notifications/',token).json()['data']['unread'],0)
        self.post('/notifications/read-all/',{},token)
        self.assertEqual(self.get('/notifications/',token).json()['data']['unread'],0)
        self.assertEqual(self.post('/notifications/read-all/',{},token).json()['data']['updated'],0)

    def test_detail_timeline_includes_submit(self):
        token=self.login();item=self.post('/bookings/',self.values(),token).json()['data']
        timeline=self.get(f"/bookings/{item['id']}/",token).json()['data']['timeline']
        self.assertEqual(timeline[0]['label'],'提交申请')

    def test_bad_page_and_status_return_validation_error(self):
        self.assertEqual(self.get('/bookings/',data={'status':'UNKNOWN'}).status_code,400)
        self.assertEqual(self.get('/resources/',data={'page':'-1'}).status_code,400)
