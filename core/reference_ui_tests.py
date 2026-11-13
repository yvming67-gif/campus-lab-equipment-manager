"""Navigation must preserve filters without changing authorization or business facts."""
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
from django.core.management import call_command
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from . import ui_navigation as ui, services as s
from .models import User, Resource, Reservation, AuditEvent


class DateNavigationTests(SimpleTestCase):
    def test_china_midnight_and_month_boundary(self):
        with patch('django.utils.timezone.now',return_value=datetime(2026,1,31,16,1,tzinfo=datetime_timezone.utc)):
            nav=ui.date_navigation('schedule',QueryDict('day=2026-02-01&lab=2&page=4'),ui.SCHEDULE_KEYS,selected=date(2026,2,1))
        self.assertEqual(nav['days'][-1]['date'],date(2026,2,1))
        self.assertTrue(nav['days'][-1]['today'])
        self.assertNotIn('page=',nav['following'])
        self.assertIn('lab=2',nav['following'])

    def test_supported_year_bounds(self):
        for chosen,key in [(date(1900,1,1),'previous'),(date(2100,12,31),'following')]:
            nav=ui.date_navigation('schedule',QueryDict(),ui.SCHEDULE_KEYS,selected=chosen)
            self.assertEqual(nav[key],'')
            self.assertTrue(all(1900<=x['date'].year<=2100 for x in nav['days']))

    def test_link_replaces_multivalue_and_drops_unknown_keys(self):
        result=ui.link('bookings',QueryDict('q=old&q=new&page=7&evil=ignored'),ui.RECORD_KEYS,updates={'day':'2026-02-01'},drop=('page',))
        self.assertEqual(parse_qs(urlsplit(result).query),{'q':['new'],'day':['2026-02-01']})


class ReferenceExperienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):call_command('seed_demo',verbosity=0)

    def setUp(self):
        self.student=User.objects.get(username='student');self.other=User.objects.get(username='student2');self.admin=User.objects.get(username='admin')
        self.resource=Resource.objects.get(code='EQ-OSC-01')
        self.start=(timezone.localtime()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)

    def booking(self,user,resource=None):
        return s.create_reservation(user,(resource or self.resource).pk,self.start,self.start+timedelta(hours=1),'不应在公开日程泄露的用途')

    def test_catalog_schedule_roundtrip_keeps_full_time_query(self):
        self.client.force_login(self.student)
        query={'q':self.resource.code,'kind':'EQUIPMENT','lab':self.resource.lab_id,'starts_at':self.start.isoformat(),'ends_at':(self.start+timedelta(hours=1)).isoformat()}
        catalog=self.client.get('/resources/',query);target=catalog.context['schedule_url']
        schedule=self.client.get(target)
        self.assertEqual(len(schedule.context['rows']),1)
        self.assertEqual(schedule.context['form'].cleaned_data['day'],self.start.date())
        back=parse_qs(urlsplit(schedule.context['view_context']['url']).query)
        self.assertEqual(back['starts_at'],[self.start.isoformat()])
        self.assertEqual(back['q'],[self.resource.code])
        next_day=self.client.get(schedule.context['date_nav']['following'])
        self.assertEqual(parse_qs(urlsplit(next_day.context['view_context']['url']).query)['starts_at'],[self.start.isoformat()])

    def test_clear_one_catalog_filter_does_not_break_paired_times(self):
        self.client.force_login(self.student)
        response=self.client.get('/resources/',{'q':self.resource.code,'kind':'EQUIPMENT','starts_at':self.start.isoformat(),'ends_at':(self.start+timedelta(hours=1)).isoformat()})
        chips={x['label']:x for x in response.context['query_chips']}
        cleared=self.client.get(chips['目标时段']['url'])
        self.assertFalse(cleared.context['form'].errors)
        self.assertIsNone(cleared.context['times'])
        self.assertEqual(cleared.context['form'].cleaned_data['q'],self.resource.code)

    def test_record_context_roundtrip_does_not_filter_public_occupancy_as_a_private_queue(self):
        booking=self.booking(self.student);self.client.force_login(self.admin)
        query={'q':booking.code,'status':'CONFIRMED','lab':self.resource.lab_id,'day':self.start.date().isoformat(),'scope':'mine','sort':'starts','page':'3'}
        listing=self.client.get('/bookings/',query)
        schedule=self.client.get(listing.context['schedule_url'])
        self.assertEqual(schedule.context['form'].cleaned_data['q'],'')
        back=parse_qs(urlsplit(schedule.context['view_context']['url']).query)
        self.assertEqual(back,{k:[str(v)] for k,v in query.items()})
        self.assertContains(schedule,'公开的有效占用')
        self.assertNotContains(schedule,booking.purpose)

    def test_teacher_keeps_room_kind_when_refining_booking_list(self):
        teacher = User.objects.get(username='teacher')
        self.client.force_login(teacher)
        response = self.client.get('/bookings/', {'scope': 'mine', 'kind': 'ROOM', 'status': 'PENDING'})
        self.assertContains(response, '<input type="hidden" name="kind" value="ROOM">', html=True)

    def test_foreign_session_filter_context_is_not_restored_or_echoed(self):
        self.client.force_login(self.other)
        response=self.client.get('/schedule/',{'origin':'records','record_user':self.student.pk,'record_q':'另一个账号的查询上下文','record_status':'PENDING','day':self.start.date().isoformat()})
        self.assertEqual(response.context['view_context']['hidden'],[])
        self.assertNotContains(response,'另一个账号的查询上下文')
        self.assertNotIn('record_q',response.context['filters'])
        self.assertNotIn('record_q',response.context['previous'])

    def test_record_totals_count_only_the_users_visible_scope(self):
        own=self.booking(self.student);self.booking(self.other,Resource.objects.get(code='EQ-OSC-02'))
        self.client.force_login(self.student)
        response=self.client.get('/bookings/',{'q':own.code})
        self.assertEqual(response.context['query_total'],1)
        self.assertEqual(response.context['query_count'],1)
        self.assertContains(response,'本人预约记录')

    def test_invalid_resource_filter_does_not_display_unfiltered_results(self):
        self.client.force_login(self.student)
        response=self.client.get('/resources/',{'starts_at':'not-a-date'})
        self.assertTrue(response.context['query_invalid'])
        self.assertEqual(response.context['page'].paginator.count,0)
        self.assertNotContains(response,'选择时段 ↗')

    def test_invalid_booking_date_does_not_display_unfiltered_results(self):
        self.booking(self.student);self.client.force_login(self.student)
        response=self.client.get('/bookings/',{'day':'2026-02-31'})
        self.assertTrue(response.context['query_invalid'])
        self.assertEqual(response.context['query_count'],0)
        self.assertContains(response,'日期格式应为有效的年-月-日')

    def test_navigation_is_read_only_and_staff_density_is_not_a_student_action(self):
        self.booking(self.student);self.client.force_login(self.student)
        before=(Reservation.objects.count(),AuditEvent.objects.count())
        response=self.client.get('/bookings/',{'day':self.start.date().isoformat()})
        self.assertNotContains(response,'data-density-toggle')
        self.client.get(response.context['date_nav']['following'])
        self.assertEqual(before,(Reservation.objects.count(),AuditEvent.objects.count()))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/bookings/'),'data-density-toggle')
