import uuid
from urllib.parse import urlsplit
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
    portable = models.BooleanField("可独立外借", default=True)
    access_room = models.ForeignKey(Resource, null=True, blank=True, on_delete=models.PROTECT, related_name="dependent_equipment")
    handover_location = models.CharField("交接地点", max_length=120, blank=True)
    class Meta: verbose_name = "设备属性"

class RoomDetail(models.Model):
    resource = models.OneToOneField(Resource, on_delete=models.PROTECT, related_name="room")
    location_label = models.CharField("场地具体位置", max_length=160, blank=True)
    capacity = models.PositiveIntegerField("容纳人数", default=30)
    space_category = models.CharField("场地类别（本项目目录分类）", max_length=80, blank=True)
    facilities = models.TextField("设施清单（逐项说明）", blank=True)
    safety_note = models.TextField("兼容说明（不作为正式规程）", blank=True)
    class Meta:
        verbose_name = "场地属性"
        constraints = [models.CheckConstraint(condition=Q(capacity__gt=0), name="room_capacity_positive")]

class SafetyProcedure(models.Model):
    """Versioned safety information; published content is immutable."""
    STATUS = [("DRAFT", "草稿"), ("PENDING_REVIEW", "待审核"),
              ("RETURNED", "退回修订"), ("PUBLISHED", "已发布"), ("SUPERSEDED", "已被新版本替代")]
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="safety_procedures")
    version = models.PositiveIntegerField("版本号")
    title = models.CharField("规程标题", max_length=160)
    risk_categories = models.TextField("风险类别", blank=True)
    applicable_users = models.TextField("适用对象", blank=True)
    prerequisites = models.TextField("预约或进入前置条件", blank=True)
    required_ppe = models.TextField("个人防护用品", blank=True)
    pre_use_checks = models.TextField("使用前检查", blank=True)
    operating_cautions = models.TextField("操作注意事项", blank=True)
    prohibited_actions = models.TextField("禁止事项", blank=True)
    incident_route = models.TextField("异常处置路径", blank=True)
    source_title = models.CharField("来源文件或制度标题", max_length=200, blank=True)
    source_reference = models.CharField("来源链接或文件编号", max_length=500, blank=True)
    source_version = models.CharField("来源版本", max_length=80, blank=True)
    source_effective_date = models.DateField("来源生效日期", null=True, blank=True)
    effective_from = models.DateField("本版本生效日期", null=True, blank=True)
    review_due_date = models.DateField("复核期限", null=True, blank=True)
    requires_acknowledgement = models.BooleanField("要求阅读确认", default=False)
    status = models.CharField("审核状态", max_length=20, choices=STATUS, default="DRAFT")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="safety_drafts")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="safety_reviews")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField("复核意见", blank=True)
    published_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="safety_publications")
    published_at = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField("编制或修订原因")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "场地安全规程版本"
        ordering = ["resource_id", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["resource", "version"], name="safety_resource_version_unique"),
            models.UniqueConstraint(fields=["resource"], condition=Q(status="PUBLISHED"), name="one_published_safety_per_resource"),
        ]

    @property
    def lab_id(self):
        return self.resource.lab_id

    @property
    def source_url(self):
        parts = urlsplit(self.source_reference or "")
        return self.source_reference if parts.scheme == "https" and parts.netloc else ""

    def save(self, *args, **kwargs):
        if not self.pk:
            if self.status != "DRAFT":
                raise ValidationError("安全规程必须从草稿状态创建")
            return super().save(*args, **kwargs)

        previous = type(self).objects.get(pk=self.pk)
        identity = ["resource_id", "version", "created_by_id", "created_at"]
        if any(getattr(previous, field) != getattr(self, field) for field in identity):
            raise ValidationError("安全规程的资源、版本和创建记录不可变更")
        content = ["title", "risk_categories", "applicable_users", "prerequisites", "required_ppe",
                   "pre_use_checks", "operating_cautions", "prohibited_actions", "incident_route",
                   "source_title", "source_reference", "source_version", "source_effective_date",
                   "effective_from", "review_due_date", "requires_acknowledgement", "change_reason"]
        if previous.status != "DRAFT" and any(getattr(previous, field) != getattr(self, field) for field in content):
            raise ValidationError("已提交审核或已发布规程内容不可覆盖；请另建版本并保留历史记录")
        attribution = ["reviewed_by_id", "reviewed_at", "published_by_id", "published_at"]
        submission_time_changed = previous.status != "DRAFT" and previous.submitted_at != self.submitted_at
        if submission_time_changed:
            raise ValidationError("安全规程提交时间不可变更")
        attribution_changed = any(getattr(previous, field) != getattr(self, field) for field in attribution)
        if attribution_changed:
            is_first_review_decision = previous.status == "PENDING_REVIEW" and self.status in {"PUBLISHED", "RETURNED"}
            rewrites_existing_attribution = any(
                getattr(previous, field) is not None and getattr(previous, field) != getattr(self, field)
                for field in attribution
            )
            if not is_first_review_decision or rewrites_existing_attribution:
                raise ValidationError("安全规程复核和发布归属记录不可覆盖")
        if previous.review_note != self.review_note:
            if previous.status != "PENDING_REVIEW" or self.status not in {"PUBLISHED", "RETURNED"}:
                raise ValidationError("安全规程复核意见只可在首次审核决定时记录，之后不可覆盖")
            if not self.review_note.strip():
                raise ValidationError("复核发布或退回修订时必须记录复核意见")
        allowed_transitions = {
            "DRAFT": {"DRAFT", "PENDING_REVIEW"},
            "PENDING_REVIEW": {"PENDING_REVIEW", "PUBLISHED", "RETURNED"},
            "RETURNED": {"RETURNED"},
            "PUBLISHED": {"PUBLISHED", "SUPERSEDED"},
            "SUPERSEDED": {"SUPERSEDED"},
        }
        if self.status not in allowed_transitions.get(previous.status, set()):
            raise ValidationError("安全规程状态不能回退或跳级")
        if self.status in {"PENDING_REVIEW", "PUBLISHED"}:
            required = ["title", "risk_categories", "applicable_users", "prerequisites", "required_ppe",
                        "pre_use_checks", "operating_cautions", "prohibited_actions", "incident_route",
                        "source_title", "source_reference", "source_version", "source_effective_date",
                        "effective_from", "review_due_date", "change_reason"]
            if any(not str(getattr(self, field) or "").strip() for field in required):
                raise ValidationError("提交复核前须填写完整的安全信息正文与来源版本")
            if self.effective_from > timezone.localdate() or self.source_effective_date > self.effective_from:
                raise ValidationError("来源和生效日期不符合版本发布顺序")
            if self.review_due_date < max(self.effective_from, timezone.localdate()):
                raise ValidationError("复核期限不得早于本版本生效日或当前日期")
        if previous.status == "DRAFT" and self.status == "PENDING_REVIEW" and not self.submitted_at:
            raise ValidationError("提交复核时必须记录提交时间")
        if previous.status == "PENDING_REVIEW" and self.status in {"PUBLISHED", "RETURNED"}:
            if not self.reviewed_by_id or not self.reviewed_at or self.reviewed_by_id == self.created_by_id:
                raise ValidationError("提交人不能复核本人版本；审核决定须记录其他复核人和时间")
            if not self.review_note.strip():
                raise ValidationError("复核发布或退回修订时必须记录复核意见")
            if self.status == "PUBLISHED":
                if not self.published_by_id or self.reviewed_by_id != self.published_by_id or not self.published_at:
                    raise ValidationError("发布时必须记录同一复核发布人及发布时间")
            elif self.published_by_id or self.published_at:
                raise ValidationError("退回修订不能生成发布时间或发布人")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("安全规程版本不可删除")

class SafetyAcknowledgement(models.Model):
    procedure = models.ForeignKey(SafetyProcedure, on_delete=models.PROTECT, related_name="acknowledgements")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="safety_acknowledgements")
    acknowledged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "安全信息阅读确认"
        constraints = [models.UniqueConstraint(fields=["procedure", "user"], name="safety_ack_user_version_unique")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("阅读确认只能追加，不可覆盖")
        if self.procedure.status != "PUBLISHED":
            raise ValidationError("只能确认已发布的安全信息")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("阅读确认只能追加，不可删除")

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
    # Optional room scope. A room-specific weekly schedule overrides its lab's
    # weekly schedule; lab-wide calendar exceptions still apply to all rooms.
    resource = models.ForeignKey(Resource, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="opening_periods")
    weekday = models.PositiveSmallIntegerField("星期1至7")
    starts_at = models.TimeField("开放时间")
    ends_at = models.TimeField("关闭时间")
    closed_all_day = models.BooleanField("该星期全天关闭", default=False)
    valid_from = models.DateField(default=timezone.localdate)
    valid_to = models.DateField(null=True, blank=True)
    class Meta:
        verbose_name = "日间开放时段"
        constraints = [
            models.CheckConstraint(condition=Q(weekday__gte=1, weekday__lte=7), name="opening_weekday_valid"),
            models.CheckConstraint(condition=(Q(closed_all_day=True, starts_at=F("ends_at")) | Q(closed_all_day=False, starts_at__lt=F("ends_at"))), name="opening_time_order"),
            models.UniqueConstraint(fields=["lab", "weekday", "starts_at", "valid_from"], condition=Q(resource__isnull=True), name="opening_lab_start_unique"),
            models.UniqueConstraint(fields=["resource", "weekday", "starts_at", "valid_from"], condition=Q(resource__isnull=False), name="opening_room_start_unique"),
        ]
    def clean(self):
        if self.closed_all_day and self.starts_at != self.ends_at:
            raise ValidationError("全天关闭日历不应包含开放时段")
        if not self.closed_all_day and self.starts_at >= self.ends_at:
            raise ValidationError("关闭时间必须晚于开放时间")
        if self.resource_id:
            resource=Resource.objects.only("kind","lab_id").get(pk=self.resource_id)
            if resource.kind!="ROOM" or resource.lab_id!=self.lab_id:
                raise ValidationError("场地开放日历必须指向同一管理单元内的实体场地")
    def save(self,*args,**kwargs):
        self.clean()
        return super().save(*args,**kwargs)

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
    review_required = models.BooleanField("暂停交接待复核", default=False)
    review_reason = models.TextField("待复核原因", blank=True)
    arrived_at = models.DateTimeField("已核实到场时间", null=True, blank=True)
    late_start_until = models.DateTimeField("协调后的最晚开始时间", null=True, blank=True)
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
    blocks_service = models.BooleanField("阻断服务", default=True)
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


class ReservationReview(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="rechecks")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    accepted = models.BooleanField()
    reason = models.TextField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "预约复核历史"


class Coordination(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="coordinations")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    action = models.CharField(max_length=20, choices=[("WAIT", "前序延误等待"), ("CANCEL", "协调取消")])
    reason = models.TextField()
    old_state = models.JSONField(default=dict)
    new_state = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "预约协调凭据"


class CalendarException(models.Model):
    lab = models.ForeignKey(Lab, on_delete=models.PROTECT, related_name="calendar_exceptions")
    day = models.DateField()
    closed = models.BooleanField(default=True)
    starts_at = models.TimeField(null=True, blank=True)
    ends_at = models.TimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    reason = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "单日开放例外版本"
        constraints = [
            models.UniqueConstraint(fields=["lab", "day"], condition=Q(active=True), name="one_active_calendar_exception"),
            models.CheckConstraint(condition=Q(closed=True) | Q(starts_at__isnull=False, ends_at__isnull=False, starts_at__lt=F("ends_at")), name="calendar_exception_order"),
        ]


class IncidentResponse(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="responses")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=[("ACK", "确认知悉"), ("DISPUTE", "提出异议"), ("SUPPLEMENT", "追加事实"), ("REVIEW", "独立复核结论")])
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "异常确认与异议记录"


class JobHeartbeat(models.Model):
    name = models.CharField(max_length=40, unique=True)
    last_success = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        verbose_name = "后台任务健康记录"
