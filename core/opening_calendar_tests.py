"""Room-level weekly schedules remain consistent across web, mini and domain rules."""
import json
from datetime import datetime, time, timedelta

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from . import services
from .experience import slot_options
from .forms import OpeningForm
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

    def test_weekly_schedule_supports_multiple_non_overlapping_windows(self):
        day=timezone.localdate()+timedelta(days=2)
        services.change_opening(self.admin,{
            "lab":self.room_short.lab,"resource":self.room_short,"weekday":day.isoweekday(),
            "windows":[(time(8),time(12)),(time(13),time(17))],
            "effective":day,"reason":"验证分段开放日历配置",
        })

        self.assertEqual(OpeningPeriod.objects.filter(lab=self.room_short.lab,resource=self.room_short,
            weekday=day.isoweekday(),valid_from=day).count(),2)
        entry=next(row for row in weekly_schedule(self.room_short) if row["date"]==day.isoformat())
        self.assertEqual(entry["ranges"],["08:00—12:00","13:00—17:00"])

        morning=timezone.make_aware(datetime.combine(day,time(11)),TZ)
        lunch=timezone.make_aware(datetime.combine(day,time(12)),TZ)
        afternoon=timezone.make_aware(datetime.combine(day,time(13)),TZ)
        services.create_reservation(self.teacher,self.room_short.pk,morning,morning+timedelta(hours=1),"上午开放窗口测试")
        with self.assertRaisesRegex(services.BusinessError,"开放时间"):
            services.create_reservation(self.teacher,self.room_short.pk,lunch,lunch+timedelta(hours=1),"中间关闭窗口测试")
        services.create_reservation(self.teacher,self.room_short.pk,afternoon,afternoon+timedelta(hours=1),"下午开放窗口测试")
        preview=slot_options(self.room_short,self.teacher,day,120,at=timezone.now())
        self.assertNotIn("11:00—13:00",{item["label"] for item in preview["items"]})

    def test_opening_form_parses_windows_and_rejects_overlap_or_bad_clock(self):
        lab=Lab.objects.get(code="LAB-101")
        base={"lab":lab.pk,"weekday":2,"effective":(timezone.localdate()+timedelta(days=1)).isoformat(),"reason":"表单校验"}
        valid=OpeningForm(data={**base,"windows":"13:00-17:00\n08:00-12:00"})
        self.assertTrue(valid.is_valid(),valid.errors)
        self.assertEqual(valid.cleaned_data["windows"],[(time(8),time(12)),(time(13),time(17))])

        overlap=OpeningForm(data={**base,"windows":"08:00-12:00\n11:30-17:00"})
        self.assertFalse(overlap.is_valid())
        self.assertIn("不得重叠",str(overlap.errors))
        malformed=OpeningForm(data={**base,"windows":"8am to 12pm"})
        self.assertFalse(malformed.is_valid())
        self.assertIn("格式错误",str(malformed.errors))

    def test_management_form_saves_multiple_windows_as_one_effective_schedule(self):
        day=timezone.localdate()+timedelta(days=2)
        self.client.force_login(self.admin)
        response=self.client.post("/manage/opening/",{
            "lab":self.room_short.lab_id,"resource":self.room_short.pk,
            "weekday":day.isoweekday(),"windows":"08:00-12:00\n13:00-17:00",
            "effective":day.isoformat(),"reason":"网页配置分段开放时段",
        })
        self.assertEqual(response.status_code,302)
        rows=list(OpeningPeriod.objects.filter(lab=self.room_short.lab,resource=self.room_short,
            weekday=day.isoweekday(),valid_from=day).order_by("starts_at"))
        self.assertEqual([(row.starts_at,row.ends_at) for row in rows],[(time(8),time(12)),(time(13),time(17))])
        calendar=self.client.get("/manage/",{"section":"calendars","calendar_from":day.isoformat()})
        groups=[group for group in calendar.context["opening_periods"] if group["resource"]==self.room_short
            and group["valid_from"]==day]
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]["windows"],[(time(8),time(12)),(time(13),time(17))])
        self.assertContains(calendar,"08:00—12:00")
        self.assertContains(calendar,"13:00—17:00")

    def test_adjacent_windows_form_one_continuous_reservable_period(self):
        day=timezone.localdate()+timedelta(days=2)
        services.change_opening(self.admin,{
            "lab":self.room_short.lab,"resource":self.room_short,"weekday":day.isoweekday(),
            "windows":[(time(8),time(12)),(time(12),time(17))],
            "effective":day,"reason":"相邻开放时段连续性测试",
        })

        rows=OpeningPeriod.objects.filter(lab=self.room_short.lab,resource=self.room_short,
            weekday=day.isoweekday(),valid_from=day)
        self.assertEqual(rows.count(),1)
        self.assertEqual((rows.get().starts_at,rows.get().ends_at),(time(8),time(17)))
        entry=next(row for row in weekly_schedule(self.room_short) if row["date"]==day.isoformat())
        self.assertEqual(entry["ranges"],["08:00—17:00"])
        preview=slot_options(self.room_short,self.teacher,day,120,at=timezone.now())
        spanning=next(item for item in preview["items"] if item["label"]=="11:00—13:00")
        self.assertTrue(spanning["available"],spanning["reason"])
        start=timezone.make_aware(datetime.combine(day,time(11)),TZ)
        booking=services.create_reservation(self.teacher,self.room_short.pk,start,start+timedelta(hours=2),"跨相邻区间预约")
        self.assertEqual(booking.status,"PENDING")

    def test_change_opening_rejects_overlapping_windows(self):
        day=timezone.localdate()+timedelta(days=2)
        values={"lab":self.room_short.lab,"resource":self.room_short,"weekday":day.isoweekday(),
            "windows":[(time(8),time(12)),(time(11,30),time(17))],
            "effective":day,"reason":"重叠窗口不得生效"}
        with self.assertRaisesRegex(services.BusinessError,"不得重叠"):
            services.change_opening(self.admin,values)
        self.assertFalse(OpeningPeriod.objects.filter(lab=self.room_short.lab,resource=self.room_short,
            weekday=day.isoweekday(),valid_from=day).exists())

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

    def test_system_admin_form_requires_lab_role_and_grant_for_room_overrides(self):
        lab = self.room_short.lab
        self.client.force_login(self.system)
        form = self.client.get("/manage/opening/").context["form"]
        self.assertFalse(form.fields["resource"].required)
        self.assertFalse(form.fields["resource"].queryset.exists())
        day = timezone.localdate() + timedelta(days=2)
        values = {
            "lab": lab.pk, "resource": self.room_short.pk, "weekday": day.isoweekday(),
            "windows": "08:00-17:00", "effective": day.isoformat(), "reason": "验证周历角色范围",
        }
        denied = self.client.post("/manage/opening/", values)
        self.assertIn("resource", denied.context["form"].errors)
        malformed = self.client.post("/manage/opening/", {**values, "lab": "invalid"})
        self.assertIn("lab", malformed.context["form"].errors)
        accepted = self.client.post("/manage/opening/", {**values, "resource": ""})
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(OpeningPeriod.objects.filter(lab=lab, resource=None, valid_from=day).exists())

        UserRole.objects.create(user=self.system, role=Role.objects.get(code="LAB_ADMIN"))
        LabGrant.objects.create(user=self.system, lab=lab)
        form = self.client.get("/manage/opening/").context["form"]
        self.assertEqual(set(form.fields["resource"].queryset.values_list("lab_id", flat=True)), {lab.pk})
        self.assertEqual(self.client.post("/manage/opening/", values).status_code, 302)
        self.assertTrue(OpeningPeriod.objects.filter(resource=self.room_short, valid_from=day).exists())

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
