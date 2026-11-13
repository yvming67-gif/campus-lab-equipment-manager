"""Web experience checks: preserve scope and real booking data while guiding actions."""
from datetime import timedelta
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import User, Resource, LabGrant, ApprovalDecision
from . import services as s


class PhaseOneWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo',verbosity=0)

    def setUp(self):
        self.student=User.objects.get(username='student')
        self.other=User.objects.get(username='student2')
        self.admin=User.objects.get(username='admin')
        self.system=User.objects.get(username='sysadmin')
        self.camera=Resource.objects.get(code='EQ-CAM-01')
        self.eq=Resource.objects.get(code='EQ-OSC-01')
        self.start=(timezone.localtime()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)

    def book(self,who=None,hour=10,age=0):
        start=self.start.replace(hour=hour)
        return s.create_reservation(who or self.student,self.camera.pk,start,start+timedelta(hours=1),'仅有权审批者可见的申请用途',at=timezone.now()-timedelta(minutes=age))

    def test_catalog_does_not_call_service_status_a_booking_promise(self):
        self.client.force_login(self.student)
        r=self.client.get('/resources/',{'q':'数字示波器','kind':'EQUIPMENT'})
        self.assertContains(r,'尚未选择目标时段')
        self.assertContains(r,'服务正常')
        self.assertContains(r,'目标时段')
        self.assertIn('q=',r.context['resources'][0].detail_url)
        self.assertIn('kind=EQUIPMENT',r.context['resources'][0].detail_url)

    def test_chosen_slot_prefills_the_actual_business_form(self):
        self.client.force_login(self.student)
        end=self.start+timedelta(hours=1)
        r=self.client.get(f'/resources/{self.eq.pk}/',{'starts_at':self.start.isoformat(),'ends_at':end.isoformat(),'q':'数字','slot_day':self.start.date().isoformat()})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.context['form'].initial['starts_at'],self.start)
        self.assertEqual(r.context['form'].initial['ends_at'],end)
        self.assertIn('q=',r.context['catalog_back'])
        self.assertTrue(r.context['slots']['items'])
        self.assertTrue(all('#reservation-form' in item['url'] for item in r.context['slots']['items']))

    def test_invalid_slot_and_invalid_selection_have_recovery_text(self):
        self.client.force_login(self.student)
        r=self.client.get(f'/resources/{self.eq.pk}/',{'slot_day':'wrong','starts_at':'broken'})
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.context['slot_error'])
        self.assertContains(r,'所选时间无效')

    def test_student_room_page_has_no_actionable_suggestions(self):
        self.client.force_login(self.student)
        room=Resource.objects.filter(kind='ROOM').first()
        r=self.client.get(f'/resources/{room.pk}/')
        self.assertIsNone(r.context['slots'])
        self.assertContains(r,'整间场地仅供教师申请')

    def test_review_queue_defaults_to_longest_wait_without_expanding_scope(self):
        older=self.book(hour=11,age=25);newer=self.book(self.other,hour=10,age=2)
        self.client.force_login(self.admin)
        r=self.client.get('/bookings/',{'queue':'review'})
        ids=[b.pk for b in r.context['bookings']]
        self.assertEqual(ids[:2],[older.pk,newer.pk])
        self.assertContains(r, f"预约编号 {older.code}")
        filtered = self.client.get('/bookings/', {'queue': 'review', 'q': older.code})
        self.assertContains(filtered, f"预约编号 {older.code}")
        self.assertNotContains(filtered, f"预约编号 {newer.code}")
        r=self.client.get('/bookings/',{'queue':'review','sort':'starts'})
        self.assertEqual([b.pk for b in r.context['bookings']][:2],[newer.pk,older.pk])
        LabGrant.objects.filter(user=self.admin,lab=self.camera.lab).delete()
        r=self.client.get('/bookings/',{'queue':'review'})
        self.assertNotIn(older.pk,[b.pk for b in r.context['bookings']])
        self.assertNotContains(r, f"预约编号 {older.code}")

    def test_approval_context_is_private_and_only_a_snapshot(self):
        b=self.book(age=10);self.client.force_login(self.admin)
        page=self.client.get(f'/bookings/{b.pk}/review/')
        self.assertContains(page,'审批前核对')
        self.assertContains(page,'批准时会再次检查')
        self.assertTrue(page.context['approval_checks']['can_approve'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/bookings/{b.pk}/review/').status_code,404)
        self.client.force_login(self.system)
        self.assertEqual(self.client.get(f'/bookings/{b.pk}/review/').status_code,404)

    def test_snapshot_cannot_approve_a_slot_that_changed_after_display(self):
        b=self.book();competitor=self.book(self.other)
        self.client.force_login(self.admin)
        self.assertTrue(self.client.get(f'/bookings/{b.pk}/review/').context['approval_checks']['can_approve'])
        s.approve(self.admin,competitor.pk,True,'先确认另一申请',competitor.version)
        r=self.client.post(f'/bookings/{b.pk}/review/',{'choice':'approve','reason':'页面显示后时段变化','version':b.version})
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'时间冲突')
        b.refresh_from_db();self.assertEqual(b.status,'PENDING')
        self.assertFalse(ApprovalDecision.objects.filter(reservation=b).exists())

    def test_next_step_does_not_invent_a_handover_address(self):
        self.eq.equipment.handover_location='';self.eq.equipment.save(update_fields=['handover_location'])
        b=s.create_reservation(self.student,self.eq.pk,self.start,self.start+timedelta(hours=1),'地点未登记的模拟申请')
        self.client.force_login(self.student)
        r=self.client.get(f'/bookings/{b.pk}/')
        self.assertContains(r,'负责岗位')
        self.assertTrue(r.context['next_step']['action'])
        self.assertNotIn('联系电话',r.context['next_step']['location'])
