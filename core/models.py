from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q, F
from django.utils import timezone

class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = 'student', '学生'
        TEACHER = 'teacher', '教师'
        MANAGER = 'manager', '实验室管理员'
        TECHNICIAN = 'technician', '维修人员'
        SYSTEM = 'system', '系统管理员'
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    managed_labs = models.ManyToManyField('Laboratory', blank=True, related_name='managers')
    demo = models.BooleanField(default=False)

class Laboratory(models.Model):
    code = models.CharField('编号', max_length=30, unique=True)
    name = models.CharField('实验室名称', max_length=100)
    location = models.CharField('位置', max_length=150)
    opening = models.CharField('开放信息', max_length=200, default='课程模拟：每日全天开放，实际制度待确认')
    demo = models.BooleanField(default=False)
    def __str__(self): return self.name

class BusinessConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    advance_days = models.PositiveSmallIntegerField('提前天数', default=14)
    slot_minutes = models.PositiveSmallIntegerField('时间粒度（分钟）', default=30)
    max_hours = models.PositiveSmallIntegerField('单次最大小时数', default=8)
    max_future = models.PositiveSmallIntegerField('未来预约上限', default=3)
    grace_minutes = models.PositiveSmallIntegerField('领取宽限（分钟）', default=30)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='config_singleton'),
                       models.CheckConstraint(condition=Q(advance_days__gt=0, slot_minutes__gt=0,
                           max_hours__gt=0, max_future__gt=0, grace_minutes__gt=0), name='config_positive')]

class Equipment(models.Model):
    class Service(models.TextChoices):
        NORMAL = 'normal', '正常'
        QUARANTINE = 'quarantine', '异常隔离'
        REPAIR = 'repair', '维修中'
        DISABLED = 'disabled', '停用'
        RETIRED = 'retired', '报废'
    class Physical(models.TextChoices):
        STOCK = 'stock', '在库'
        OUT = 'out', '借出中'
    tag = models.CharField('唯一设备标签', max_length=40, unique=True)
    name = models.CharField('设备名称', max_length=100)
    category = models.CharField('设备类型', max_length=60)
    laboratory = models.ForeignKey(Laboratory, on_delete=models.PROTECT)
    description = models.TextField('说明', blank=True)
    qualification = models.CharField('所需资格代码', max_length=60, blank=True)
    high_value = models.BooleanField('高价值', default=False)
    dangerous = models.BooleanField('危险设备', default=False)
    physical = models.CharField(max_length=12, choices=Physical.choices, default=Physical.STOCK)
    service = models.CharField(max_length=20, choices=Service.choices, default=Service.NORMAL, db_index=True)
    demo = models.BooleanField(default=False)
    def __str__(self): return f'{self.tag} · {self.name}'

class Accessory(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name='accessories')
    name = models.CharField('配件', max_length=100)
    quantity = models.PositiveSmallIntegerField('数量', default=1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['equipment', 'name'], name='accessory_name_unique')]

class Qualification(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='qualifications')
    code = models.CharField('资格代码', max_length=60)
    expires_at = models.DateTimeField('有效至')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'code'], name='user_qualification_unique')]

class Reservation(models.Model):
    def __str__(self): return f'预约 #{self.pk} · {self.equipment.tag}'
    class Status(models.TextChoices):
        PENDING = 'pending', '待审核'
        APPROVED = 'approved', '已通过'
        REJECTED = 'rejected', '已驳回'
        CANCELLED = 'cancelled', '已取消'
        EXPIRED = 'expired', '已失效'
        BORROWED = 'borrowed', '借出中'
        COMPLETED = 'completed', '已完成'
    applicant = models.ForeignKey(User, on_delete=models.PROTECT)
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name='reservations')
    start = models.DateTimeField('开始时间')
    end = models.DateTimeField('结束时间')
    purpose = models.CharField('用途', max_length=300)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField('最近状态原因', blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    demo = models.BooleanField(default=False)
    class Meta:
        ordering = ['-id']
        constraints = [models.CheckConstraint(condition=Q(start__lt=F('end')), name='reservation_positive_interval')]
        indexes = [models.Index(fields=['equipment', 'status', 'start', 'end']),
                   models.Index(fields=['applicant', 'status', 'start'])]

class Review(models.Model):
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT)
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT)
    approved = models.BooleanField()
    reason = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

class Loan(models.Model):
    def __str__(self): return f'借用 #{self.pk} · {self.equipment.tag}'
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name='loan')
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT)
    borrower = models.ForeignKey(User, on_delete=models.PROTECT, related_name='loans')
    operator = models.ForeignKey(User, on_delete=models.PROTECT, related_name='issued_loans')
    issued_at = models.DateTimeField()
    due_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    accessories = models.JSONField(default=list)
    inspection = models.TextField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['equipment'], condition=Q(closed_at__isnull=True), name='one_open_loan_per_device')]
        indexes = [models.Index(fields=['borrower', 'closed_at', 'due_at'])]

class ReturnRecord(models.Model):
    class Condition(models.TextChoices):
        NORMAL = 'normal', '正常'
        DAMAGE = 'damage', '设备损坏'
        MISSING = 'missing', '配件缺失'
        FAULT = 'fault', '运行异常'
    loan = models.OneToOneField(Loan, on_delete=models.PROTECT, related_name='return_record')
    receiver = models.ForeignKey(User, on_delete=models.PROTECT)
    received_at = models.DateTimeField()
    condition = models.CharField(max_length=20, choices=Condition.choices)
    notes = models.TextField()
    overdue_seconds = models.PositiveIntegerField(default=0)

class Incident(models.Model):
    def __str__(self): return f'异常 #{self.pk} · {self.equipment.tag}'
    class Status(models.TextChoices):
        PENDING = 'pending', '待确认'
        CONFIRMED = 'confirmed', '已确认待解决'
        RESOLVED = 'resolved', '已解决'
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name='incidents')
    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name='incidents')
    loan = models.ForeignKey(Loan, null=True, blank=True, on_delete=models.PROTECT)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True)

class WorkOrder(models.Model):
    def __str__(self): return f'工单 #{self.pk} · {self.equipment.tag}'
    class Status(models.TextChoices):
        OPEN = 'open', '待处理'
        PROGRESS = 'progress', '处理中'
        ACCEPTANCE = 'acceptance', '待验收'
        CLOSED = 'closed', '已关闭'
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name='work_orders')
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name='work_orders')
    assignee = models.ForeignKey(User, on_delete=models.PROTECT, related_name='work_orders')
    creator = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_work_orders')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    progress = models.TextField(blank=True)
    result = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    accepted_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name='accepted_work_orders')
    closed_at = models.DateTimeField(null=True)

class ServiceInterval(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name='downtimes')
    start = models.DateTimeField()
    end = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['equipment'], condition=Q(end__isnull=True), name='one_open_downtime')]

class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=160, unique=True)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=12, default='pending', choices=[('pending','等待发送'),('sending','发送中'),('sent','已发送'),('failed','最终失败')])
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    last_error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True)

class MessageDelivery(models.Model):
    event_key = models.CharField(max_length=160, unique=True)
    delivered_at = models.DateTimeField(default=timezone.now)

class Audit(models.Model):
    object_type = models.CharField(max_length=40)
    object_id = models.PositiveBigIntegerField()
    old_state = models.CharField(max_length=100, blank=True)
    new_state = models.CharField(max_length=100)
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    reason = models.TextField()
    trace = models.CharField(max_length=100)
    class Meta: ordering = ['-id']

class CommandReceipt(models.Model):
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    key = models.CharField(max_length=100)
    operation = models.CharField(max_length=60)
    digest = models.CharField(max_length=64)
    result_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'key'], name='idempotency_actor_key')]
