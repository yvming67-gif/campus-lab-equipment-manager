from django import forms
from django.utils import timezone
from .models import *
class LocalDateTime(forms.DateTimeField):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs, input_formats=["%Y-%m-%dT%H:%M:%S","%Y-%m-%dT%H:%M","%Y-%m-%d %H:%M"],widget=forms.DateTimeInput(attrs={"type":"datetime-local","step":"1"},format="%Y-%m-%dT%H:%M:%S"))
class BookingForm(forms.Form):
    starts_at=LocalDateTime(label="开始时间")
    ends_at=LocalDateTime(label="结束时间")
    purpose=forms.CharField(label="使用目的",max_length=1000,widget=forms.Textarea(attrs={"rows":3}))
    participants=forms.IntegerField(label="参与人数",min_value=1,initial=1)
    request_key=forms.UUIDField(widget=forms.HiddenInput)
    def clean(self):
        data=super().clean();a,b=data.get("starts_at"),data.get("ends_at")
        if a and b and a>=b:self.add_error("ends_at","结束时间必须晚于开始时间")
        return data
class QueryForm(forms.Form):
    q=forms.CharField(label="资源名称或编号",required=False)
    kind=forms.ChoiceField(label="资源类型",required=False,choices=[("","全部" )]+Resource.KIND)
    lab=forms.ModelChoiceField(label="所属实验室",queryset=Lab.objects.all(),required=False,empty_label="全部实验室")
    starts_at=LocalDateTime(label="查询开始时间",required=False)
    ends_at=LocalDateTime(label="查询结束时间",required=False)
    def clean(self):
        d=super().clean();a,b=d.get("starts_at"),d.get("ends_at")
        if bool(a)!=bool(b):raise forms.ValidationError("查询时段须同时填写开始与结束时间")
        if a and b and a>=b:raise forms.ValidationError("查询开始时间必须早于结束时间")
        return d
class ActionForm(forms.Form):
    reason=forms.CharField(label="操作原因或说明",max_length=1500,widget=forms.Textarea(attrs={"rows":3}))
class ReviewForm(ActionForm):
    choice=forms.ChoiceField(label="审核决定",choices=[("approve","通过"),("reject","驳回"),("info","要求补充说明")])
    version=forms.IntegerField(widget=forms.HiddenInput)
class CheckoutForm(forms.Form):
    school_no=forms.CharField(label="现场核验借用人编号",max_length=32)
    tag=forms.CharField(label="现场扫描设备标签",max_length=40)
    arrived_at=LocalDateTime(label="到场排队时间")
    service_started_at=LocalDateTime(label="开始办理时间")
    accessories_ok=forms.BooleanField(label="设备及全部配件完好（未勾选将隔离设备）",required=False,initial=True)
    condition=forms.CharField(label="设备与配件检查结论",max_length=1000,initial="设备运行正常，配件齐全")
class ReturnForm(forms.Form):
    tag=forms.CharField(label="现场扫描设备标签",max_length=40)
    note=forms.CharField(label="归还检查记录",widget=forms.Textarea(attrs={"rows":3}),max_length=1500)
    returned_at=LocalDateTime(label="实际交还时间")
    damaged=forms.BooleanField(label="设备损坏",required=False)
    missing=forms.BooleanField(label="配件缺失",required=False)
    fault=forms.BooleanField(label="运行状态异常",required=False)
    def __init__(self,*args,loan=None,**kwargs):
        super().__init__(*args,**kwargs)
        if loan:
            for item in loan.items.all():self.fields[f"qty_{item.pk}"]=forms.IntegerField(label=f"归还 {item.name_snapshot}（借出 {item.out_qty} 件）",min_value=0,max_value=item.out_qty,initial=item.out_qty)
class RoomStartForm(forms.Form):
    school_no=forms.CharField(label="现场核验负责教师编号",max_length=32)
    participants=forms.IntegerField(label="实际参与人数",min_value=1)
    arrived_at=LocalDateTime(label="到场时间")
    note=forms.CharField(label="入场检查结论",max_length=1000,initial="教师身份、人数与场地状态已核对")
class RoomEndForm(forms.Form):
    ended_at=LocalDateTime(label="实际结束时间")
    note=forms.CharField(label="场地离场检查结论",max_length=1500,widget=forms.Textarea(attrs={"rows":3}))
    abnormal=forms.BooleanField(label="场地存在异常",required=False)
class IncidentForm(forms.Form):
    resource=forms.ModelChoiceField(label="异常资源",queryset=Resource.objects.all())
    reservation=forms.ModelChoiceField(label="关联预约（可选）",queryset=Reservation.objects.none(),required=False)
    kind=forms.ChoiceField(label="异常类型",choices=Incident._meta.get_field("kind").choices)
    description=forms.CharField(label="具体情况",max_length=2000,widget=forms.Textarea(attrs={"rows":4}))
    occurred_at=LocalDateTime(label="发现时间")
class VerifyForm(ActionForm):
    choice=forms.ChoiceField(label="处置",choices=[("confirm","核实确认"),("dismiss","核实排除"),("resolve","无需维修，已处置完成")])
class WorkCreateForm(ActionForm):
    technician=forms.ModelChoiceField(label="指派维修人员",queryset=User.objects.filter(is_active=True,roles__code="TECHNICIAN").distinct())
class WorkUpdateForm(ActionForm):
    choice=forms.ChoiceField(label="操作",choices=[("START","开始维修"),("PROGRESS","追加过程记录"),("SUBMIT","提交维修结果，等待验收")])
class AcceptanceForm(ActionForm):
    choice=forms.ChoiceField(label="验收结论",choices=[("pass","通过"),("fail","退回继续维修")])
class EvidenceForm(forms.Form):
    file=forms.FileField(label="证据附件（PNG、JPEG 或 PDF，最大5MB）")
class ResourceForm(forms.Form):
    code=forms.CharField(label="唯一设备标签或场地编号",max_length=40)
    name=forms.CharField(label="资源名称",max_length=100)
    kind=forms.ChoiceField(label="类型",choices=Resource.KIND)
    lab=forms.ModelChoiceField(label="所属实验室",queryset=Lab.objects.all())
    description=forms.CharField(label="资源说明",required=False,widget=forms.Textarea(attrs={"rows":2}))
    category=forms.CharField(label="设备类别（设备必填）",required=False,max_length=60)
    risk=forms.ChoiceField(label="设备分级",choices=EquipmentDetail._meta.get_field("risk").choices)
    qualification=forms.ModelChoiceField(label="要求培训资质",queryset=QualificationType.objects.all(),required=False)
    portable=forms.BooleanField(label="可独立外借（固定设备取消勾选）",required=False,initial=True)
    access_room=forms.ModelChoiceField(label="固定设备关联场地",queryset=Resource.objects.filter(kind='ROOM'),required=False)
    handover_location=forms.CharField(label="设备交接地点",max_length=120,required=False)
    capacity=forms.IntegerField(label="场地容量",required=False,min_value=1)
    accessories=forms.CharField(label="配件（每行：编码,名称,数量；保留历史交接快照）",required=False,widget=forms.Textarea(attrs={"rows":3}))
    reason=forms.CharField(label="变更依据",max_length=1000)
class GrantForm(forms.Form):
    user=forms.ModelChoiceField(label="用户",queryset=User.objects.all())
    roles=forms.ModelMultipleChoiceField(label="角色",queryset=Role.objects.all(),required=False,widget=forms.CheckboxSelectMultiple)
    labs=forms.ModelMultipleChoiceField(label="管理员负责实验室",queryset=Lab.objects.all(),required=False,widget=forms.CheckboxSelectMultiple)
    qualification=forms.ModelChoiceField(label="调整的培训资格（可选）",queryset=QualificationType.objects.all(),required=False)
    expires_at=LocalDateTime(label="资格有效期（有资格时必填）",required=False)
    revoke=forms.BooleanField(label="撤销该培训资格",required=False)
    active=forms.BooleanField(label="账号有效",required=False)
    reason=forms.CharField(label="授权或变更依据",max_length=1000)
class PolicyForm(forms.Form):
    window_days=forms.IntegerField(label="可提前预约天数",min_value=1,max_value=365)
    slot_minutes=forms.ChoiceField(label="时间粒度（分钟）",choices=[(15,"15"),(30,"30"),(60,"60")])
    max_hours=forms.IntegerField(label="单次最长小时",min_value=1,max_value=24)
    equipment_quota=forms.IntegerField(label="未来设备预约配额",min_value=1,max_value=100)
    room_quota=forms.IntegerField(label="未来场地预约配额",min_value=1,max_value=100)
    simultaneous_equipment=forms.IntegerField(label="同时设备使用上限",min_value=1,max_value=100)
    grace_minutes=forms.IntegerField(label="迟到宽限（分钟）",min_value=1,max_value=120)
    mock_delivery_failure=forms.BooleanField(label="模拟外部消息服务不可用（仅演示）",required=False)
    reason=forms.CharField(label="修改依据",max_length=1000)
class LabForm(forms.Form):
    code=forms.CharField(label="实验室编号",max_length=32)
    name=forms.CharField(label="实验室名称",max_length=80)
    location=forms.CharField(label="地点",max_length=120)
    description=forms.CharField(label="说明",required=False,widget=forms.Textarea(attrs={"rows":2}))
    reason=forms.CharField(label="创建依据",max_length=1000)
class OpeningForm(forms.Form):
    lab=forms.ModelChoiceField(label="实验室",queryset=Lab.objects.all())
    weekday=forms.IntegerField(label="星期（1至7）",min_value=1,max_value=7)
    starts_at=forms.TimeField(label="开放时间",widget=forms.TimeInput(attrs={"type":"time"}))
    ends_at=forms.TimeField(label="关闭时间",widget=forms.TimeInput(attrs={"type":"time"}))
    effective=forms.DateField(label="生效日期（至少为明天）",widget=forms.DateInput(attrs={"type":"date"}))
    reason=forms.CharField(label="变更原因",max_length=1000)


class CoordinationForm(ActionForm):
    action=forms.ChoiceField(label="协调方式",choices=[('WAIT','前序延误：登记到场并保留原结束时间'),('CANCEL','协调取消未使用预约')])
    arrived_at=LocalDateTime(label="实际到场时间（等待时必填）",required=False)


class RecheckForm(ActionForm):
    accepted=forms.ChoiceField(label="复核结论",choices=[('yes','复核通过'),('no','不能执行，协调取消')])
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)


class CalendarExceptionForm(ActionForm):
    lab=forms.ModelChoiceField(label="实验室",queryset=Lab.objects.all())
    day=forms.DateField(label="生效日期",widget=forms.DateInput(attrs={'type':'date'}))
    closed=forms.BooleanField(label="全天关闭（取消勾选时填写例外开放时段）",required=False,initial=True)
    starts_at=forms.TimeField(label="例外开放时间",required=False,widget=forms.TimeInput(attrs={'type':'time'}))
    ends_at=forms.TimeField(label="例外关闭时间",required=False,widget=forms.TimeInput(attrs={'type':'time'}))


class IncidentResponseForm(ActionForm):
    kind=forms.ChoiceField(label="记录类型",choices=IncidentResponse._meta.get_field('kind').choices)
