"""Product navigation, day schedule and native discovery contract; mock data only."""
from datetime import timedelta
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import User, Resource, Reservation, CalendarException, LabGrant, Role, UserRole, Incident, WorkOrder, AuditEvent, OpeningPeriod, Lab
from . import services as s


class ProductTests(TestCase):
    @classmethod
    def setUpTestData(cls): call_command('seed_demo',verbosity=0)

    def setUp(self):
        self.student=User.objects.get(username='student');self.other=User.objects.get(username='student2')
        self.admin=User.objects.get(username='admin');self.system=User.objects.get(username='sysadmin')
        self.eq=Resource.objects.get(code='EQ-OSC-01')
        self.start=(timezone.localtime()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)

    def book(self, resource=None, user=None):
        return s.create_reservation(user or self.student,(resource or self.eq).pk,self.start,self.start+timedelta(hours=1),'非公开用途材料')

    def mini(self,path,**params):
        session=SessionStore();session.update({'channel':'campus-mini-v1','mini_user_id':self.student.pk});session.set_expiry(7200);session.create()
        try:return self.client.get('/api/mini/v1/'+path,params,HTTP_AUTHORIZATION='Bearer '+session.session_key)
        finally:session.delete()

    def test_schedule_requires_login_and_is_anonymized(self):
        b=self.book();self.assertEqual(self.client.get('/schedule/').status_code,302)
        self.client.force_login(self.other)
        page=self.client.get('/schedule/',{'day':self.start.date().isoformat(),'q':self.eq.code})
        self.assertEqual(len(page.context['rows']),1)
        self.assertEqual(len(page.context['rows'][0]['events']),1)
        for private in [b.code,b.purpose,self.student.display_name]:self.assertNotContains(page,private)
        self.assertContains(page,'10:00');self.assertContains(page,'已确认')

    def test_schedule_excludes_pending_and_respects_closure_override(self):
        b=self.book(Resource.objects.get(code='EQ-CAM-01'))
        CalendarException.objects.create(lab=b.resource.lab,day=self.start.date(),closed=True,actor=self.system,reason='模拟闭馆')
        self.client.force_login(self.student)
        page=self.client.get('/schedule/',{'day':self.start.date().isoformat(),'q':b.resource.code})
        row=page.context['rows'][0]
        self.assertEqual((row['events'],row['windows']),([],[]));self.assertTrue(row['exception'])

    def test_schedule_carries_unfinished_use_without_fabricating_end(self):
        b=self.book();s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start+timedelta(minutes=2))
        self.client.force_login(self.other)
        page=self.client.get('/schedule/',{'day':(self.start+timedelta(days=2)).date().isoformat(),'q':self.eq.code})
        event=page.context['rows'][0]['events'][0]
        self.assertTrue(event['carry']);self.assertEqual(event['width'],0)
        self.assertContains(page,'实际使用未结束')
        self.assertEqual(self.client.get('/schedule/',{'day':(self.start-timedelta(days=1)).date().isoformat(),'q':self.eq.code}).context['rows'][0]['events'],[])

    def test_schedule_invalid_date_and_lab_never_throw(self):
        self.client.force_login(self.student)
        for params in [{'day':'9999-12-31'},{'day':'2026-02-30'},{'lab':'9'*100},{'kind':'INVALID'}]:
            with self.subTest(params=params):
                page=self.client.get('/schedule/',params);self.assertEqual(page.status_code,200);self.assertFalse(page.context['form'].is_valid());self.assertEqual(page.context['rows'],[])

    def test_management_role_and_lab_scope(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/manage/',{'section':'access'}).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/manage/',{'section':'people'}).status_code,403)
        page=self.client.get('/manage/',{'section':'calendars'})
        self.assertSetEqual(set(page.context['directory_labs'].values_list('pk',flat=True)),set(LabGrant.objects.filter(user=self.admin).values_list('lab_id',flat=True)))
        self.client.force_login(self.system)
        self.assertContains(self.client.get('/manage/',{'section':'access'}),'角色职责与授权边界')

    def test_review_queue_cannot_expand_authorization_or_include_self(self):
        restricted=Resource.objects.get(code='EQ-CAM-01');b=self.book(restricted)
        limited=User.objects.create(username='product_admin',school_no='PRODUCT_ADMIN',display_name='产品管理员')
        UserRole.objects.create(user=limited,role=Role.objects.get(code='LAB_ADMIN'))
        self.client.force_login(limited)
        self.assertEqual(self.client.get('/bookings/',{'queue':'review'}).context['page'].paginator.count,0)
        LabGrant.objects.create(user=limited,lab=restricted.lab)
        page=self.client.get('/bookings/',{'queue':'review'})
        self.assertEqual(list(page.context['bookings']),[b]);self.assertContains(page,'name="queue" value="review"')
        self.client.force_login(self.student);self.assertEqual(self.client.get('/bookings/',{'queue':'review'}).status_code,403)

    def test_workbench_actions_and_mini_upcoming_have_same_record(self):
        b=self.book();self.client.force_login(self.admin)
        self.assertContains(self.client.get('/'),'我的工作队列')
        data=self.mini('me/').json()['data'];self.assertEqual([x['id'] for x in data['upcoming']],[b.pk])
        self.assertIn('qualifications',data);self.assertEqual(data['stats']['future_equipment'],1)
        self.client.force_login(self.other);self.assertNotContains(self.client.get('/'),b.code)

    def test_personal_cards_filter_to_self_for_multi_role_manager(self):
        self.book();self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/bookings/',{'scope':'mine','status':'CONFIRMED'}).context['page'].paginator.count,0)
        self.assertEqual(self.client.get('/bookings/',{'status':'CONFIRMED'}).context['page'].paginator.count,1)

    def test_technician_sees_submitted_work_but_cannot_accept_it(self):
        tech=User.objects.get(username='tech')
        i=Incident.objects.create(resource=self.eq,reporter=self.student,kind='FAULT',description='模拟异常',occurred_at=timezone.now())
        w=WorkOrder.objects.create(incident=i,created_by=self.admin,assigned_to=tech,status='AWAITING_ACCEPTANCE')
        self.client.force_login(tech)
        self.assertContains(self.client.get('/work/',{'status':'AWAITING_ACCEPTANCE'}),w.code)
        self.assertEqual(self.client.get('/work/',{'queue':'acceptance'}).status_code,403)

    def test_former_repair_performer_is_not_in_independent_acceptance_queue(self):
        tech=User.objects.get(username='tech')
        i=Incident.objects.create(resource=self.eq,reporter=self.student,kind='FAULT',description='维修历史样例',occurred_at=timezone.now())
        w=WorkOrder.objects.create(incident=i,created_by=self.admin,assigned_to=tech,status='AWAITING_ACCEPTANCE')
        s.audit(self.admin,w,'PROGRESS',new={'status':'IN_PROGRESS'},reason='改派前曾参与维修')
        self.client.force_login(self.admin)
        tasks=self.client.get('/').context['tasks']
        self.assertEqual(next(t['count'] for t in tasks if t['name']=='维修待验收'),0)
        self.assertNotContains(self.client.get('/work/',{'queue':'acceptance'}),w.code)
        self.assertContains(self.client.get('/work/',{'status':'AWAITING_ACCEPTANCE'}),w.code)

    def test_recheck_queue_excludes_self_and_incident_queue_matches_scope(self):
        UserRole.objects.create(user=self.admin,role=Role.objects.get(code='TEACHER'))
        b=self.book(user=self.admin);Reservation.objects.filter(pk=b.pk).update(review_required=True)
        # Preserve a cross-scope self-reported incident without granting management rights.
        LabGrant.objects.filter(user=self.admin).exclude(lab=self.eq.lab).delete()
        other_resource=Resource.objects.exclude(lab=self.eq.lab).first()
        i=Incident.objects.create(resource=other_resource,reporter=self.admin,kind='FAULT',description='跨范围报告',occurred_at=timezone.now())
        self.client.force_login(self.admin)
        page=self.client.get('/');self.assertEqual(next(t['count'] for t in page.context['tasks'] if t['name']=='待复核承诺'),0)
        self.assertEqual(self.client.get('/bookings/',{'queue':'recheck'}).context['page'].paginator.count,0)
        self.assertNotContains(self.client.get('/incidents/',{'queue':'managed','status':'PENDING'}),i.code)
        self.assertContains(self.client.get('/incidents/',{'status':'PENDING'}),i.code)

    def test_native_time_discovery_reuses_authoritative_availability(self):
        self.book()
        result=self.mini('resources/',q=self.eq.code,starts_at=self.start.isoformat(),ends_at=(self.start+timedelta(hours=1)).isoformat()).json()['data']
        self.assertFalse(result['items'][0]['available']);self.assertIn('占用',result['items'][0]['available_label'])

    def test_unread_filter_returns_only_owned_unread_and_preserves_total(self):
        self.book();all_data=self.mini('notifications/').json()['data'];self.assertGreater(all_data['unread'],0)
        item=self.student.notifications.first();item.read_at=timezone.now();item.save(update_fields=['read_at'])
        data=self.mini('notifications/',unread='1').json()['data']
        self.assertTrue(all(x['read_at'] is None for x in data['items']));self.assertEqual(data['unread'],data['total'])

    def test_calendar_catalog_pages_beyond_old_limits_and_preserves_filters(self):
        today=timezone.localdate()
        for i in range(105):
            begin=today+timedelta(days=7*(i+1))
            OpeningPeriod.objects.create(lab=self.eq.lab,weekday=1,starts_at='08:00',ends_at='20:00',valid_from=begin,valid_to=begin+timedelta(days=6))
        CalendarException.objects.bulk_create([CalendarException(lab=self.eq.lab,day=today+timedelta(days=i+1),closed=True,actor=self.system,reason='分页例外') for i in range(35)])
        self.client.force_login(self.system)
        params={'section':'calendars','directory_q':self.eq.lab.code,'openings_page':4,'exceptions_page':2}
        page=self.client.get('/manage/',params)
        self.assertGreater(page.context['opening_catalog_page'].paginator.count,100)
        self.assertEqual(page.context['opening_catalog_page'].number,4)
        self.assertEqual(page.context['exception_catalog_page'].paginator.count,35)
        self.assertEqual(len(page.context['calendar_exceptions']),15)
        self.assertContains(page,'directory_q='+self.eq.lab.code)
        self.assertContains(page,'section=calendars')
        self.assertContains(page,'openings_page=3');self.assertContains(page,'单日例外分页')

    def test_calendar_catalog_date_intersection_and_invalid_range(self):
        today=timezone.localdate();a=today+timedelta(days=30);b=a+timedelta(days=7)
        op=OpeningPeriod.objects.create(lab=self.eq.lab,weekday=1,starts_at='08:00',ends_at='20:00',valid_from=a,valid_to=b)
        e=CalendarException.objects.create(lab=self.eq.lab,day=a,closed=True,actor=self.system,reason='日期交集')
        self.client.force_login(self.system)
        params={'section':'calendars','directory_lab':self.eq.lab_id,'calendar_from':a.isoformat(),'calendar_to':a.isoformat()}
        page=self.client.get('/manage/',params)
        self.assertIn(op,list(page.context['opening_periods']));self.assertEqual(list(page.context['calendar_exceptions']),[e])
        params['calendar_to']=(a-timedelta(days=1)).isoformat()
        invalid=self.client.get('/manage/',params)
        self.assertContains(invalid,'结束日期不得早于起始日期');self.assertEqual(invalid.context['opening_catalog_page'].paginator.count,0)

    def test_calendar_directory_pagination_cannot_expand_management_scope(self):
        for i in range(15):Lab.objects.create(code=f'CAT-{i:02}',name=f'目录实验室{i}',location='模拟地点')
        self.client.force_login(self.system)
        page=self.client.get('/manage/',{'section':'calendars','directory_q':'CAT-','labs_page':2})
        self.assertEqual(page.context['lab_catalog_page'].paginator.count,15)
        self.assertEqual(len(page.context['directory_labs']),3)
        limited=User.objects.create(username='calendar_limited',school_no='CAL_LIMIT',display_name='范围管理员')
        UserRole.objects.create(user=limited,role=Role.objects.get(code='LAB_ADMIN'));LabGrant.objects.create(user=limited,lab=self.eq.lab)
        hidden=Lab.objects.exclude(pk=self.eq.lab_id).first();self.client.force_login(limited)
        page=self.client.get('/manage/',{'section':'calendars','directory_lab':hidden.pk})
        self.assertFalse(page.context['catalog_form'].is_valid());self.assertEqual(page.context['lab_catalog_page'].paginator.count,0)
        self.assertNotContains(page,hidden.name)
