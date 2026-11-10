"""Room-level weekly schedules remain consistent across web, mini and domain rules."""
import json
from datetime import datetime, time, timedelta

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from . import services
from .experience import slot_options
from .models import CalendarException, Lab, LabGrant, OpeningPeriod, Resource, Role, User, UserRole
from .opening_calendar import weekly_periods, weekly_schedule
from .services import TZ


class RoomOpeningCalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.teacher=User.objects.get(username="teacher")
        cls.student=User.objects.get(username="student")
        cls.admin=User.objects.get(username="admin")
        cls.system=User.objects.get(username="sysadmin")
        cls.room_short=Resource.objects.select_related("lab","room").get(code="ROOM-101")
        cls.room_late=Resource.objects.select_related("lab","room").get(code="ROOM-102")

    def day_and_time(self, hour, day_offset=2):
        day=timezone.localdate()+timedelta(days=day_offset)
        start=timezone.make_aware(datetime.combine(day,time(hour)),TZ)
        return day,start

    def test_room_schedule_overrides_lab_default_in_web_and_mini_detail(self):
        day,start=self.day_and_time(10)
        self.client.force_login(self.teacher)
        web=self.client.get(f"/resources/{self.room_short.pk}/")
        self.assertContains(web,"近期开放时段")
        self.assertIn("08:00—18:00",web.content.decode("utf-8"))
        short=next(x for x in weekly_schedule(self.room_short) if x["date"]==day.isoformat())
        late=next(x for x in weekly_schedule(self.room_late) if x["date"]==day.isoformat())
        self.assertEqual(short["ranges"],["08:00—18:00"])
        self.assertEqual(late["ranges"],["09:00—20:00"])

        client=Client()
        login=client.post("/api/mini/v1/auth/login/",json.dumps({"username":"teacher","password":"Demo@2026"}),content_type="application/json")
        token=login.json()["data"]["token"]
        response=client.get(f"/api/mini/v1/resources/{self.room_short.pk}/",HTTP_AUTHORIZATION="Bearer "+token)
        self.assertEqual(response.status_code,200)
        data=response.json()["data"]
        self.assertEqual(data["opening_schedule"],weekly_schedule(self.room_short))

    def test_room_specific_hours_control_reservations_and_fixed_equipment_dependency(self):
        day,start=self.day_and_time(19)
        with self.assertRaisesRegex(services.BusinessError,"开放时间"):
            services.create_reservation(self.teacher,self.room_short.pk,start,start+timedelta(hours=1),"场地关闭时段测试")
        late_booking=services.create_reservation(self.teacher,self.room_late.pk,start,start+timedelta(hours=1),"场地延长时段测试")
        self.assertEqual(late_booking.status,"PENDING")

        equipment=Resource.objects.select_related("equipment","lab").get(code="EQ-OSC-01")
        equipment.equipment.portable=False
        equipment.equipment.access_room=self.room_short
        equipment.equipment.save(update_fields=["portable","access_room"])
        with self.assertRaisesRegex(services.BusinessError,"开放时间"):
            services.create_reservation(self.student,equipment.pk,start,start+timedelta(hours=1),"固定设备需遵守关联场地时段")

    def test_lab_admin_can_change_only_authorized_room_schedule(self):
        lab=Lab.objects.get(code="LAB-101")
        effective=timezone.localdate()+timedelta(days=1)
        values={"lab":lab,"resource":self.room_short,"weekday":effective.isoweekday(),
                "starts_at":time(8),"ends_at":time(17),"effective":effective,"reason":"课程演示测试版本"}
        result=services.change_opening(self.admin,values)
        self.assertEqual(result.resource_id,self.room_short.pk)
        self.assertEqual(result.lab_id,lab.pk)

        values["resource"]=None
        with self.assertRaisesRegex(services.BusinessError,"默认开放日历仅系统管理员"):
            services.change_opening(self.admin,values)

        other=Resource.objects.get(code="ROOM-201")
        values.update(lab=lab,resource=other)
        with self.assertRaisesRegex(services.BusinessError,"所选实验室内"):
            services.change_opening(self.admin,values)

    def test_lab_admin_form_scopes_rooms_and_requires_specific_resource(self):
        limited=User.objects.create(username="single_lab_opening",school_no="OPEN-01",display_name="单实验室管理员")
        UserRole.objects.create(user=limited,role=Role.objects.get(code="LAB_ADMIN"))
        lab=Lab.objects.get(code="LAB-101")
        LabGrant.objects.create(user=limited,lab=lab)
        self.client.force_login(limited)
        response=self.client.get("/manage/opening/")
        self.assertEqual(response.status_code,200)
        form=response.context["form"]
        self.assertTrue(form.fields["resource"].required)
        self.assertEqual(set(form.fields["resource"].queryset.values_list("lab_id",flat=True)),{lab.pk})
        self.assertFalse(form.fields["lab"].queryset.filter(code="LAB-202").exists())

    def test_room_schedule_change_cannot_make_an_existing_confirmed_booking_outside_hours(self):
        effective,start=self.day_and_time(17,day_offset=1)
        booking=services.create_reservation(self.teacher,self.room_short.pk,start,start+timedelta(hours=1),"开放日历变更冲突检查")
        services.approve(self.admin,booking.pk,True,"课程演示审核")
        values={"lab":self.room_short.lab,"resource":self.room_short,"weekday":effective.isoweekday(),
                "starts_at":time(8),"ends_at":time(16),"effective":effective,"reason":"不得覆盖已有承诺测试"}
        with self.assertRaisesRegex(services.BusinessError,"影响已确认预约"):
            services.change_opening(self.admin,values)

    def test_room_schedule_change_closes_finite_period_crossing_effective_date(self):
        effective=timezone.localdate()+timedelta(days=14)
        weekday=effective.isoweekday()
        old=OpeningPeriod.objects.filter(lab=self.room_short.lab,resource=self.room_short,
                                         weekday=weekday,valid_from__lt=effective).order_by("-valid_from").first()
        self.assertIsNotNone(old)
        old.valid_to=effective+timedelta(days=7)
        old.save(update_fields=["valid_to"])

        before=effective-timedelta(days=7)
        values={"lab":self.room_short.lab,"resource":self.room_short,"weekday":weekday,
                "starts_at":time(9),"ends_at":time(16),"effective":effective,
                "reason":"有限期日历版本边界回归"}
        new=services.change_opening(self.admin,values)

        old.refresh_from_db()
        self.assertEqual(old.valid_to,effective-timedelta(days=1))
        rows_before=weekly_periods(self.room_short,before)
        rows_effective=weekly_periods(self.room_short,effective)
        self.assertIn(old,rows_before)
        self.assertEqual(rows_effective,[new])

    def test_room_can_be_closed_weekly_without_closing_other_rooms_in_lab(self):
        day=timezone.localdate()+timedelta(days=2)
        start=timezone.make_aware(datetime.combine(day,time(10)),TZ)
        values={"lab":self.room_short.lab,"resource":self.room_short,"weekday":day.isoweekday(),
                "starts_at":time.min,"ends_at":time.min,"closed_all_day":True,
                "effective":day,"reason":"验证单间场地每周关闭覆盖"}
        services.change_opening(self.admin,values)

        entry=next(row for row in weekly_schedule(self.room_short) if row["date"]==day.isoformat())
        self.assertEqual(entry["ranges"],[])
        self.assertEqual(entry["note"],"每周全天关闭")
        with self.assertRaisesRegex(services.BusinessError,"开放时间"):
            services.create_reservation(self.teacher,self.room_short.pk,start,start+timedelta(hours=1),"关闭场地不可预约")

        other=services.create_reservation(self.teacher,self.room_late.pk,start,start+timedelta(hours=1),"同实验室其他房间仍开放")
        self.assertEqual(other.status,"PENDING")

    def test_room_candidates_use_room_override_instead_of_lab_default(self):
        day=timezone.localdate()+timedelta(days=2)
        result=slot_options(self.room_short,self.teacher,day,60,at=timezone.now())
        labels=[item["label"] for item in result["items"]]
        self.assertEqual(len(labels),19)
        self.assertEqual(labels[0],"08:00—09:00")
        self.assertEqual(labels[-1],"17:00—18:00")
        self.assertNotIn("19:00—20:00",labels)

    def test_closed_room_has_no_candidates_from_lab_default(self):
        day=timezone.localdate()+timedelta(days=2)
        services.change_opening(self.admin,{
            "lab":self.room_short.lab,"resource":self.room_short,"weekday":day.isoweekday(),
            "starts_at":time.min,"ends_at":time.min,"closed_all_day":True,
            "effective":day,"reason":"仅验证场地关闭不生成默认候选时段",
        })
        result=slot_options(self.room_short,self.teacher,day,60,at=timezone.now())
        self.assertEqual(result["items"],[])
        self.assertEqual(result["notice"],"当日没有容纳所选时长的开放时段。")

    def test_weekly_and_single_day_closure_are_explicit_in_calendar_and_schedule(self):
        day=timezone.localdate()+timedelta(days=2)
        start=timezone.make_aware(datetime.combine(day,time(10)),TZ)
        services.change_opening(self.admin,{
            "lab":self.room_short.lab,"resource":self.room_short,"weekday":day.isoweekday(),
            "starts_at":time.min,"ends_at":time.min,"closed_all_day":True,
            "effective":day,"reason":"日程明确展示每周全天关闭",
        })
        self.client.force_login(self.teacher)
        weekly=self.client.get("/schedule/",{"day":day.isoformat(),"q":self.room_short.code})
        weekly_row=weekly.context["rows"][0]
        self.assertEqual(weekly_row["windows"],[])
        self.assertEqual(weekly_row["opening_note"],"每周全天关闭")
        self.assertContains(weekly,"每周全天关闭")
        self.assertNotContains(weekly,"00:00—00:00")

        exception_day=day+timedelta(days=1)
        CalendarException.objects.create(lab=self.room_short.lab,day=exception_day,closed=True,
                                         actor=self.system,reason="测试单日关闭例外")
        display=next(row for row in weekly_schedule(self.room_short,as_of=exception_day)
                     if row["date"]==exception_day.isoformat())
        self.assertEqual(display["ranges"],[])
        self.assertEqual(display["note"],"单日关闭例外")
        exception_page=self.client.get("/schedule/",{"day":exception_day.isoformat(),"q":self.room_short.code})
        self.assertContains(exception_page,"单日关闭例外")
