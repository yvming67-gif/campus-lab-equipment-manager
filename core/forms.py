from django import forms
from .models import *

class ActionForm(forms.Form):
    key=forms.UUIDField(widget=forms.HiddenInput)

class DateInput(forms.DateTimeInput):
    input_type='datetime-local'
    def __init__(self): super().__init__(format='%Y-%m-%dT%H:%M',attrs={'step':1800})

def dt(label): return forms.DateTimeField(label=label,widget=DateInput(),input_formats=['%Y-%m-%dT%H:%M'])

class AvailabilityForm(forms.Form):
    start=dt('开始时间')
    end=dt('结束时间')

class ReserveForm(ActionForm):
    start=dt('开始时间（上海时间）')
    end=dt('结束时间（上海时间）')
    purpose=forms.CharField(label='预约用途',max_length=300,widget=forms.Textarea(attrs={'rows':3}))

class ReasonForm(ActionForm):
    reason=forms.CharField(label='原因 / 处理说明',widget=forms.Textarea(attrs={'rows':3}),max_length=2000)

class ReviewForm(ReasonForm):
    approve=forms.ChoiceField(label='审核结论',choices=[('yes','通过'),('no','驳回')])

class IssueForm(ActionForm):
    borrower_username=forms.CharField(label='核验实际借用人账号',max_length=150)
    equipment_tag=forms.CharField(label='核验实物唯一标签',max_length=40)
    accessories_checked=forms.BooleanField(label='已按页面配件清单逐项核对')
    normal_checked=forms.BooleanField(label='外观及功能正常（异常请先登记）')
    inspection=forms.CharField(label='外观、功能及配件检查记录',widget=forms.Textarea(attrs={'rows':3}),max_length=2000)

class ReceiveForm(ActionForm):
    condition=forms.ChoiceField(label='归还检查结论',choices=ReturnRecord.Condition.choices)
    notes=forms.CharField(label='外观、功能及配件接收记录',widget=forms.Textarea(attrs={'rows':3}),max_length=2000)

class IncidentForm(ActionForm):
    description=forms.CharField(label='异常情况说明',widget=forms.Textarea(attrs={'rows':4}),max_length=2000)

class ConfirmForm(ReasonForm):
    decision=forms.ChoiceField(label='核验结论',choices=[('confirm','确认异常，继续隔离'),('resolve','确认已解决，尝试恢复')])

class WorkCreateForm(ReasonForm):
    assignee=forms.ModelChoiceField(label='指派维修人员',queryset=User.objects.filter(role='technician',is_active=True))

class ProgressForm(ActionForm):
    submit=forms.ChoiceField(label='提交方式',choices=[('no','保存进度，进入处理中'),('yes','提交结果，等待管理员验收')])
    text=forms.CharField(label='维修进度 / 维修结果',widget=forms.Textarea(attrs={'rows':4}),max_length=2000)

class AcceptForm(ReasonForm):
    approved=forms.ChoiceField(label='验收结论',choices=[('yes','验收通过'),('no','退回继续维修')])

class EquipmentForm(ReasonForm):
    name=forms.CharField(label='设备名称',max_length=100)
    category=forms.CharField(label='设备类型',max_length=60)
    description=forms.CharField(label='设备说明',required=False,widget=forms.Textarea(attrs={'rows':3}))
    qualification=forms.CharField(label='培训资格代码',required=False,max_length=60)
    high_value=forms.BooleanField(label='高价值设备',required=False)
    dangerous=forms.BooleanField(label='危险设备',required=False)
    service=forms.ChoiceField(label='服务状态（恢复仍检查全部阻塞条件）',choices=Equipment.Service.choices)

class EquipmentCreateForm(ActionForm):
    laboratory=forms.ModelChoiceField(label='授权实验室',queryset=Laboratory.objects.none())
    tag=forms.CharField(label='唯一设备标签',max_length=40)
    name=forms.CharField(label='设备名称',max_length=100)
    category=forms.CharField(label='设备类型',max_length=60)
    qualification=forms.CharField(label='培训资格代码',required=False,max_length=60)
    high_value=forms.BooleanField(label='高价值设备',required=False)
    dangerous=forms.BooleanField(label='危险设备',required=False)
    accessories=forms.CharField(label='配件清单（英文逗号分隔，各1件）',max_length=500)

class UserForm(ActionForm):
    username=forms.RegexField(label='账号',regex=r'^[\w.@+-]+$',max_length=150)
    role=forms.ChoiceField(label='角色',choices=User.Role.choices)
    active=forms.BooleanField(label='账号启用',required=False,initial=True)
    labs=forms.ModelMultipleChoiceField(label='管理实验室（仅实验室管理员生效）',queryset=Laboratory.objects.all(),required=False)
    password=forms.CharField(label='新密码（编辑时留空保留原密码）',required=False,widget=forms.PasswordInput, max_length=128)

class LabForm(ActionForm):
    code=forms.CharField(label='实验室编号',max_length=30)
    name=forms.CharField(label='实验室名称',max_length=100)
    location=forms.CharField(label='位置',max_length=150)
    opening=forms.CharField(label='开放信息',max_length=200)

class QualificationForm(ActionForm):
    user=forms.ModelChoiceField(label='用户',queryset=User.objects.filter(role__in=['student','teacher'],is_active=True))
    code=forms.CharField(label='资格代码',max_length=60)
    expires_at=dt('有效至（上海时间）')

class ConfigForm(ActionForm):
    advance_days=forms.IntegerField(label='最多提前天数',min_value=1,max_value=365)
    slot_minutes=forms.TypedChoiceField(label='时间粒度（分钟）',choices=[(15,'15'),(30,'30'),(60,'60')],coerce=int)
    max_hours=forms.IntegerField(label='单次最大小时数',min_value=1,max_value=24)
    max_future=forms.IntegerField(label='未来预约数量上限',min_value=1,max_value=30)
    grace_minutes=forms.IntegerField(label='领取宽限（分钟）',min_value=1,max_value=120)
