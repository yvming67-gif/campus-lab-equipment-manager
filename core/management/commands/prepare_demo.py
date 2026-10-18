"""生成当前开放时段的模拟已确认记录，方便现场交接演示。不会清空数据。"""
from datetime import timedelta
from django.core.management.base import BaseCommand,CommandError
from django.utils import timezone
from core.models import *
from core.services import create_reservation,approve,BusinessError
class Command(BaseCommand):
    help="仅新增模拟数据：在开放时段准备当前设备与场地预约，不修改电脑时间"
    def handle(self,*args,**options):
        now=timezone.localtime();start=now.replace(minute=now.minute//30*30,second=0,microsecond=0);end=min(start+timedelta(hours=1),start.replace(hour=20,minute=0))
        if start.hour<8 or start.hour>=20:raise CommandError("当前不在默认08:00至20:00开放时间。可演示未来预约，现场交接请在开放时间运行本命令。")
        admin=User.objects.get(username="admin")
        for username,code in [("student2","EQ-SEN-01"),("teacher","ROOM-101")]:
            u=User.objects.get(username=username);r=Resource.objects.get(code=code)
            if Reservation.objects.filter(applicant=u,resource=r,starts_at=start,status__in=["CONFIRMED","IN_USE"]).exists():self.stdout.write("Existing current sample: "+code);continue
            try:
                b=create_reservation(u,r.pk,start,end,"当前时段课程演示（模拟已获准申请）",participants=18 if r.kind=="ROOM" else 1,at=start-timedelta(minutes=5))
                if b.status=="PENDING":approve(admin,b.pk,True,"模拟教学安排通过",at=start-timedelta(minutes=4))
                self.stdout.write(f"Ready: {code} / {u.school_no} / booking {b.pk}")
            except BusinessError as e:self.stdout.write("Sample skipped: "+str(e))
