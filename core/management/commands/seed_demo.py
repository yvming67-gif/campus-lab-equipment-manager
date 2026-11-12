from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from core.demo_identity import provision_private_applicants
from core.models import *
from core.services import create_reservation, approve, checkout, start_room, report_incident, audit


DEMO_OPENING_VALID_FROM = date(2000, 1, 1)
DEMO_ROOM_LOCATIONS = {
    "ROOM-101": ("LAB-101", "A", "1层", "101室"),
    "ROOM-102": ("LAB-101", "A", "1层", "102室"),
    "ROOM-201": ("LAB-202", "B", "2层", "201室"),
    "ROOM-202": ("LAB-202", "B", "2层", "202室"),
    "ROOM-301": ("LAB-303", "C", "3层", "301室"),
    "ROOM-302": ("LAB-303", "C", "3层", "302室"),
}
DEMO_LAB_303 = {
    "name": "演示·计算与物联网实验室",
    "location": "演示楼 C · 3层",
    "description": "课程演示管理单元；用途、地点和资源均为模拟示例。",
}
LEGACY_PUBLIC_DEMO = {
    "LAB-101": ("电子与传感实验室", "实验楼 A101", "示波器与传感器教学。全部为模拟资源。", "ROOM-101"),
    "LAB-202": ("机器人与数字影像实验室", "实验楼 B202", "机器人、摄影与协同课程。", "ROOM-202"),
}
LEGACY_ROOM_DESCRIPTION = "场地与室内设备分别预约。"
SAFETY_PLACEHOLDER = "安全信息待责任岗位确认。本演示占位不构成风险评估、操作规程、培训、准入或安全许可；现场以经批准并现行有效的安全材料为准。"
DEMO_ROOM_SPECS = (
    ("ROOM-101", 32, "演示·电子测量教室 101", "适用课程：电子测量（模拟示例）；示例设施：教学桌与投影设备。", "综合教学实验室", "教学桌；投影设备（均为模拟示例）", time(8), time(18)),
    ("ROOM-102", 20, "演示·传感器实验教室 102", "适用课程：传感器基础（模拟示例）；示例设施：可移动教学台。", "传感器教学实验室", "可移动教学台（模拟示例）", time(9), time(20)),
    ("ROOM-201", 24, "演示·机器人实践教室 201", "适用课程：机器人实践（模拟示例）；设施和设备依赖须由管理员核实。", "机器人实践实验室", "教学工作台；设备依赖待核实（模拟示例）", time(8), time(19)),
    ("ROOM-202", 16, "演示·数字影像教室 202", "适用课程：数字影像（模拟示例）；示例设施：教学工作台。", "数字影像教学实验室", "影像教学工作台（模拟示例）", time(10), time(18)),
    ("ROOM-301", 36, "演示·计算机实验教室 301", "适用课程：计算实验（模拟示例）；示例设施：学生工作站。", "计算机教学实验室", "学生工作站（均为模拟示例）", time(8), time(20)),
    ("ROOM-302", 18, "演示·物联网教学教室 302", "适用课程：物联网教学（模拟示例）；固定设备约束以实际档案为准。", "物联网教学实验室", "物联网教学设备依赖待核实（模拟示例）", time(9), time(17)),
)


def fill_known_demo_room_locations():
    """Link only unchanged demo labels with no recorded structured location."""
    existing = RoomDetail.objects.select_for_update(of=("self",)).filter(
        resource__code__in=DEMO_ROOM_LOCATIONS, resource__kind="ROOM",
        resource__description__startswith="演示数据。", building__isnull=True,
        floor_label="", room_number="",
    ).select_related("resource__lab")
    matched = []
    for detail in existing:
        lab, building, floor, number = DEMO_ROOM_LOCATIONS[detail.resource.code]
        if detail.resource.lab.code == lab and detail.location_label == f"演示楼{building}·{floor}·{number}":
            matched.append((detail, building, floor, number))
    if not matched:
        return 0

    campus, _ = Campus.objects.get_or_create(code="CAMPUS-DEMO", defaults={"name": "演示校区"})
    if not campus.is_active or campus.name != "演示校区":
        return 0
    updated = 0
    for detail, suffix, floor, number in matched:
        name = f"演示楼{suffix}"
        building, _ = Building.objects.get_or_create(campus=campus, code=f"BLD-DEMO-{suffix}", defaults={"name": name})
        if building.is_active and building.name == name:
            detail.building = building
            detail.floor_label = floor
            detail.room_number = number
            detail.save(update_fields=["building", "floor_label", "room_number"])
            updated += 1
    return updated


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


def seed_demo_room(lab, spec, *, repair_existing=False):
    code, capacity, name, description, category, facilities, opens, closes = spec
    _, building, floor, number = DEMO_ROOM_LOCATIONS[code]
    location = f"演示楼{building}·{floor}·{number}"
    status = "MAINTENANCE" if code == "ROOM-302" else "NORMAL"
    status_note = " 演示状态示例：该场地当前维护中，不代表任何真实学校或实际房间状态。" if status == "MAINTENANCE" else ""
    demo_description = f"演示数据。{description}"
    resource, created = Resource.objects.get_or_create(
        code=code, defaults={
            "name": name, "lab": lab, "kind": "ROOM", "description": demo_description + status_note,
            "service_status": status,
        },
    )
    if not created:
        if not repair_existing or resource.kind != "ROOM" or resource.lab_id != lab.pk:
            return False
        changes = []
        legacy = LEGACY_PUBLIC_DEMO.get(lab.code)
        if legacy and code == legacy[3] and resource.name == f"{legacy[0]}整间场地":
            resource.name = name
            changes.append("name")
        if resource.description == LEGACY_ROOM_DESCRIPTION:
            resource.description = demo_description
            changes.append("description")
        if changes:
            resource.save(update_fields=changes)

    detail, _ = RoomDetail.objects.get_or_create(resource=resource, defaults={
        "location_label": location, "capacity": capacity, "space_category": category,
        "facilities": facilities, "safety_note": SAFETY_PLACEHOLDER,
    })
    if repair_existing:
        changes = []
        if resource.description.startswith("演示数据。") and not detail.space_category and not detail.facilities:
            detail.space_category, detail.facilities = category, facilities
            changes.extend(("space_category", "facilities"))
        if detail.safety_note == "结束时整理工位、关闭电源并交接。":
            detail.safety_note = SAFETY_PLACEHOLDER
            changes.append("safety_note")
        if changes:
            detail.save(update_fields=changes)
    if created:
        ensure_demo_opening_periods(lab=lab, resource=resource, starts_at=opens, ends_at=closes)
    if not resource.service_periods.exists():
        event = audit(None, resource, "RESOURCE_CREATE", new={"status": resource.service_status},
                      reason="模拟资源初始化", at=resource.created_at)
        ServicePeriod.objects.create(resource=resource, starts_at=resource.created_at,
                                     status=resource.service_status, audit=event)
    return created


def extend_legacy_public_demo_rooms():
    """Add the missing catalog examples only to the recognized original public demo."""
    labs = {}
    for code, (name, location, description, room_code) in LEGACY_PUBLIC_DEMO.items():
        lab = Lab.objects.filter(code=code, name=name, location=location, description=description, is_active=True).first()
        if lab is None or not RoomDetail.objects.filter(
            resource__lab=lab, resource__code=room_code, resource__kind="ROOM",
            resource__name=f"{name}整间场地", resource__description=LEGACY_ROOM_DESCRIPTION,
        ).exists():
            return 0
        labs[code] = lab
    lab, created = Lab.objects.get_or_create(code="LAB-303", defaults=DEMO_LAB_303)
    if lab.is_active and all(getattr(lab, field) == value for field, value in DEMO_LAB_303.items()):
        labs[lab.code] = lab
        if created:
            ensure_demo_opening_periods(lab=lab, starts_at=time(8), ends_at=time(20))
    added = 0
    for spec in DEMO_ROOM_SPECS:
        lab = labs.get(DEMO_ROOM_LOCATIONS[spec[0]][0])
        if lab is not None:
            added += seed_demo_room(lab, spec)
    return added


class Command(BaseCommand):
    help = "幂等初始化模拟资源、人员和可演示业务；不会清空已有数据"
    def add_arguments(self, parser):
        parser.add_argument("--if-empty", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if options.get("if_empty") and User.objects.exists():
            if settings.DEMO_MODE and settings.PUBLIC_DEMO_MODE:
                added = extend_legacy_public_demo_rooms()
                if added:
                    self.stdout.write(f"Added {added} simulated room resources to the known public demo.")
                linked = fill_known_demo_room_locations()
                if linked:
                    self.stdout.write(f"Linked {linked} known demo room locations.")
            provision_private_applicants()
            self.stdout.write("Existing demo data preserved; general initialization skipped.")
            return
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
        lab3,lab3_created=Lab.objects.get_or_create(code="LAB-303",defaults=DEMO_LAB_303)
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
        labs_by_code = {lab.code: lab for lab in (lab1, lab2, lab3)}
        for spec in DEMO_ROOM_SPECS:
            lab_code = DEMO_ROOM_LOCATIONS[spec[0]][0]
            seed_demo_room(labs_by_code[lab_code], spec, repair_existing=True)
        fill_known_demo_room_locations()
        # Exercise the fixed-equipment constraint on fresh demo installations.
        # Never rewrite a resource or equipment record already present.
        if "EQ-ROB-FIX-01" in new_equipment_codes:
            robot=Resource.objects.get(code="EQ-ROB-FIX-01")
            robot.equipment.portable=False
            robot.equipment.access_room=Resource.objects.get(code="ROOM-201")
            robot.equipment.save(update_fields=["portable","access_room"])
        policy,_=Policy.objects.get_or_create(pk=1)
        PolicyRevision.objects.get_or_create(policy=policy,version=policy.version,defaults={"data":policy.snapshot(),"reason":"课程案例初始参数"})
        provision_private_applicants()
        self.stdout.write("Demo seed ready: 7 accounts, 3 demo lab units, 9 equipment, 6 demo room resources.")
