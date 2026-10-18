from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from core.models import *
from core.services import create_reservation, approve, checkout, start_room, report_incident, audit
class Command(BaseCommand):
    help = "幂等初始化模拟资源、人员和可演示业务；不会清空已有数据"
    def add_arguments(self,parser):parser.add_argument("--if-empty",action="store_true")
    @transaction.atomic
    def handle(self, *args, **options):
        if options.get("if_empty") and User.objects.exists():
            self.stdout.write("Existing demo data preserved; initialization skipped.");return
        roles = [("STUDENT","学生"),("TEACHER","教师"),("LAB_ADMIN","实验室管理员"),("TECHNICIAN","维修人员"),("SYS_ADMIN","系统管理员"),("DIRECTOR","实验教学中心主任")]
        for code,name in roles: Role.objects.get_or_create(code=code,defaults={"name":name})
        people=[("student","S-DEMO-01","演示学生","STUDENT"),("student2","S-DEMO-02","另一位学生","STUDENT"),("teacher","T-DEMO-01","演示教师","TEACHER"),("admin","A-DEMO-01","资源管理员","LAB_ADMIN"),("tech","M-DEMO-01","维修工程师","TECHNICIAN"),("sysadmin","Y-DEMO-01","系统管理员","SYS_ADMIN"),("director","D-DEMO-01","中心主任","DIRECTOR")]
        for username,no,name,role in people:
            u,new=User.objects.get_or_create(username=username,defaults={"school_no":no,"display_name":name})
            if new: u.set_unusable_password();u.save()
            UserRole.objects.get_or_create(user=u,role=Role.objects.get(code=role))
        lab1,_=Lab.objects.get_or_create(code="LAB-101",defaults={"name":"电子与传感实验室","location":"实验楼 A101","description":"示波器与传感器教学。全部为模拟资源。"})
        lab2,_=Lab.objects.get_or_create(code="LAB-202",defaults={"name":"机器人与数字影像实验室","location":"实验楼 B202","description":"机器人、摄影与协同课程。"})
        admin=User.objects.get(username="admin")
        for lab in [lab1,lab2]:
            LabGrant.objects.get_or_create(user=admin,lab=lab)
            for day in range(1,8): OpeningPeriod.objects.get_or_create(lab=lab,weekday=day,starts_at=time(8),valid_from=timezone.localdate()-timedelta(days=365),defaults={"ends_at":time(20)})
        qual,_=QualificationType.objects.get_or_create(code="ROBOT-SAFE",defaults={"name":"机器人平台安全培训（模拟）"})
        for username in ["student","teacher"]:UserQualification.objects.get_or_create(user=User.objects.get(username=username),qualification=qual,defaults={"expires_at":timezone.now()+timedelta(days=365),"granted_by":admin})
        specs=[("EQ-OSC-01","数字示波器",lab1,"示波器","NORMAL",None),("EQ-SEN-01","传感器教学套件",lab1,"传感器套件","NORMAL",None),("EQ-PC-01","便携式计算机",lab1,"计算机","NORMAL",None),("EQ-CAM-01","全画幅摄影套装",lab2,"摄影设备","HIGH_VALUE",None),("EQ-ROB-01","协作机器人平台",lab2,"机器人","HAZARDOUS",qual),("EQ-SEN-02","物联网传感套件",lab2,"传感器套件","NORMAL",None),("EQ-PC-02","移动工作站",lab2,"计算机","NORMAL",None),("EQ-OSC-02","混合信号示波器",lab1,"示波器","NORMAL",None)]
        for code,name,lab,category,risk,q in specs:
            r,new=Resource.objects.get_or_create(code=code,defaults={"name":name,"lab":lab,"kind":"EQUIPMENT","description":"可独立借用，示例信息。"})
            EquipmentDetail.objects.get_or_create(resource=r,defaults={"category":category,"risk":risk,"qualification":q})
            Accessory.objects.get_or_create(resource=r,code="POWER",defaults={"name":"电源与连接线","quantity":1})
            Accessory.objects.get_or_create(resource=r,code="CASE",defaults={"name":"便携保护箱","quantity":1})
            if not r.service_periods.exists():
                ev=audit(None,r,"RESOURCE_CREATE",new={"status":r.service_status},reason="模拟资源初始化",at=r.created_at)
                ServicePeriod.objects.create(resource=r,starts_at=r.created_at,status=r.service_status,audit=ev)
        for code,lab,cap in [("ROOM-101",lab1,32),("ROOM-202",lab2,24)]:
            r,_=Resource.objects.get_or_create(code=code,defaults={"name":lab.name+"整间场地","lab":lab,"kind":"ROOM","description":"场地与室内设备分别预约。"})
            RoomDetail.objects.get_or_create(resource=r,defaults={"capacity":cap,"safety_note":"结束时整理工位、关闭电源并交接。"})
            if not r.service_periods.exists():
                ev=audit(None,r,"RESOURCE_CREATE",new={"status":r.service_status},reason="模拟资源初始化",at=r.created_at)
                ServicePeriod.objects.create(resource=r,starts_at=r.created_at,status=r.service_status,audit=ev)
        policy,_=Policy.objects.get_or_create(pk=1)
        PolicyRevision.objects.get_or_create(policy=policy,version=policy.version,defaults={"data":policy.snapshot(),"reason":"课程案例初始参数"})
        self.stdout.write("Demo seed ready: 7 accounts, 2 labs, 8 equipment, 2 room resources.")
