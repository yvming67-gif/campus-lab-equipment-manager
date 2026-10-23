"""Regression coverage for AUD-0075/76/77/79/80; mock data only."""
import ast
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.template import Context, Engine
from django.test import TestCase
from django.utils import timezone

from .domain_labels import NOTIFICATION_LABELS, event_label, notification_label
from .models import LabGrant, Reservation, Resource, Role, User, UserRole


class FollowupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        self.admin = User.objects.get(username="admin")
        self.sys = User.objects.get(username="sysadmin")
        self.student = User.objects.get(username="student")
        self.resource = Resource.objects.get(code="EQ-OSC-01")

    def schedule(self, count=31):
        start = (timezone.localtime()+timedelta(days=1)).replace(hour=8,minute=0,second=0,microsecond=0)
        users=User.objects.bulk_create([User(username=f"occupant{i}",school_no=f"OCC-{i:03}",display_name=f"模拟占用者{i}") for i in range(count)])
        role=Role.objects.get(code="STUDENT")
        UserRole.objects.bulk_create([UserRole(user=u,role=role) for u in users])
        bookings=[]
        for i,u in enumerate(users):
            a=start+timedelta(days=i//24,minutes=(i%24)*30)
            bookings.append(Reservation(resource=self.resource,applicant=u,starts_at=a,ends_at=a+timedelta(minutes=30),status="CONFIRMED",approval_mode="AUTO",purpose="独立模拟占用"))
        return Reservation.objects.bulk_create(bookings)

    def mini_get(self, **params):
        session=SessionStore();session["channel"]="campus-mini-v1";session["mini_user_id"]=self.student.pk;session.set_expiry(7200);session.create()
        try:return self.client.get(f"/api/mini/v1/resources/{self.resource.pk}/",params,HTTP_AUTHORIZATION="Bearer "+session.session_key)
        finally:session.delete()

    def test_resource_configuration_search_and_pagination(self):
        Resource.objects.bulk_create([Resource(lab=self.resource.lab,kind="EQUIPMENT",code=f"FIND-{i:03}",name=f"分页资源{i}") for i in range(35)])
        self.client.force_login(self.admin)
        page=self.client.get('/manage/',{'resource_q':'FIND-','resources_page':2})
        self.assertEqual(page.context['resource_page'].paginator.count,35)
        self.assertEqual(len(page.context['resources']),5)
        self.assertContains(page,'resource_q=FIND-')
        self.assertContains(page,'resources_page=1')
        self.assertContains(page,'资源档案分页')

    def test_management_filters_never_expand_lab_grants(self):
        other=User.objects.create(username="limited_admin",school_no="LIMITED",display_name="有限范围管理员")
        UserRole.objects.create(user=other,role=Role.objects.get(code="LAB_ADMIN"))
        LabGrant.objects.create(user=other,lab=self.resource.lab)
        hidden=Resource.objects.exclude(lab=self.resource.lab).first()
        self.client.force_login(other)
        page=self.client.get('/manage/',{'resource_q':hidden.code,'resource_lab':hidden.lab_id})
        self.assertEqual(page.context['resource_page'].paginator.count,0)
        self.assertNotContains(page,hidden.name)

    def test_user_configuration_search_role_and_pagination(self):
        users=User.objects.bulk_create([User(username=f"filter{i}",school_no=f"FILTER-{i:03}",display_name="用户筛选模拟") for i in range(35)])
        role=Role.objects.get(code="TEACHER")
        UserRole.objects.bulk_create([UserRole(user=u,role=role) for u in users])
        self.client.force_login(self.sys)
        page=self.client.get('/manage/',{'user_q':'FILTER-','user_role':'TEACHER','users_page':2})
        self.assertEqual(page.context['user_page'].paginator.count,35)
        self.assertEqual(len(page.context['users']),5)
        self.assertContains(page,'user_role=TEACHER')
        self.assertContains(page,'users_page=1')

    def test_students_cannot_access_management_and_lab_admin_cannot_list_users(self):
        self.client.force_login(self.student);self.assertEqual(self.client.get('/manage/').status_code,403)
        self.client.force_login(self.admin);page=self.client.get('/manage/',{'user_q':'student'})
        self.assertEqual(page.context['user_page'].paginator.count,0)
        self.assertNotContains(page,'href="/manage/grant/')

    def test_invalid_management_lab_identifiers_never_raise_server_errors(self):
        self.client.force_login(self.admin)
        for value in ['²', '9'*100, '-1', '0', '1.2']:
            with self.subTest(value=value):
                page=self.client.get('/manage/',{'resource_lab':value})
                self.assertContains(page,'请选择有效的实验室')
                self.assertEqual(page.context['resource_page'].paginator.count,0)

    def test_web_schedule_exposes_total_and_all_pages_without_applicant(self):
        bookings=self.schedule();self.client.force_login(self.student)
        first=self.client.get(f'/resources/{self.resource.pk}/')
        last=self.client.get(f'/resources/{self.resource.pk}/',{'schedule_page':3})
        self.assertEqual(first.context['schedule_page'].paginator.count,31)
        self.assertEqual(len(first.context['reservations']),15)
        self.assertEqual(list(last.context['reservations']),bookings[-1:])
        self.assertContains(first,'共31项')
        self.assertContains(first,'schedule_page=2')
        self.assertNotContains(first,'模拟占用者')

    def test_mini_schedule_exposes_total_and_next_page_without_applicant(self):
        self.schedule()
        first=self.mini_get().json()['data'];last=self.mini_get(schedule_page=2).json()['data']
        self.assertEqual((first['schedule_total'],len(first['schedule']),first['schedule_has_more']),(31,30,True))
        self.assertEqual((last['schedule_page'],len(last['schedule']),last['schedule_has_more']),(2,1,False))
        self.assertEqual(set(first['schedule'][0]),{'starts_at','ends_at','status','status_label'})

    def test_schedule_date_filter_agrees_between_clients_and_rejects_bad_date(self):
        bookings=self.schedule();day=timezone.localtime(bookings[-1].starts_at).date().isoformat()
        self.client.force_login(self.student)
        web=self.client.get(f'/resources/{self.resource.pk}/',{'schedule_day':day})
        mini=self.mini_get(schedule_day=day).json()['data']
        self.assertEqual(web.context['schedule_page'].paginator.count,7)
        self.assertEqual(mini['schedule_total'],7)
        self.assertEqual(self.mini_get(schedule_day='2026-02-30').status_code,400)
        self.assertEqual(self.mini_get(schedule_page='0').status_code,400)
        self.assertContains(self.client.get(f'/resources/{self.resource.pk}/',{'schedule_day':'bad'}),'占用日期应为年-月-日')

    def test_last_representable_calendar_date_is_a_field_error_on_both_clients(self):
        self.client.force_login(self.student)
        self.assertContains(self.client.get(f'/resources/{self.resource.pk}/',{'schedule_day':'9999-12-31'}),'占用日期无效')
        response=self.mini_get(schedule_day='9999-12-31')
        self.assertEqual(response.status_code,400)
        self.assertIn('schedule_day',response.json()['error']['fields'])

    def test_new_notification_kinds_have_specific_labels_at_every_known_constant_call(self):
        for name in ['core/services.py','core/coordination.py','core/operations.py']:
            tree=ast.parse((settings.BASE_DIR/name).read_text('utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call) or len(node.args)<3:continue
                func=node.func
                if not ((isinstance(func,ast.Name) and func.id=='notify') or (isinstance(func,ast.Attribute) and func.attr=='notify')):continue
                if isinstance(node.args[2],ast.Constant) and isinstance(node.args[2].value,str):
                    self.assertIn(node.args[2].value,NOTIFICATION_LABELS,(name,node.lineno))
        for kind in ['ARRANGEMENT_IMPACT','COORDINATE','INCIDENT_RESPONSE','REASSIGN','RECALL','RECHECK','WORK_CREATED','WORK_PROGRESS']:
            self.assertNotEqual(notification_label(kind),'业务进度')

    def test_business_timelines_translate_states_events_and_channels(self):
        engine=Engine.get_default()
        rendered=engine.from_string("{% load domain_labels %}{{ old|work_state_label }}→{{ new|work_state_label }}；{{ action|event_label }}；{{ channel|channel_label }}").render(Context({'old':'IN_PROGRESS','new':'AWAITING_ACCEPTANCE','action':'RECHECK','channel':'EXTERNAL'}))
        self.assertEqual(rendered,'处理中→待验收；复核预约；正式外部')
        self.assertEqual(engine.from_string("{% load domain_labels %}{{ value|channel_label }}").render(Context({'value':'OTHER'})),'未知投递渠道')
        self.assertEqual(event_label('COORDINATE'),'协调预约')
        self.assertIn('ev.action|event_label',(settings.BASE_DIR/'templates/booking.html').read_text('utf-8'))
        self.assertIn('e.new_status|work_state_label',(settings.BASE_DIR/'templates/work_detail.html').read_text('utf-8'))
