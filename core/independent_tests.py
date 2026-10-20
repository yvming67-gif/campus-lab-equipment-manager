from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from django.core.management import call_command
from django.db import connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from core.models import User,Resource,EquipmentDetail,Reservation,ApprovalDecision
from core import services as s,coordination as c


class IndependentAcceptance(TransactionTestCase):
    def setUp(self):
        call_command('seed_demo',verbosity=0)
        self.student=User.objects.get(username='student')
        self.teacher=User.objects.get(username='teacher')
        self.admin=User.objects.get(username='admin')
        self.eq=Resource.objects.get(code='EQ-OSC-01')
        self.room=Resource.objects.get(code='ROOM-101')
        self.start=timezone.localtime(timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        self.end=self.start+timedelta(hours=1)

    def fixed(self):
        EquipmentDetail.objects.filter(resource=self.eq).update(portable=False,access_room=self.room)
        self.eq=Resource.objects.get(pk=self.eq.pk)

    def book(self,user=None,resource=None,start=None,end=None):
        return s.create_reservation(user or self.student,(resource or self.eq).pk,start or self.start,end or self.end,'独立复验')

    def test_identical_missing_return_retry(self):
        b=self.book();loan=s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        item=loan.items.first();quantities={item.pk:0}
        first=s.return_equipment(self.admin,b.pk,self.eq.code,'配件缺失',missing=False,return_quantities=quantities,at=self.end)
        second=s.return_equipment(self.admin,b.pk,self.eq.code,'配件缺失',missing=False,return_quantities=quantities,at=self.end)
        self.assertEqual(first.pk,second.pk)

    def test_recheck_during_existing_pickup_grace(self):
        b=self.book()
        s.save_resource(self.admin,dict(code=self.eq.code,name=self.eq.name,kind='EQUIPMENT',lab=self.eq.lab,description='',category='示波器',risk='HIGH_VALUE',qualification=None,accessories='',reason='安全升级'),self.eq.pk)
        b.refresh_from_db()
        c.recheck(self.admin,b.pk,True,'培训安全均核验',expected=b.version,at=self.start+timedelta(minutes=5))
        result=s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start+timedelta(minutes=6))
        self.assertEqual(result.reservation_id,b.pk)

    def test_own_room_availability_matches_allowed_submit(self):
        self.fixed();b=self.book(self.teacher,self.room);s.approve(self.admin,b.pk,True,'课堂')
        allowed=s.availability(self.eq,self.start,self.end,applicant=self.teacher)[0]
        submitted=self.book(self.teacher,self.eq)
        self.assertEqual(submitted.status,'CONFIRMED')
        self.assertTrue(allowed,'查询不应把已批准本人场地误报为固定设备不可用')

    def test_overdue_fixed_equipment_blocks_next_room_start(self):
        self.fixed();b=self.book();s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.start)
        room=self.book(self.teacher,self.room,self.end,self.end+timedelta(hours=1));s.approve(self.admin,room.pk,True,'后续课程')
        with self.assertRaises(s.BusinessError):
            s.start_room(self.admin,room.pk,self.teacher.school_no,10,'前序设备未交还应阻止入场',at=self.end+timedelta(minutes=5))

    def test_lab_lock_serializes_cross_resource_conflict(self):
        self.fixed();barrier=Barrier(2)
        def worker(kind):
            try:
                barrier.wait(timeout=10)
                if kind=='equipment':
                    return s.create_reservation(User.objects.get(pk=self.student.pk),self.eq.pk,self.start,self.end,'并发固定设备').status
                booking=s.create_reservation(User.objects.get(pk=self.teacher.pk),self.room.pk,self.start,self.end,'并发场地')
                return s.approve(User.objects.get(pk=self.admin.pk),booking.pk,True,'并发审批').status
            except s.BusinessError:return 'RULE_REJECTED'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(worker,['equipment','room']))
        self.assertEqual(outcomes.count('CONFIRMED'),1,outcomes)

    def test_overdue_room_blocks_next_fixed_equipment_collection(self):
        self.fixed();room=self.book(self.teacher,self.room);s.approve(self.admin,room.pk,True,'课堂')
        b=self.book(self.student,self.eq,self.end,self.end+timedelta(hours=1))
        s.start_room(self.admin,room.pk,self.teacher.school_no,10,'核验',at=self.start)
        with self.assertRaises(s.BusinessError):
            s.checkout(self.admin,b.pk,self.student.school_no,self.eq.code,at=self.end+timedelta(minutes=5))

    def test_cancelled_booking_no_longer_requires_active_recheck(self):
        b=self.book();s.report_incident(self.admin,self.eq.pk,'FAULT','影响预约')
        b.refresh_from_db();self.assertTrue(b.review_required)
        s.cancel(self.student,b.pk,'不再借用');b.refresh_from_db()
        self.assertEqual(b.status,'CANCELLED');self.assertFalse(b.review_required)
        self.assertEqual(b.review_reason,'')

    def test_repaired_incident_is_no_longer_a_current_service_block(self):
        tech=User.objects.get(username='tech')
        i=s.report_incident(self.admin,self.eq.pk,'FAULT','管理员核实故障')
        s.verify_incident(self.admin,i.pk,'CONFIRM','已核实')
        w=s.create_work_order(self.admin,i.pk,tech.pk,'安排维修')
        s.update_work(tech,w.pk,'START','开始维修');s.update_work(tech,w.pk,'SUBMIT','复测正常')
        s.accept_work(self.admin,w.pk,True,'验收通过');i.refresh_from_db();self.eq.refresh_from_db()
        self.assertEqual((i.status,self.eq.service_status),('RESOLVED','NORMAL'))
        self.assertFalse(i.blocks_service,'结案异常不应继续在事实页宣称本异常阻断服务')

