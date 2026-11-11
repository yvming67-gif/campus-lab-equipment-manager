from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from core.models import *
from core.services import create_reservation, approve, checkout, start_room, report_incident, audit


DEMO_OPENING_VALID_FROM = date(2000, 1, 1)


def ensure_demo_opening_periods(*, lab, starts_at, ends_at, resource=None):
    """Add a demo week only when that resource scope has no schedule.

    Existing schedules may be locally maintained or institution-specific. The
    seed must not replace them, reopen an intentionally closed weekday, or
    create a new copy each day it is rerun. Call only for newly created demo
    labs or rooms.
    """
    existing = OpeningPeriod.objects.filter(lab=lab)
    if resource is None:
        existing = existing.filter(resource__isnull=True)
    else:
        existing = existing.filter(resource=resource)
    if existing.exists():
        return
    for weekday in range(1, 8):
        OpeningPeriod.objects.get_or_create(
            lab=lab,
            resource=resource,
            weekday=weekday,
            starts_at=starts_at,
            valid_from=DEMO_OPENING_VALID_FROM,
            defaults={"ends_at": ends_at},
        )


def grant_new_demo_lab_scopes(admin, labs, newly_created_lab_ids, admin_created=False):
    """Grant first-run scope without undoing an explicit revocation."""
    for lab in labs:
        if admin_created or lab.pk in newly_created_lab_ids:
            LabGrant.objects.get_or_create(user=admin, lab=lab)


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
        created_users=set()
        for username,no,name,role in people:
            u,new=User.objects.get_or_create(username=username,defaults={"school_no":no,"display_name":name})
            if new:
                created_users.add(u.pk);u.set_unusable_password();u.save()
                UserRole.objects.get_or_create(user=u,role=Role.objects.get(code=role))
        lab1,lab1_created=Lab.objects.get_or_create(code="LAB-101",defaults={"name":"演示·电子与传感实验室","location":"演示楼 A · 1层","description":"课程演示管理单元；用途、地点和资源均为模拟示例。"})
        lab2,lab2_created=Lab.objects.get_or_create(code="LAB-202",defaults={"name":"演示·机器人与数字影像实验室","location":"演示楼 B · 2层","description":"课程演示管理单元；用途、地点和资源均为模拟示例。"})
        lab3,lab3_created=Lab.objects.get_or_create(code="LAB-303",defaults={"name":"演示·计算与物联网实验室","location":"演示楼 C · 3层","description":"课程演示管理单元；用途、地点和资源均为模拟示例。"})
        # These physical locations are explicitly fictional demo rows. Never
        # import or infer real campus/building data from the management units.
        demo_campus,_=Campus.objects.get_or_create(code="CAMPUS-DEMO",defaults={"name":"演示校区"})
        demo_buildings={}
        for suffix,name in (("A","演示楼A"),("B","演示楼B"),("C","演示楼C")):
            demo_buildings[suffix],_=Building.objects.get_or_create(
                campus=demo_campus,code=f"BLD-DEMO-{suffix}",defaults={"name":name})
        # Convert only the exact labels created by earlier versions of this seed.
        # Any locally edited values are left untouched.
        legacy_labels={
            lab1:("电子与传感实验室","实验楼 A101"),
            lab2:("机器人与数字影像实验室","实验楼 B202"),
        }
        for lab,(old_name,old_location) in legacy_labels.items():
            changed=[]
            if lab.name==old_name:lab.name="演示·"+old_name;changed.append("name")
            if lab.location==old_location:
                lab.location="演示楼 A · 1层" if lab==lab1 else "演示楼 B · 2层";changed.append("location")
            if changed:lab.save(update_fields=changed)
        admin=User.objects.get(username="admin")
        # Establish the initial scope for a new account and newly introduced
        # demo labs, but never silently restore an existing revoked grant.
        grant_new_demo_lab_scopes(admin,[lab1,lab2,lab3],
                                  {lab.pk for lab,created in [(lab1,lab1_created),(lab2,lab2_created),(lab3,lab3_created)] if created},
                                  admin_created=admin.pk in created_users)
        for lab, created in ((lab1,lab1_created),(lab2,lab2_created),(lab3,lab3_created)):
            if created:
                ensure_demo_opening_periods(
                    lab=lab, starts_at=time(8), ends_at=time(20)
                )
        qual,_=QualificationType.objects.get_or_create(code="ROBOT-SAFE",defaults={"name":"机器人平台安全培训（模拟）"})
        for username in ["student","teacher"]:UserQualification.objects.get_or_create(user=User.objects.get(username=username),qualification=qual,defaults={"expires_at":timezone.now()+timedelta(days=365),"granted_by":admin})
        specs=[("EQ-OSC-01","数字示波器",lab1,"示波器","NORMAL",None),("EQ-SEN-01","传感器教学套件",lab1,"传感器套件","NORMAL",None),("EQ-PC-01","便携式计算机",lab1,"计算机","NORMAL",None),("EQ-CAM-01","全画幅摄影套装",lab2,"摄影设备","HIGH_VALUE",None),("EQ-ROB-01","协作机器人平台",lab2,"机器人","HAZARDOUS",qual),("EQ-SEN-02","物联网传感套件",lab2,"传感器套件","NORMAL",None),("EQ-PC-02","移动工作站",lab2,"计算机","NORMAL",None),("EQ-OSC-02","混合信号示波器",lab1,"示波器","NORMAL",None),("EQ-ROB-FIX-01","固定式机器人演示平台",lab2,"机器人平台","HAZARDOUS",qual)]
        new_equipment_codes=set()
        for code,name,lab,category,risk,q in specs:
            description="演示数据：固定设备须受关联场地可用性约束。" if code=="EQ-ROB-FIX-01" else "演示数据：设备可独立借用。"
            r,new=Resource.objects.get_or_create(code=code,defaults={"name":name,"lab":lab,"kind":"EQUIPMENT","description":description})
            if new:new_equipment_codes.add(code)
            EquipmentDetail.objects.get_or_create(resource=r,defaults={"category":category,"risk":risk,"qualification":q})
            Accessory.objects.get_or_create(resource=r,code="POWER",defaults={"name":"电源与连接线","quantity":1})
            Accessory.objects.get_or_create(resource=r,code="CASE",defaults={"name":"便携保护箱","quantity":1})
            if not r.service_periods.exists():
                ev=audit(None,r,"RESOURCE_CREATE",new={"status":r.service_status},reason="模拟资源初始化",at=r.created_at)
                ServicePeriod.objects.create(resource=r,starts_at=r.created_at,status=r.service_status,audit=ev)
        rooms=[
            ("ROOM-101",lab1,32,"演示·电子测量教室 101","适用课程：电子测量（模拟示例）；示例设施：教学桌与投影设备。","综合教学实验室","教学桌；投影设备（均为模拟示例）","演示楼A·1层·101室"),
            ("ROOM-102",lab1,20,"演示·传感器实验教室 102","适用课程：传感器基础（模拟示例）；示例设施：可移动教学台。","传感器教学实验室","可移动教学台（模拟示例）","演示楼A·1层·102室"),
            ("ROOM-201",lab2,24,"演示·机器人实践教室 201","适用课程：机器人实践（模拟示例）；设施和设备依赖须由管理员核实。","机器人实践实验室","教学工作台；设备依赖待核实（模拟示例）","演示楼B·2层·201室"),
            ("ROOM-202",lab2,16,"演示·数字影像教室 202","适用课程：数字影像（模拟示例）；示例设施：教学工作台。","数字影像教学实验室","影像教学工作台（模拟示例）","演示楼B·2层·202室"),
            ("ROOM-301",lab3,36,"演示·计算机实验教室 301","适用课程：计算实验（模拟示例）；示例设施：学生工作站。","计算机教学实验室","学生工作站（模拟示例）","演示楼C·3层·301室"),
            ("ROOM-302",lab3,18,"演示·物联网教学教室 302","适用课程：物联网教学（模拟示例）；固定设备约束以实际档案为准。","物联网教学实验室","物联网教学设备依赖待核实（模拟示例）","演示楼C·3层·302室"),
        ]
        legacy_room_names={"ROOM-101":"电子与传感实验室整间场地","ROOM-202":"机器人与数字影像实验室整间场地"}
        safety_placeholder="安全信息待责任岗位确认。本演示占位不构成风险评估、操作规程、培训、准入或安全许可；现场以经批准并现行有效的安全材料为准。"
        room_hours={"ROOM-101":(time(8),time(18)),"ROOM-102":(time(9),time(20)),"ROOM-201":(time(8),time(19)),"ROOM-202":(time(10),time(18)),"ROOM-301":(time(8),time(20)),"ROOM-302":(time(9),time(17))}
        for code,lab,cap,name,description,space_category,facilities,location_label in rooms:
            demo_status="MAINTENANCE" if code=="ROOM-302" else "NORMAL"
            status_note="演示状态示例：该场地当前维护中，不代表任何真实学校或实际房间状态。" if code=="ROOM-302" else ""
            r,new=Resource.objects.get_or_create(code=code,defaults={"name":name,"lab":lab,"kind":"ROOM","description":"演示数据。"+description+(" "+status_note if status_note else ""),"service_status":demo_status})
            if r.name==legacy_room_names.get(code):r.name=name;r.save(update_fields=["name"])
            if r.description=="场地与室内设备分别预约。":r.description="演示数据。"+description;r.save(update_fields=["description"])
            RoomDetail.objects.get_or_create(resource=r,defaults={"location_label":location_label,"capacity":cap,"space_category":space_category,"facilities":facilities,"safety_note":safety_placeholder})
            detail=RoomDetail.objects.get(resource=r)
            demo_location={
                "ROOM-101":("A","1层","101室"),"ROOM-102":("A","1层","102室"),
                "ROOM-201":("B","2层","201室"),"ROOM-202":("B","2层","202室"),
                "ROOM-301":("C","3层","301室"),"ROOM-302":("C","3层","302室"),
            }.get(code)
            if demo_location and not detail.building_id and detail.location_label==location_label:
                suffix,floor,room_no=demo_location
                detail.building=demo_buildings[suffix];detail.floor_label=floor;detail.room_number=room_no
                detail.save(update_fields=["building","floor_label","room_number"])
            if r.description.startswith("演示数据。") and not detail.space_category and not detail.facilities:
                detail.space_category=space_category;detail.facilities=facilities;detail.save(update_fields=["space_category","facilities"])
            if detail.safety_note=="结束时整理工位、关闭电源并交接。":detail.safety_note=safety_placeholder;detail.save(update_fields=["safety_note"])
            # Course-demo room schedules intentionally vary by room; they do
            # not represent any real institution's opening hours.
            opens,closes=room_hours[code]
            if new:
                ensure_demo_opening_periods(
                    lab=lab, resource=r, starts_at=opens, ends_at=closes,
                )
            if not r.service_periods.exists():
                ev=audit(None,r,"RESOURCE_CREATE",new={"status":r.service_status},reason="模拟资源初始化",at=r.created_at)
                ServicePeriod.objects.create(resource=r,starts_at=r.created_at,status=r.service_status,audit=ev)
        # Exercise the fixed-equipment constraint on fresh demo installations.
        # Never rewrite a resource or equipment record already present.
        if "EQ-ROB-FIX-01" in new_equipment_codes:
            robot=Resource.objects.get(code="EQ-ROB-FIX-01")
            robot.equipment.portable=False
            robot.equipment.access_room=Resource.objects.get(code="ROOM-201")
            robot.equipment.save(update_fields=["portable","access_room"])
        policy,_=Policy.objects.get_or_create(pk=1)
        PolicyRevision.objects.get_or_create(policy=policy,version=policy.version,defaults={"data":policy.snapshot(),"reason":"课程案例初始参数"})
        self.stdout.write("Demo seed ready: 7 accounts, 3 demo lab units, 9 equipment, 6 demo room resources.")
