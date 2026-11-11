from datetime import time
import re
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
    expected_approval_mode=forms.ChoiceField(choices=[("AUTO","规则通过后自动确认"),("MANUAL","提交后人工审核")],required=False,widget=forms.HiddenInput)
    def clean(self):
        data=super().clean();a,b=data.get("starts_at"),data.get("ends_at")
        if a and b and a>=b:self.add_error("ends_at","结束时间必须晚于开始时间")
        return data
class QueryForm(forms.Form):
    q=forms.CharField(label="资源名称或编号",required=False)
    kind=forms.ChoiceField(label="资源类型",required=False,choices=[("","全部" )]+Resource.KIND)
    campus=forms.ModelChoiceField(label="校区",queryset=Campus.objects.none(),required=False,empty_label="全部校区")
    building=forms.ModelChoiceField(label="楼宇",queryset=Building.objects.none(),required=False,empty_label="全部楼宇")
    lab=forms.ModelChoiceField(label="所属管理单元",queryset=Lab.objects.all(),required=False,empty_label="全部管理单元")
    category=forms.CharField(label="场地类别或设备类别",required=False,max_length=80)
    facility=forms.CharField(label="设施或配件",required=False,max_length=80)
    capacity_min=forms.IntegerField(label="最小容量（人，仅场地）",required=False,min_value=1,max_value=10000)
    capacity_max=forms.IntegerField(label="最大容量（人，仅场地）",required=False,min_value=1,max_value=10000)
    service_status=forms.ChoiceField(label="服务状态",required=False,choices=[("","全部状态")]+Resource.STATUS)
    starts_at=LocalDateTime(label="查询开始时间",required=False)
    ends_at=LocalDateTime(label="查询结束时间",required=False)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["campus"].queryset=Campus.objects.filter(is_active=True).order_by("code")
        buildings=Building.objects.filter(is_active=True,campus__is_active=True)
        raw_campus=self.data.get("campus") if self.is_bound else None
        if raw_campus:
            self.fields["building"].queryset=buildings.filter(campus_id=raw_campus).order_by("code") if str(raw_campus).isascii() and str(raw_campus).isdecimal() else buildings.none()
        else:self.fields["building"].queryset=buildings.select_related("campus").order_by("campus__code","code")
    def clean(self):
        d=super().clean();a,b=d.get("starts_at"),d.get("ends_at")
        low,high=d.get("capacity_min"),d.get("capacity_max")
        if low and high and low>high:self.add_error("capacity_max","最大容量不得小于最小容量")
        if bool(a)!=bool(b):raise forms.ValidationError("查询时段须同时填写开始与结束时间")
        if a and b and a>=b:raise forms.ValidationError("查询开始时间必须早于结束时间")
        if d.get("building") and d.get("campus") and d["building"].campus_id!=d["campus"].pk:self.add_error("building","所选楼宇不属于所选校区")
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
    lab=forms.ModelChoiceField(label="所属管理单元",queryset=Lab.objects.all())
    description=forms.CharField(label="资源说明",required=False,widget=forms.Textarea(attrs={"rows":2}))
    category=forms.CharField(label="设备类别（设备必填）",required=False,max_length=60)
    risk=forms.ChoiceField(label="设备分级",choices=EquipmentDetail._meta.get_field("risk").choices)
    qualification=forms.ModelChoiceField(label="要求培训资质",queryset=QualificationType.objects.all(),required=False)
    portable=forms.BooleanField(label="可独立外借（固定设备取消勾选）",required=False,initial=True)
    access_room=forms.ModelChoiceField(label="固定设备关联场地",queryset=Resource.objects.filter(kind='ROOM'),required=False)
    handover_location=forms.CharField(label="设备交接地点",max_length=120,required=False)
    building=forms.ModelChoiceField(label="场地所属楼宇（未核实可留空）",queryset=Building.objects.none(),required=False)
    floor_label=forms.CharField(label="楼层标识",max_length=40,required=False)
    room_number=forms.CharField(label="房间编号",max_length=40,required=False)
    room_location=forms.CharField(label="旧位置说明（兼容字段，不作为结构化位置）",max_length=160,required=False)
    capacity=forms.IntegerField(label="场地容量",required=False,min_value=1)
    space_category=forms.CharField(label="场地类别（演示分类，可按真实目录调整）",required=False,max_length=80)
    facilities=forms.CharField(label="场地设施（逐项填写）",required=False,widget=forms.Textarea(attrs={"rows":3}))
    accessories=forms.CharField(label="配件（每行：编码,名称,数量；保留历史交接快照）",required=False,widget=forms.Textarea(attrs={"rows":3}))
    reason=forms.CharField(label="变更依据",max_length=1000)

    def clean(self):
        data=super().clean()
        if data.get("kind")!="EQUIPMENT":
            return data
        portable=data.get("portable",True)
        room=data.get("access_room")
        lab=data.get("lab")
        if portable and room:
            self.add_error("access_room","可独立外借设备不填写固定关联场地；请清空该项，或将设备改为固定设备后再关联场地。")
        elif not portable:
            if not room:
                self.add_error("access_room","固定设备须关联同一管理单元内的场地。")
            elif lab and room.lab_id!=lab.pk:
                self.add_error("access_room","固定设备关联场地必须属于同一管理单元。")
        return data

class GrantForm(forms.Form):
    user=forms.ModelChoiceField(label="用户",queryset=User.objects.all())
    roles=forms.ModelMultipleChoiceField(label="角色",queryset=Role.objects.all(),required=False,widget=forms.CheckboxSelectMultiple)
    labs=forms.ModelMultipleChoiceField(label="管理员负责的管理单元",queryset=Lab.objects.all(),required=False,widget=forms.CheckboxSelectMultiple)
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
    code=forms.CharField(label="管理单元编号",max_length=32)
    name=forms.CharField(label="管理单元名称",max_length=80)
    location=forms.CharField(label="管理单元地点备注（非单间场地地址）",max_length=120)
    description=forms.CharField(label="说明",required=False,widget=forms.Textarea(attrs={"rows":2}))
    reason=forms.CharField(label="创建依据",max_length=1000)
class CampusForm(forms.Form):
    code=forms.CharField(label="校区编号",max_length=32)
    name=forms.CharField(label="校区名称",max_length=80)
    active=forms.BooleanField(label="启用该校区",required=False,initial=True)
    reason=forms.CharField(label="资料来源或维护依据",max_length=1000)
class BuildingForm(forms.Form):
    campus=forms.ModelChoiceField(label="所属校区",queryset=Campus.objects.none())
    code=forms.CharField(label="楼宇编号",max_length=32)
    name=forms.CharField(label="楼宇名称",max_length=100)
    address_label=forms.CharField(label="地址补充说明",max_length=180,required=False)
    active=forms.BooleanField(label="启用该楼宇",required=False,initial=True)
    reason=forms.CharField(label="资料来源或维护依据",max_length=1000)
class OpeningForm(forms.Form):
    lab=forms.ModelChoiceField(label="管理单元",queryset=Lab.objects.all())
    resource=forms.ModelChoiceField(label="单间场地（留空表示管理单元默认时段）",queryset=Resource.objects.none(),required=False)
    weekday=forms.IntegerField(label="星期（1至7）",min_value=1,max_value=7)
    windows=forms.CharField(label="开放时段（每行一段）",required=False,max_length=2000,
        widget=forms.Textarea(attrs={"rows":4,"placeholder":"08:00-12:00\n13:30-17:00"}),
        help_text="使用24小时制，每行填写“开始-结束”；时段不得重叠且不能跨日。")
    closed_all_day=forms.BooleanField(label="该星期全天关闭",required=False)
    effective=forms.DateField(label="生效日期（至少为明天）",widget=forms.DateInput(attrs={"type":"date"}))
    reason=forms.CharField(label="变更原因",max_length=1000)

    def clean(self):
        data=super().clean()
        resource,lab=data.get("resource"),data.get("lab")
        if resource and lab and resource.lab_id!=lab.pk:
            self.add_error("resource","场地必须属于所选实验室")
        if data.get("closed_all_day"):
            if data.get("windows", "").strip():
                self.add_error("windows","全天关闭时不能填写开放时段")
            data["windows"]=[]
            data["starts_at"]=time.min
            data["ends_at"]=time.min
        else:
            parsed=[]
            raw=data.get("windows", "")
            for line_no,line in enumerate(raw.splitlines(),1):
                line=line.strip()
                if not line:
                    continue
                match=re.fullmatch(r"(\d{2}):(\d{2})\s*[-–—~～]\s*(\d{2}):(\d{2})",line)
                if not match:
                    self.add_error("windows",f"第{line_no}行格式错误，请使用HH:MM-HH:MM，例如08:00-12:00")
                    continue
                try:
                    start=time(int(match.group(1)),int(match.group(2)))
                    end=time(int(match.group(3)),int(match.group(4)))
                except ValueError:
                    self.add_error("windows",f"第{line_no}行时间无效，请填写有效的24小时制时间")
                    continue
                if start>=end:
                    self.add_error("windows",f"第{line_no}行结束时间必须晚于开始时间，且不能跨日")
                    continue
                parsed.append((start,end))
            parsed.sort(key=lambda pair:pair[0])
            for previous,current in zip(parsed,parsed[1:]):
                if current[0]<previous[1]:
                    self.add_error("windows","开放时段不得重叠，请调整对应时间")
                    break
            if not parsed and not self.errors.get("windows"):
                self.add_error("windows","开放日必须至少填写一段时段；全天不开放请勾选全天关闭")
            data["windows"]=parsed
            if parsed:
                data["starts_at"],data["ends_at"]=parsed[0]
        return data

class SafetyProcedureForm(forms.Form):
    title=forms.CharField(label="规程标题",max_length=160)
    risk_categories=forms.CharField(label="风险类别",widget=forms.Textarea(attrs={"rows":2}),help_text="只填写经责任岗位核定的内容；无数据时注明待核实。")
    applicable_users=forms.CharField(label="适用对象",widget=forms.Textarea(attrs={"rows":2}))
    prerequisites=forms.CharField(label="预约或进入前置条件",widget=forms.Textarea(attrs={"rows":2}))
    required_ppe=forms.CharField(label="个人防护用品",widget=forms.Textarea(attrs={"rows":2}))
    pre_use_checks=forms.CharField(label="使用前检查",widget=forms.Textarea(attrs={"rows":2}))
    operating_cautions=forms.CharField(label="操作注意事项",widget=forms.Textarea(attrs={"rows":2}))
    prohibited_actions=forms.CharField(label="禁止事项",widget=forms.Textarea(attrs={"rows":2}))
    incident_route=forms.CharField(label="异常处置路径",widget=forms.Textarea(attrs={"rows":2}))
    source_title=forms.CharField(label="批准来源文件标题",max_length=200,required=False)
    source_reference=forms.CharField(label="来源链接或文件编号",max_length=500,required=False)
    source_version=forms.CharField(label="来源版本",max_length=80,required=False)
    source_effective_date=forms.DateField(label="来源生效日期",required=False,widget=forms.DateInput(attrs={"type":"date"}))
    effective_from=forms.DateField(label="本版本生效日期",required=False,widget=forms.DateInput(attrs={"type":"date"}))
    review_due_date=forms.DateField(label="复核期限",required=False,widget=forms.DateInput(attrs={"type":"date"}))
    requires_acknowledgement=forms.BooleanField(label="预约前要求阅读确认（不等于培训或许可）",required=False)
    change_reason=forms.CharField(label="编制或修订原因",max_length=1000,widget=forms.Textarea(attrs={"rows":2}))

    def clean_source_reference(self):
        from urllib.parse import urlsplit
        value=self.cleaned_data.get("source_reference", "").strip()
        parts=urlsplit(value)
        if parts.scheme and (parts.scheme != "https" or not parts.netloc):
            raise forms.ValidationError("在线来源只接受有效的 HTTPS 地址；本地材料请填写制度编号或文件名称。")
        return value


class CoordinationForm(ActionForm):
    action=forms.ChoiceField(label="协调方式",choices=[('WAIT','前序延误：登记到场并保留原结束时间'),('CANCEL','协调取消未使用预约')])
    arrived_at=LocalDateTime(label="实际到场时间（等待时必填）",required=False)


class RecheckForm(ActionForm):
    accepted=forms.ChoiceField(label="复核结论",choices=[('yes','复核通过'),('no','不能执行，协调取消')])
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)


class CalendarExceptionForm(ActionForm):
    lab=forms.ModelChoiceField(label="管理单元",queryset=Lab.objects.all())
    day=forms.DateField(label="生效日期",widget=forms.DateInput(attrs={'type':'date'}))
    closed=forms.BooleanField(label="全天关闭（取消勾选时填写例外开放时段）",required=False,initial=True)
    starts_at=forms.TimeField(label="例外开放时间",required=False,widget=forms.TimeInput(attrs={'type':'time'}))
    ends_at=forms.TimeField(label="例外关闭时间",required=False,widget=forms.TimeInput(attrs={'type':'time'}))


class IncidentResponseForm(ActionForm):
    kind=forms.ChoiceField(label="记录类型",choices=IncidentResponse._meta.get_field('kind').choices)
