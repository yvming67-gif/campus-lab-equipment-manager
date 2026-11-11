"""Product navigation, day schedule and native discovery contract; mock data only."""
from datetime import date, time, timedelta
import re
import uuid
from unittest.mock import patch
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import User, Resource, Reservation, CalendarException, LabGrant, Role, UserRole, Incident, WorkOrder, AuditEvent, Notification, OpeningPeriod, Lab, RoomDetail, Accessory, Campus, Building
from . import services as s
from .catalog import filter_resources
from .forms import QueryForm, ResourceForm


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

    def test_structured_room_location_is_editable_and_shown_on_web_and_mini(self):
        room=Resource.objects.select_related('lab','room').get(code='ROOM-101')
        self.assertIn('演示楼A·1层·101室',room.room.location_label)
        initial_location=room.location_display
        self.client.force_login(self.student)
        web=self.client.get(f'/resources/{room.pk}/')
        self.assertContains(web,initial_location)
        mini=self.mini(f'resources/{room.pk}/')
        self.assertEqual(mini.status_code,200)
        self.assertEqual(mini.json()['data']['location'],initial_location)

        updated=s.save_resource(self.admin,{
            'lab':room.lab,'kind':'ROOM','code':room.code,'name':room.name,'description':room.description,
            'capacity':room.room.capacity,'building':room.room.building,'floor_label':'2层','room_number':'209室',
            'room_location':'旧位置兼容字段（位置编辑测试）',
            'space_category':room.room.space_category,'facilities':room.room.facilities,'reason':'自动化测试位置更新',
            'accessories':'',
        },room.pk)
        updated.refresh_from_db()
        self.assertEqual(updated.location_display,'演示校区 · 演示楼A · 2层 · 209室')
        self.assertEqual(updated.room.location_label,'旧位置兼容字段（位置编辑测试）')

    def test_portable_equipment_location_is_not_misrepresented_as_a_fixed_room(self):
        room=Resource.objects.select_related('room__building__campus').get(code='ROOM-101')
        detail=self.eq.equipment
        detail.handover_location='实验教学中心服务台'
        detail.access_room=room  # Simulate a legacy inconsistent record.
        detail.save(update_fields=['handover_location','access_room'])
        self.eq.refresh_from_db()

        self.assertEqual(self.eq.location_label,'交接地点')
        self.assertEqual(self.eq.location_display,'实验教学中心服务台')
        query=QueryForm({'campus':str(room.room.building.campus_id),'building':str(room.room.building_id)})
        self.assertTrue(query.is_valid(),query.errors)
        self.assertNotIn(self.eq.pk,filter_resources(Resource.objects.all(),query.cleaned_data).values_list('pk',flat=True))

        self.client.force_login(self.student)
        web=self.client.get(f'/resources/{self.eq.pk}/')
        self.assertContains(web,'交接地点：实验教学中心服务台')
        self.assertNotContains(web,'实际交接地点')
        mini=self.mini(f'resources/{self.eq.pk}/')
        payload=mini.json()['data']
        self.assertTrue(payload['portable'])
        self.assertEqual(payload['location_label'],'交接地点')
        self.assertEqual(payload['location'],'实验教学中心服务台')
        self.assertEqual(payload['handover_location'],'实验教学中心服务台')

    def test_resource_form_rejects_portable_equipment_with_fixed_room(self):
        room=Resource.objects.get(code='ROOM-101')
        form=ResourceForm({'code':'EQ-PORTABLE-INVALID','name':'可移动设备测试','kind':'EQUIPMENT',
            'lab':str(self.eq.lab_id),'category':'测试设备','risk':'NORMAL','portable':'on',
            'access_room':str(room.pk),'handover_location':'服务台'})
        self.assertFalse(form.is_valid())
        self.assertIn('access_room',form.errors)

    def test_resource_service_rejects_portable_equipment_with_fixed_room(self):
        room=Resource.objects.get(code='ROOM-101')
        with self.assertRaises(s.BusinessError):
            s.save_resource(self.admin,{'lab':self.eq.lab,'kind':'EQUIPMENT','code':'EQ-PORTABLE-INVALID',
                'name':'可移动设备测试','description':'','category':'测试设备','risk':'NORMAL',
                'portable':True,'access_room':room,'handover_location':'服务台','accessories':'',
                'reason':'验证固定关联规则'})
        self.assertFalse(Resource.objects.filter(code='EQ-PORTABLE-INVALID').exists())

    def test_fixed_equipment_with_invalid_equipment_link_has_a_safe_location_fallback(self):
        fixed=Resource.objects.select_related('equipment').get(code='EQ-ROB-FIX-01')
        fixed.equipment.access_room=self.eq  # Simulate a legacy reference to the wrong resource kind.
        fixed.equipment.save(update_fields=['access_room'])
        fixed.refresh_from_db()
        self.assertEqual(fixed.location_label,'固定关联场地')
        self.assertEqual(fixed.location_display,'固定关联场地待核实')
        self.client.force_login(self.student)
        self.assertContains(self.client.get(f'/resources/{fixed.pk}/'),'固定关联场地待核实')
        payload=self.mini(f'resources/{fixed.pk}/').json()['data']
        self.assertEqual(payload['location'],'固定关联场地待核实')

    def test_room_without_verified_location_does_not_fall_back_to_management_unit_note(self):
        room=Resource.objects.select_related('lab','room').get(code='ROOM-101')
        detail=room.room
        detail.building=None;detail.floor_label='';detail.room_number='';detail.location_label=''
        detail.save(update_fields=['building','floor_label','room_number','location_label'])
        room.refresh_from_db()
        self.assertEqual(room.location_display,'场地位置待核实')
        self.client.force_login(self.student)
        web=self.client.get(f'/resources/{room.pk}/')
        self.assertContains(web,'场地位置：场地位置待核实')
        self.assertNotContains(web,room.lab.location)
        mini=self.mini(f'resources/{room.pk}/')
        self.assertEqual(mini.json()['data']['location'],'场地位置待核实')

    def test_inactive_lab_blocks_web_booking_and_shows_the_actual_service_state(self):
        Lab.objects.filter(pk=self.eq.lab_id).update(is_active=False)
        self.eq=Resource.objects.select_related('lab').get(pk=self.eq.pk)
        self.assertEqual(s.resource_service_label(self.eq),'所属管理单元已停用')
        allowed,reason=s.resource_reservation_state(self.eq)
        self.assertFalse(allowed);self.assertEqual(reason,'所属实验室管理单元已停用，暂不可预约')
        available,label=s.availability(self.eq,self.start,self.start+timedelta(hours=1),applicant=self.student)
        self.assertFalse(available);self.assertEqual(label,reason)

        self.client.force_login(self.student)
        catalog=self.client.get('/resources/',{'q':self.eq.code})
        self.assertContains(catalog,'服务所属管理单元已停用')
        detail=self.client.get(f'/resources/{self.eq.pk}/')
        self.assertTrue(detail.context['can_apply']);self.assertFalse(detail.context['reservation_open'])
        self.assertContains(detail,reason);self.assertNotContains(detail,'保存前核对')
        response=self.client.post(f'/resources/{self.eq.pk}/',{
            'starts_at':timezone.localtime(self.start).strftime('%Y-%m-%dT%H:%M:%S'),
            'ends_at':timezone.localtime(self.start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
            'purpose':'停用边界回归','participants':'1','request_key':str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code,200);self.assertContains(response,reason)
        self.assertFalse(Reservation.objects.filter(resource=self.eq).exists())

    def test_web_booking_carries_the_review_mode_shown_on_the_resource_page(self):
        self.client.force_login(self.student)
        ordinary=self.client.get(f'/resources/{self.eq.pk}/')
        self.assertEqual(ordinary.context['form']['expected_approval_mode'].value(),'AUTO')
        restricted=Resource.objects.get(code='EQ-CAM-01')
        page=self.client.get(f'/resources/{restricted.pk}/')
        self.assertEqual(page.context['form']['expected_approval_mode'].value(),'MANUAL')

    def test_web_booking_rejects_a_review_mode_change_since_confirmation_page(self):
        self.client.force_login(self.student)
        page=self.client.get(f'/resources/{self.eq.pk}/')
        self.assertEqual(page.context['form']['expected_approval_mode'].value(),'AUTO')
        audit_count=AuditEvent.objects.count();notification_count=Notification.objects.count()
        self.eq.equipment.risk='HIGH_VALUE'
        self.eq.equipment.save(update_fields=['risk'])
        response=self.client.post(f'/resources/{self.eq.pk}/',{
            'starts_at':timezone.localtime(self.start).strftime('%Y-%m-%dT%H:%M:%S'),
            'ends_at':timezone.localtime(self.start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
            'purpose':'审批方式变化边界回归','participants':'1','request_key':str(uuid.uuid4()),
            'expected_approval_mode':'AUTO',
        })
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'预约审核方式已变化')
        self.assertFalse(Reservation.objects.filter(resource=self.eq).exists())
        self.assertEqual(AuditEvent.objects.count(),audit_count)
        self.assertEqual(Notification.objects.count(),notification_count)

    def test_web_booking_without_an_approval_mode_snapshot_remains_backward_compatible(self):
        self.client.force_login(self.student)
        response=self.client.post(f'/resources/{self.eq.pk}/',{
            'starts_at':timezone.localtime(self.start).strftime('%Y-%m-%dT%H:%M:%S'),
            'ends_at':timezone.localtime(self.start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
            'purpose':'旧表单兼容回归','participants':'1','request_key':str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code,302)
        self.assertEqual(Reservation.objects.filter(resource=self.eq,applicant=self.student).count(),1)

    def test_resource_directory_does_not_fetch_unused_accessory_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(self.student)
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get('/resources/')
        self.assertEqual(response.status_code,200)
        accessory_table=Accessory._meta.db_table.lower()
        self.assertFalse(any(accessory_table in query['sql'].lower() for query in queries))

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

    def test_role_homepages_prioritize_different_authorized_work(self):
        cases=[
            ('student','STUDENT / 个人实验安排','查找教学设备','LAB ADMIN / 今日管理'),
            ('teacher','TEACHER / 课程安排','查找可预约场地','SYSTEM ADMIN / 身份与配置'),
            ('admin','LAB ADMIN / 今日管理','处理待审核申请','SYSTEM ADMIN / 身份与配置'),
            ('tech','TECHNICIAN / 指派任务','打开我的维修任务','处理待审核申请'),
            ('director','CENTER OVERVIEW / 管理摘要','查看资源利用报表','管理用户与授权'),
            ('sysadmin','SYSTEM ADMIN / 身份与配置','管理用户与授权','处理待审核申请'),
        ]
        for username,heading,action,forbidden in cases:
            with self.subTest(role=username):
                self.client.force_login(User.objects.get(username=username))
                page=self.client.get('/')
                self.assertEqual(page.status_code,200)
                self.assertContains(page,heading)
                role_banner=re.search(r'<section class="workspace-banner role-banner[^\"]*">(.*?)</section>',page.content.decode(),re.S)
                self.assertIsNotNone(role_banner)
                self.assertIn(action,role_banner.group(1))
                self.assertNotIn(forbidden,role_banner.group(1))

    def test_seed_demo_has_varied_rooms_and_only_adds_missing_records(self):
        self.assertEqual(Lab.objects.count(),3)
        self.assertEqual(Resource.objects.filter(kind='ROOM').count(),6)
        self.assertEqual(Resource.objects.filter(kind='EQUIPMENT').count(),9)
        fixed=Resource.objects.get(code='EQ-ROB-FIX-01')
        self.assertFalse(fixed.equipment.portable)
        self.assertEqual(fixed.equipment.access_room.code,'ROOM-201')
        closed=Resource.objects.get(code='ROOM-302')
        self.assertEqual(closed.service_status,'MAINTENANCE')
        self.assertIn('演示状态示例',closed.description)

        reservation=self.book()
        room=Resource.objects.get(code='ROOM-101')
        room.name='本地自定义场地名称';room.save(update_fields=['name'])
        closed.service_status='NORMAL';closed.save(update_fields=['service_status'])
        counts=(Reservation.objects.count(),AuditEvent.objects.count())
        call_command('seed_demo',verbosity=0)
        self.assertEqual((Reservation.objects.count(),AuditEvent.objects.count()),counts)
        self.assertEqual(Resource.objects.get(code='ROOM-101').name,'本地自定义场地名称')
        self.assertEqual(Resource.objects.get(code='ROOM-302').service_status,'NORMAL')
        self.assertTrue(Reservation.objects.filter(pk=reservation.pk).exists())

    def test_seed_demo_does_not_duplicate_or_overwrite_opening_periods(self):
        default=OpeningPeriod.objects.get(lab=Lab.objects.get(code='LAB-101'),resource__isnull=True,weekday=1)
        default.ends_at=time(19)
        default.save(update_fields=['ends_at'])
        room=Resource.objects.get(code='ROOM-101')
        OpeningPeriod.objects.get(resource=room,weekday=7).delete()
        before=OpeningPeriod.objects.count()

        # Simulate deployments rerunning on different calendar days.
        for run_date in (date(2026,11,8),date(2026,11,9)):
            with patch('core.management.commands.seed_demo.timezone.localdate',return_value=run_date):
                call_command('seed_demo',verbosity=0)

        self.assertEqual(OpeningPeriod.objects.count(),before)
        default.refresh_from_db()
        self.assertEqual(default.ends_at,time(19))
        self.assertFalse(OpeningPeriod.objects.filter(resource=room,weekday=7).exists())

    def test_seed_demo_preserves_an_existing_unknown_room_location(self):
        room=Resource.objects.get(code='ROOM-101')
        detail=RoomDetail.objects.get(resource=room)
        detail.location_label=''
        detail.save(update_fields=['location_label'])

        call_command('seed_demo',verbosity=0)

        detail.refresh_from_db()
        self.assertEqual(detail.location_label,'')

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
        op.refresh_from_db()
        e=CalendarException.objects.create(lab=self.eq.lab,day=a,closed=True,actor=self.system,reason='日期交集')
        self.client.force_login(self.system)
        params={'section':'calendars','directory_lab':self.eq.lab_id,'calendar_from':a.isoformat(),'calendar_to':a.isoformat()}
        page=self.client.get('/manage/',params)
        opening_keys=list(page.context['opening_catalog_page'].paginator.object_list)
        target_key={'lab_id':op.lab_id,'resource_id':op.resource_id,'weekday':op.weekday,
                    'valid_from':op.valid_from,'valid_to':op.valid_to}
        target_index=opening_keys.index(target_key)
        params['openings_page']=target_index//page.context['opening_catalog_page'].paginator.per_page+1
        page=self.client.get('/manage/',params)
        groups=list(page.context['opening_periods'])
        matching_group=any(
            group['lab'].pk==op.lab_id and group['valid_from']==a and group['valid_to']==b
            and any(start<=op.starts_at and end>=op.ends_at for start,end in group['windows'])
            for group in groups
        )
        self.assertTrue(matching_group)
        self.assertEqual(list(page.context['calendar_exceptions']),[e])
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


class StructuredLocationDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls): call_command('seed_demo',verbosity=0)

    def test_seeded_rooms_have_explicitly_simulated_location_hierarchy(self):
        campus=Campus.objects.get(code='CAMPUS-DEMO')
        self.assertEqual(campus.name,'演示校区')
        self.assertEqual(campus.buildings.count(),3)
        room=Resource.objects.select_related('lab','room__building__campus').get(code='ROOM-101')
        self.assertEqual(room.location_display,'演示校区 · 演示楼A · 1层 · 101室')
        self.assertEqual(room.room.location_label,'演示楼A·1层·101室')

    def test_building_filter_returns_rooms_and_associated_fixed_equipment(self):
        building=Building.objects.get(code='BLD-DEMO-B')
        form=QueryForm({'campus':str(building.campus_id),'building':str(building.pk)})
        self.assertTrue(form.is_valid(),form.errors)
        rows=filter_resources(Resource.objects.all(),form.cleaned_data)
        self.assertEqual(set(rows.values_list('code',flat=True)),{'ROOM-201','ROOM-202','EQ-ROB-FIX-01'})

    def test_campus_without_building_filters_only_resources_with_verified_structure(self):
        campus=Campus.objects.get(code='CAMPUS-DEMO')
        form=QueryForm({'campus':str(campus.pk)})
        self.assertTrue(form.is_valid(),form.errors)
        rows=filter_resources(Resource.objects.all(),form.cleaned_data)
        self.assertEqual(rows.filter(kind='ROOM').count(),6)
        self.assertEqual(set(rows.filter(kind='EQUIPMENT').values_list('code',flat=True)),{'EQ-ROB-FIX-01'})

    def test_building_from_another_campus_is_rejected(self):
        campus=Campus.objects.get(code='CAMPUS-DEMO')
        foreign=Campus.objects.create(code='CAMPUS-TEST-OTHER',name='测试校区')
        building=Building.objects.create(campus=foreign,code='BLD-OTHER',name='测试楼')
        form=QueryForm({'campus':str(campus.pk),'building':str(building.pk)})
        self.assertFalse(form.is_valid())
        self.assertIn('building',form.errors)

    def test_legacy_room_location_remains_usable_without_building_guess(self):
        room=Resource.objects.get(code='ROOM-101')
        room.room.building=None;room.room.floor_label='';room.room.room_number='';room.room.save()
        room.refresh_from_db()
        self.assertEqual(room.location_display,'演示楼A·1层·101室')
