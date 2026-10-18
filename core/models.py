import uuid
from django.db import models
from django.db.models import Q, F
from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators, RangeBoundary
from django.db.models import Func
from django.core.exceptions import ValidationError
from django.utils import timezone

def number():
    return uuid.uuid4().hex[:16].upper()

class Role(models.Model):
    code = models.CharField("角色代码", max_length=20, unique=True)
    name = models.CharField("角色名称", max_length=40)
    class Meta: verbose_name = "业务角色"
    def __str__(self): return self.name

class User(AbstractUser):
    school_no = models.CharField("学校唯一编号", max_length=32, unique=True)
    display_name = models.CharField("显示名", max_length=60)
    roles = models.ManyToManyField(Role, through="UserRole", through_fields=("user","role"), related_name="users")
    class Meta: verbose_name = "身份映射"
    def has_role(self, *codes):
        return self.is_active and self.roles.filter(code__in=codes).exists()
    def __str__(self): return self.display_name

class UserRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    granted_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="role_grants")
    granted_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "用户角色关联"
        constraints = [models.UniqueConstraint(fields=["user", "role"], name="user_role_unique")]

class Lab(models.Model):
    code = models.CharField("实验室编号", max_length=32, unique=True)
    name = models.CharField("名称", max_length=80)
    location = models.CharField("地点", max_length=120)
    description = models.TextField("说明", blank=True)
    is_active = models.BooleanField("开放服务", default=True)
    class Meta: verbose_name = "实验室"
    def __str__(self): return self.name

class LabGrant(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    lab = models.ForeignKey(Lab, on_delete=models.PROTECT)
    granted_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "实验室管理范围"
        constraints = [models.UniqueConstraint(fields=["user", "lab"], name="lab_grant_unique")]

class QualificationType(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    class Meta: verbose_name = "培训资格类型"
    def __str__(self): return self.name

class UserQualification(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    qualification = models.ForeignKey(QualificationType, on_delete=models.PROTECT)
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    granted_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="qualification_grants")
    class Meta:
        verbose_name = "用户培训资格"
        constraints = [models.UniqueConstraint(fields=["user", "qualification"], name="user_qualification_unique")]

class Resource(models.Model):
    KIND = [("EQUIPMENT", "设备"), ("ROOM", "场地")]
    STATUS = [("NORMAL", "正常"), ("ISOLATED", "异常隔离"), ("MAINTENANCE", "维护中"), ("DISABLED", "停用"), ("SCRAPPED", "报废")]
    code = models.CharField("唯一标签或场地编号", max_length=40, unique=True)
    name = models.CharField("资源名称", max_length=100)
    kind = models.CharField("资源类型", max_length=16, choices=KIND)
    lab = models.ForeignKey(Lab, on_delete=models.PROTECT, related_name="resources")
    description = models.TextField("资源说明", blank=True)
    service_status = models.CharField("服务状态", max_length=20, choices=STATUS, default="NORMAL")
    manual_hold = models.CharField("人工服务限制", max_length=16, choices=[("", "无"), ("DISABLED", "停用"), ("SCRAPPED", "报废")], blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "可预约资源"
        indexes = [models.Index(fields=["kind", "lab", "service_status"])]
    def __str__(self): return self.name
    @property
    def requires_approval(self):
        return self.kind == "ROOM" or self.equipment.risk != "NORMAL" or self.equipment.qualification_id is not None

class EquipmentDetail(models.Model):
    resource = models.OneToOneField(Resource, on_delete=models.PROTECT, related_name="equipment")
    category = models.CharField("设备类别", max_length=60)
    risk = models.CharField("受限级别", max_length=20, choices=[("NORMAL", "普通"), ("HIGH_VALUE", "高价值"), ("HAZARDOUS", "危险")], default="NORMAL")
    qualification = models.ForeignKey(QualificationType, null=True, blank=True, on_delete=models.PROTECT)
    physical_status = models.CharField("实物状态", max_length=16, choices=[("IN_STOCK", "在库"), ("BORROWED", "借出中")], default="IN_STOCK")
    class Meta: verbose_name = "设备属性"

class RoomDetail(models.Model):
    resource = models.OneToOneField(Resource, on_delete=models.PROTECT, related_name="room")
    capacity = models.PositiveIntegerField("容纳人数", default=30)
    safety_note = models.TextField("安全说明", blank=True)
    class Meta:
        verbose_name = "场地属性"
        constraints = [models.CheckConstraint(condition=Q(capacity__gt=0), name="room_capacity_positive")]

class Accessory(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="accessories")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    class Meta:
        verbose_name = "设备配件档案"
        constraints = [models.UniqueConstraint(fields=["resource", "code"], name="resource_accessory_unique"), models.CheckConstraint(condition=Q(quantity__gt=0), name="accessory_quantity_positive")]

class OpeningPeriod(models.Model):
    lab = models.ForeignKey(Lab, on_delete=models.PROTECT, related_name="opening_periods")
    weekday = models.PositiveSmallIntegerField("星期1至7")
    starts_at = models.TimeField("开放时间")
    ends_at = models.TimeField("关闭时间")
    valid_from = models.DateField(default=timezone.localdate)
    valid_to = models.DateField(null=True, blank=True)
    class Meta:
        verbose_name = "日间开放时段"
        constraints = [models.CheckConstraint(condition=Q(weekday__gte=1, weekday__lte=7), name="opening_weekday_valid"), models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="opening_time_order"), models.UniqueConstraint(fields=["lab", "weekday", "starts_at", "valid_from"], name="opening_start_unique")]

class Policy(models.Model):
    version = models.PositiveIntegerField(default=1)
    window_days = models.PositiveIntegerField(default=14)
    slot_minutes = models.PositiveIntegerField(default=30)
    max_hours = models.PositiveIntegerField(default=8)
    equipment_quota = models.PositiveIntegerField(default=3)
    room_quota = models.PositiveIntegerField(default=3)
    simultaneous_equipment = models.PositiveIntegerField(default=3)
    grace_minutes = models.PositiveIntegerField(default=30)
    mock_delivery_failure = models.BooleanField(default=False)
    updated_at = models.DateTimeField(default=timezone.now)
    class Meta: verbose_name = "当前业务参数"
    def snapshot(self):
        return {f.name: getattr(self, f.name) for f in self._meta.fields if f.name not in ["id", "updated_at"]}

class PolicyRevision(models.Model):
    policy = models.ForeignKey(Policy, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    data = models.JSONField()
    reason = models.TextField()
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "参数历史版本"
        constraints = [models.UniqueConstraint(fields=["policy", "version"], name="policy_version_unique")]

class TsRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()

class Reservation(models.Model):
    STATUS = [("PENDING", "待审核"), ("CONFIRMED", "已确认"), ("REJECTED", "已驳回"), ("CANCELLED", "已取消"), ("EXPIRED", "已失效"), ("IN_USE", "使用中"), ("COMPLETED", "已完成")]
    code = models.CharField(max_length=40, unique=True, default=number)
    applicant = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reservations")
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="reservations")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    purpose = models.TextField()
    participants = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS)
    approval_mode = models.CharField(max_length=10, choices=[("AUTO", "自动确认"), ("MANUAL", "人工审核")])
    request_key = models.UUIDField(default=uuid.uuid4)
    request_fingerprint = models.CharField(max_length=64)
    policy_snapshot = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "资源预约"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["applicant", "status", "starts_at"]), models.Index(fields=["resource", "starts_at", "ends_at"])]
        constraints = [models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="reservation_time_order"), models.UniqueConstraint(fields=["applicant", "request_key"], name="reservation_request_unique"), ExclusionConstraint(name="resource_confirmed_no_overlap", expressions=[("resource", RangeOperators.EQUAL), (TsRange("starts_at", "ends_at", RangeBoundary()), RangeOperators.OVERLAPS)], condition=Q(status__in=["CONFIRMED", "IN_USE"]))]

class ReviewNote(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="review_notes")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=[("REQUEST_INFO", "要求补充"), ("PROVIDE_INFO", "补充材料")])
    content = models.TextField()
    evidence_ref = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta: verbose_name = "审核补充记录"

class ApprovalDecision(models.Model):
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="decision")
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    mode = models.CharField(max_length=10)
    approved = models.BooleanField()
    reason = models.TextField()
    decided_at = models.DateTimeField(default=timezone.now)
    class Meta: verbose_name = "自动或人工确认决定"

class Loan(models.Model):
    code = models.CharField(max_length=40, unique=True, default=number)
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="loan")
    operator = models.ForeignKey(User, on_delete=models.PROTECT)
    arrived_at = models.DateTimeField()
    service_started_at = models.DateTimeField()
    handed_over_at = models.DateTimeField()
    due_at = models.DateTimeField()
    tag_snapshot = models.CharField(max_length=40)
    condition_out = models.TextField()
    class Meta: verbose_name = "设备借出事实"

class ReturnRecord(models.Model):
    code = models.CharField(max_length=40, unique=True, default=number)
    loan = models.OneToOneField(Loan, on_delete=models.PROTECT, related_name="return_record")
    operator = models.ForeignKey(User, on_delete=models.PROTECT)
    returned_at = models.DateTimeField()
    damaged = models.BooleanField(default=False)
    missing = models.BooleanField(default=False)
    fault = models.BooleanField(default=False)
    note = models.TextField()
    class Meta: verbose_name = "设备归还事实"
    @property
    def overdue_seconds(self): return max(0, int((self.returned_at - self.loan.due_at).total_seconds()))

class HandoverItem(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="items")
    accessory = models.ForeignKey(Accessory, null=True, on_delete=models.PROTECT)
    code_snapshot = models.CharField(max_length=32)
    name_snapshot = models.CharField(max_length=80)
    expected_qty = models.PositiveIntegerField()
    out_qty = models.PositiveIntegerField()
    out_condition = models.CharField(max_length=160)
    return_qty = models.PositiveIntegerField(null=True, blank=True)
    return_condition = models.CharField(max_length=160, blank=True)
    class Meta:
        verbose_name = "配件交接快照"
        constraints = [models.UniqueConstraint(fields=["loan", "code_snapshot"], name="handover_item_unique")]

class RoomUse(models.Model):
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="room_use")
    started_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="room_starts")
    arrived_at = models.DateTimeField()
    started_at = models.DateTimeField()
    actual_participants = models.PositiveIntegerField()
    start_note = models.TextField()
    ended_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="room_ends")
    ended_at = models.DateTimeField(null=True, blank=True)
    end_note = models.TextField(blank=True)
    abnormal = models.BooleanField(default=False)
    class Meta: verbose_name = "场地实际使用与检查"

class Incident(models.Model):
    STATUS = [("PENDING", "待核实"), ("CONFIRMED", "已确认"), ("DISMISSED", "已排除"), ("RESOLVED", "已解决")]
    code = models.CharField(max_length=40, unique=True, default=number)
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="incidents")
    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reports")
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=[("DAMAGE", "损坏"), ("MISSING", "缺件"), ("FAULT", "运行或场地异常")])
    description = models.TextField()
    occurred_at = models.DateTimeField()
    reported_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=STATUS, default="PENDING")
    confirmed_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="confirmed_incidents")
    conclusion = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    class Meta: verbose_name = "资源异常事实"

class Evidence(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="evidence")
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    file = models.FileField(upload_to="evidence/%Y/%m/")
    original_name = models.CharField(max_length=160)
    sha256 = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(default=timezone.now)
    class Meta: verbose_name = "异常证据索引"

class WorkOrder(models.Model):
    STATUS = [("OPEN", "待处理"), ("IN_PROGRESS", "处理中"), ("AWAITING_ACCEPTANCE", "待验收"), ("CLOSED", "已关闭")]
    code = models.CharField(max_length=40, unique=True, default=number)
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="work_orders")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_work")
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, related_name="assigned_work")
    status = models.CharField(max_length=24, choices=STATUS, default="OPEN")
    priority = models.CharField(max_length=12, default="NORMAL")
    result = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="accepted_work")
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "维修工单"
        constraints = [models.UniqueConstraint(fields=["incident"], condition=~Q(status="CLOSED"), name="one_open_work_per_incident")]

class WorkOrderEvent(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    old_status = models.CharField(max_length=24)
    new_status = models.CharField(max_length=24)
    note = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    class Meta: verbose_name = "维修及验收过程"

class AuditEvent(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    actor_kind = models.CharField(max_length=8, default="USER")
    entity_type = models.CharField(max_length=40)
    entity_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=40)
    old_state = models.JSONField(default=dict)
    new_state = models.JSONField(default=dict)
    reason = models.TextField()
    request_id = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(default=timezone.now)
    lab = models.ForeignKey(Lab, null=True, on_delete=models.PROTECT)
    dedupe_key = models.CharField(max_length=160, null=True, unique=True)
    class Meta:
        verbose_name = "不可覆盖审计事件"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]
    def save(self, *args, **kwargs):
        if self.pk: raise ValidationError("审计只能追加，不可覆盖")
        return super().save(*args, **kwargs)

class ServicePeriod(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="service_periods")
    audit = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    status = models.CharField(max_length=20)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        verbose_name = "资源服务状态区间"
        constraints = [models.UniqueConstraint(fields=["resource"], condition=Q(ends_at__isnull=True), name="one_open_service_period"), models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(starts_at__lt=F("ends_at")), name="service_period_time_order"), ExclusionConstraint(name="service_period_no_overlap", expressions=[("resource", RangeOperators.EQUAL), (TsRange("starts_at", "ends_at", RangeBoundary()), RangeOperators.OVERLAPS)])]

class Notification(models.Model):
    code = models.CharField(max_length=40, unique=True, default=number)
    audit = models.ForeignKey(AuditEvent, on_delete=models.PROTECT)
    recipient = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=32)
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        verbose_name = "接收人通知"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["audit", "recipient", "kind"], name="notification_recipient_unique")]

class Delivery(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.PROTECT, related_name="deliveries")
    channel = models.CharField(max_length=16, default="MOCK_EXTERNAL")
    status = models.CharField(max_length=16, default="PENDING")
    retry_count = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    class Meta:
        verbose_name = "渠道投递任务"
        constraints = [models.UniqueConstraint(fields=["notification", "channel"], name="delivery_channel_unique")]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

class DeliveryAttempt(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.PROTECT, related_name="attempts")
    attempt_no = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=12)
    detail = models.TextField(blank=True)
    attempted_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = "投递尝试"
        constraints = [models.UniqueConstraint(fields=["delivery", "attempt_no"], name="delivery_attempt_unique")]

class MockReceipt(models.Model):
    delivery = models.OneToOneField(Delivery, on_delete=models.PROTECT)
    received_at = models.DateTimeField(default=timezone.now)
    class Meta: verbose_name = "模拟投递去重凭据"
