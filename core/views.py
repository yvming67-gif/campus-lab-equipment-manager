import uuid
from datetime import datetime, timedelta, time
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from . import forms, services as s
from .models import *
from .queries import *

def page(request, qs): return Paginator(qs,10).get_page(request.GET.get('page'))
def link(label,url): return {'label':label,'url':url}
def act(label,operation,pk): return link(label,f'/act/{operation}/{pk}/')
def detail(request,title,fields,actions=(),rows=None):
    return render(request,'detail.html',dict(title=title,fields=fields,actions=actions,rows=rows))
def listing(request,title,headers,rows,pagination=None,actions=(),hint=''):
    choices={'设备目录':Equipment.Service.choices,'审核工作台':Reservation.Status.choices,'我的预约':Reservation.Status.choices,
        '异常记录':Incident.Status.choices,'维修工作台':WorkOrder.Status.choices,
        '借出与归还记录':[('open','借出中'),('overdue','逾期未归还'),('closed','已归还')]}
    return render(request,'list.html',dict(title=title,headers=headers,rows=rows,pagination=pagination,actions=actions,hint=hint,filter_states=choices.get(title,[])))
def fmt(value): return timezone.localtime(value).strftime('%Y-%m-%d %H:%M') if value else '—'

@login_required
def dashboard(request):
    u=request.user
    stats=[('设备总数',Equipment.objects.count()),('正常服务',Equipment.objects.filter(service='normal').count()),
           ('我的未读通知',Notification.objects.filter(recipient=u,read_at__isnull=True).count())]
    if u.role=='manager':
        stats += [('待审核',reservations_for(u).filter(status='pending').count()),('待归还',loans_for(u).filter(closed_at__isnull=True).count())]
    return render(request,'dashboard.html',{'stats':stats,'recent':reservations_for(u)[:5]})

@login_required
def labs(request):
    q=Laboratory.objects.all().order_by('code')
    term=request.GET.get('q','').strip()
    if term: q=q.filter(Q(name__icontains=term)|Q(location__icontains=term)|Q(code__icontains=term))
    p=page(request,q)
    rows=[[x.code,x.name,x.location,x.opening,link('查看设备',f'/equipment/?lab={x.pk}'),act('编辑','lab',x.pk) if request.user.role=='system' else ''] for x in p]
    return listing(request,'实验室查询',['编号','实验室','位置','开放信息','设备','管理'],rows,p,[act('新增实验室','lab',0)] if request.user.role=='system' else [])

@login_required
def equipment_list(request):
    q=Equipment.objects.select_related('laboratory').order_by('tag')
    term=request.GET.get('q','').strip()
    if term: q=q.filter(Q(tag__icontains=term)|Q(name__icontains=term)|Q(category__icontains=term))
    lab=request.GET.get('lab',''); state=request.GET.get('status','')
    if lab.isdigit(): q=q.filter(laboratory_id=lab)
    if state in Equipment.Service.values: q=q.filter(service=state)
    p=page(request,q)
    rows=[[link(e.tag,f'/equipment/{e.pk}/'),e.name,e.category,e.laboratory.name,e.get_physical_display(),e.get_service_display(),e.qualification or '普通设备'] for e in p]
    return listing(request,'设备目录',['标签','设备','类型','实验室','实物状态','服务状态','资格'],rows,p,
        [act('新建设备','equipment-new',0)] if request.user.role=='manager' else [],'选择设备可查询指定时段。待审核申请不占用设备。')

@login_required
def equipment_detail(request,pk):
    e=get_object_or_404(Equipment.objects.select_related('laboratory'),pk=pk)
    availability=None
    if request.GET:
        # availability uses dates only; do not require purpose or command key.
        dates=forms.AvailabilityForm(request.GET)
        if dates.is_valid():
            start,end=dates.cleaned_data['start'],dates.cleaned_data['end']
            if start>=end: availability='结束时间必须晚于开始时间。'
            else: availability=s.available(e,start,end)[1]
        else: availability='时间格式无效，请填写完整日期和时间。'
    now=timezone.now()
    slots=e.reservations.filter(status='approved',end__gt=now).order_by('start')[:20]
    actions=[]
    if request.user.role in ['student','teacher']: actions.append(act('提交预约','reserve',pk))
    if request.user.role=='manager' and request.user.managed_labs.filter(pk=e.laboratory_id).exists():
        actions += [act('维护设备','equipment',pk),act('登记异常','incident',pk)]
    elif request.user.role in ['student','teacher'] and Loan.objects.filter(equipment=e,borrower=request.user).exists():
        actions.append(act('提交相关异常说明','incident',pk))
    return render(request,'equipment.html',dict(title=str(e),e=e,slots=slots,actions=actions,availability=availability))

def record_filters(request,q,date_field):
    term=request.GET.get('q','').strip()
    if term: q=q.filter(Q(equipment__tag__icontains=term)|Q(equipment__name__icontains=term))
    lab=request.GET.get('lab','')
    if lab.isdigit(): q=q.filter(equipment__laboratory_id=lab)
    date=parse_date(request.GET.get('date',''))
    if date: q=q.filter(**{f'{date_field}__date':date})
    return q

@login_required
def reservations(request):
    q=record_filters(request,reservations_for(request.user),'start')
    state=request.GET.get('status','')
    if state in Reservation.Status.values: q=q.filter(status=state)
    p=page(request,q)
    rows=[[link(f'#{r.pk}',f'/reservations/{r.pk}/'),r.equipment.tag,r.applicant.username,fmt(r.start),fmt(r.end),r.get_status_display()] for r in p]
    return listing(request,'审核工作台' if request.user.role=='manager' else '我的预约',['预约','设备','申请人','开始','结束','状态'],rows,p,hint='按设备、实验室、开始日期和状态查询；所有预约均须人工审核。')

@login_required
def reservation_detail(request,pk):
    r=get_object_or_404(reservations_for(request.user),pk=pk)
    actions=[]
    if request.user.pk==r.applicant_id and r.status in ['pending','approved'] and r.start>timezone.now(): actions.append(act('取消预约','cancel',pk))
    if request.user.role=='manager':
        if r.status=='pending': actions.append(act('审核预约','review',pk))
        if r.status=='approved': actions.append(act('办理借出','issue',pk))
    if hasattr(r,'loan'): actions.append(link('查看借用记录',f'/loans/{r.loan.pk}/'))
    fields=[('申请人',r.applicant.username),('设备',str(r.equipment)),('开始',fmt(r.start)),('结束',fmt(r.end)),('状态',r.get_status_display()),('用途',r.purpose),('状态原因',r.reason or '等待管理员处理')]
    if hasattr(r,'review'): fields += [('审核人',r.review.reviewer.username),('审核结论',r.review.reason)]
    return detail(request,f'预约 #{pk}',fields,actions)

@login_required
def loans(request):
    q=record_filters(request,loans_for(request.user),'issued_at').order_by('-id')
    state=request.GET.get('status','')
    if state=='open': q=q.filter(closed_at__isnull=True)
    if state=='closed': q=q.filter(closed_at__isnull=False)
    if state=='overdue': q=q.filter(closed_at__isnull=True,due_at__lt=timezone.now())
    p=page(request,q)
    rows=[[link(f'#{x.pk}',f'/loans/{x.pk}/'),x.equipment.tag,x.borrower.username,fmt(x.issued_at),fmt(x.due_at),fmt(x.closed_at),'已归还' if x.closed_at else ('逾期未归还' if x.due_at<timezone.now() else '借出中')] for x in p]
    return listing(request,'借出与归还记录',['编号','设备','借用人','借出','应还','实还','状态'],rows,p)

@login_required
def loan_detail(request,pk):
    x=get_object_or_404(loans_for(request.user),pk=pk)
    actions=[act('办理归还','receive',pk)] if request.user.role=='manager' and not x.closed_at else []
    fields=[('设备',str(x.equipment)),('借用人',x.borrower.username),('借出时间',fmt(x.issued_at)),('应还时间',fmt(x.due_at)),('实际归还',fmt(x.closed_at)),('借出检查',x.inspection),('配件快照',str(x.accessories))]
    if hasattr(x,'return_record'):
        y=x.return_record
        fields += [('接收人',y.receiver.username),('归还检查',y.get_condition_display()),('检查记录',y.notes),('逾期秒数',y.overdue_seconds)]
    return detail(request,f'借用 #{pk}',fields,actions)

@login_required
def incidents(request):
    q=record_filters(request,incidents_for(request.user),'created_at').order_by('-id')
    state=request.GET.get('status','')
    if state in Incident.Status.values: q=q.filter(status=state)
    p=page(request,q)
    rows=[[link(f'#{i.pk}',f'/incidents/{i.pk}/'),i.equipment.tag,i.reporter.username,i.description,i.get_status_display(),fmt(i.created_at)] for i in p]
    return listing(request,'异常记录',['编号','设备','报告人','异常说明','状态','报告时间'],rows,p)

@login_required
def incident_detail(request,pk):
    i=get_object_or_404(incidents_for(request.user),pk=pk)
    actions=[]
    if request.user.role=='manager' and i.status!='resolved':
        actions += [act('核验异常','confirm',pk)]
        if i.status=='confirmed': actions.append(act('创建维修工单','work-new',pk))
    return detail(request,f'异常 #{pk}',[('设备',str(i.equipment)),('情况',i.description),('状态',i.get_status_display()),('处理说明',i.resolution)],actions,
        [[link(f'工单 #{w.pk}',f'/work/{w.pk}/'),w.get_status_display()] for w in i.work_orders.all() if request.user.role=='manager' or w.assignee_id==request.user.pk])

@login_required
def work(request):
    q=record_filters(request,work_for(request.user),'created_at').order_by('-id')
    state=request.GET.get('status','')
    if state in WorkOrder.Status.values: q=q.filter(status=state)
    p=page(request,q)
    return listing(request,'维修工作台',['工单','设备','维修人员','状态','创建时间'],[[link(f'#{w.pk}',f'/work/{w.pk}/'),w.equipment.tag,w.assignee.username,w.get_status_display(),fmt(w.created_at)] for w in p],p)

@login_required
def work_detail(request,pk):
    w=get_object_or_404(work_for(request.user),pk=pk)
    actions=[]
    if request.user.role=='technician' and w.status in ['open','progress']: actions.append(act('更新维修 / 提交验收','progress',pk))
    if request.user.role=='manager' and w.status=='acceptance': actions.append(act('管理员验收','accept',pk))
    return detail(request,f'工单 #{pk}',[('设备',str(w.equipment)),('关联异常',w.incident.description),('维修人员',w.assignee.username),('状态',w.get_status_display()),('进度',w.progress),('结果',w.result),('验收人',w.accepted_by.username if w.accepted_by else '—')],actions)

@login_required
def notifications(request):
    q=Notification.objects.filter(recipient=request.user).order_by('-id')
    p=page(request,q)
    return listing(request,'通知中心',['时间','消息','模拟外部发送','发送次数（首次＋重试）','最近错误'],[[fmt(n.created_at),n.message,n.get_status_display(),n.attempts,n.last_error] for n in p],p,hint='站内通知随业务事务立即生成；外部消息完全模拟，失败不会撤销业务。')

@login_required
def audits(request):
    s.require_role(request.user,'manager','system')
    q=Audit.objects.select_related('actor').all()
    if request.user.role=='manager':
        es=Equipment.objects.filter(laboratory__in=request.user.managed_labs.all())
        allowed=Q(object_type='Equipment',object_id__in=es.values('id'))
        for model in [Reservation,Loan,Incident,WorkOrder]: allowed |= Q(object_type=model.__name__,object_id__in=model.objects.filter(equipment__in=es).values('id'))
        allowed |= Q(object_type='ReturnRecord',object_id__in=ReturnRecord.objects.filter(loan__equipment__in=es).values('id'))
        q=q.filter(allowed)
    term=request.GET.get('q','')
    if term: q=q.filter(Q(object_type__icontains=term)|Q(reason__icontains=term)|Q(trace__icontains=term))
    date=parse_date(request.GET.get('date',''))
    if date: q=q.filter(at__date=date)
    p=page(request,q)
    return listing(request,'操作审计',['时间','对象','编号','原状态','新状态','操作者','原因','追踪'],[[fmt(a.at),a.object_type,a.object_id,a.old_state,a.new_state,a.actor.username if a.actor else '后台任务',a.reason,a.trace] for a in p],p,hint='关键历史只读，页面不提供修改或删除功能。')

@login_required
def users(request):
    s.require_role(request.user,'system')
    q=User.objects.order_by('username')
    if request.GET.get('q'): q=q.filter(username__icontains=request.GET['q'])
    p=page(request,q)
    return listing(request,'用户与角色',['账号','角色','启用','授权实验室','操作'],[[u.username,u.get_role_display(),'是' if u.is_active else '否','、'.join(u.managed_labs.values_list('name',flat=True)),act('编辑','user',u.pk)] for u in p],p,[act('新增用户','user',0)])

@login_required
def foundation(request):
    s.require_role(request.user,'system')
    c=BusinessConfig.objects.get(pk=1)
    p=page(request,Qualification.objects.select_related('user').order_by('user__username','code'))
    return listing(request,'基础数据与培训资格',['用户','资格代码','有效至'],[[q.user.username,q.code,fmt(q.expires_at)] for q in p],p,
        [act('业务参数','config',1),act('添加 / 更新培训资格','qualification',0),link('实验室维护','/labs/')],
        f'课程假设：提前 {c.advance_days} 天 / {c.slot_minutes} 分钟粒度 / 最长 {c.max_hours} 小时 / 最多 {c.max_future} 条 / 领取宽限 {c.grace_minutes} 分钟。')

@login_required
def report(request):
    s.require_role(request.user,'manager')
    now=timezone.now()
    from_date=parse_date(request.GET.get('from','')) or (timezone.localdate()-timedelta(days=7))
    to_date=parse_date(request.GET.get('to','')) or timezone.localdate()
    if to_date<from_date or (to_date-from_date).days>366:
        return render(request,'error.html',{'title':'统计日期无效','message':'结束日须不早于开始日，周期最多367天。'},status=400)
    start=timezone.make_aware(datetime.combine(from_date,time.min))
    end=timezone.make_aware(datetime.combine(to_date+timedelta(days=1),time.min))
    q=Equipment.objects.filter(laboratory__in=request.user.managed_labs.all()).select_related('laboratory').order_by('tag')
    lab=request.GET.get('lab','')
    if lab.isdigit(): q=q.filter(laboratory_id=lab)
    p=page(request,q)
    rows=[]
    for e in p:
        used,denominator,ratio=utilisation(e,start,end,now)
        rows.append([e.tag,e.laboratory.name,used,denominator,f'{ratio}%'])
    return listing(request,'设备利用率报表',['设备','实验室','实际使用小时','可服务小时','利用率'],rows,p,
        hint=f'{from_date} 至 {to_date}（含结束日，截至当前时刻）。分子：实际借出时长扣除服务中断；分母：全天日历时长扣除异常/维修/停用/报废时长。仅模拟数据，不代表真实实施效果。')

ACTION_FORMS={'reserve':forms.ReserveForm,'review':forms.ReviewForm,'cancel':forms.ReasonForm,'issue':forms.IssueForm,
    'receive':forms.ReceiveForm,'incident':forms.IncidentForm,'confirm':forms.ConfirmForm,'work-new':forms.WorkCreateForm,
    'progress':forms.ProgressForm,'accept':forms.AcceptForm,'equipment':forms.EquipmentForm,'equipment-new':forms.EquipmentCreateForm,
    'user':forms.UserForm,'lab':forms.LabForm,'qualification':forms.QualificationForm,'config':forms.ConfigForm}
TITLES={'reserve':'提交预约','review':'审核预约','cancel':'取消预约','issue':'办理设备借出','receive':'办理设备归还',
    'incident':'登记异常 / 提交说明','confirm':'核验异常','work-new':'创建维修工单','progress':'处理维修工单','accept':'管理员维修验收',
    'equipment':'维护设备','equipment-new':'新建设备','user':'管理用户与角色','lab':'维护实验室','qualification':'维护培训资格','config':'业务参数'}

@login_required
def action(request,operation,pk):
    if operation not in ACTION_FORMS: return error404(request,None)
    u=request.user; initial={'key':uuid.uuid4()}; subject=None; hint=''
    if operation in ['review','cancel','issue']:
        subject=get_object_or_404(reservations_for(u),pk=pk)
        if operation in ['review','issue']: s.require_manager(u,subject.equipment)
        elif subject.applicant_id!=u.pk: raise PermissionDenied('只能取消本人预约。')
        if operation=='issue': hint='配件：'+str(list(subject.equipment.accessories.values('name','quantity')))+'。核验实际到场借用人、标签及正常外观功能。'
    elif operation=='receive':
        subject=get_object_or_404(loans_for(u),pk=pk); s.require_manager(u,subject.equipment)
    elif operation in ['reserve','incident','equipment']:
        subject=get_object_or_404(Equipment,pk=pk)
        if operation=='equipment':
            s.require_manager(u,subject)
            initial.update({f:getattr(subject,f) for f in ['name','category','description','qualification','high_value','dangerous','service']})
        elif operation=='reserve': s.require_role(u,'student','teacher')
        elif u.role=='manager': s.require_manager(u,subject)
        elif u.role not in ['student','teacher'] or not Loan.objects.filter(equipment=subject,borrower=u).exists(): raise PermissionDenied('只能报告本人相关设备异常。')
    elif operation in ['confirm','work-new']:
        subject=get_object_or_404(incidents_for(u),pk=pk); s.require_manager(u,subject.equipment)
    elif operation in ['progress','accept']:
        subject=get_object_or_404(work_for(u),pk=pk)
        if operation=='accept': s.require_manager(u,subject.equipment)
        else: s.require_role(u,'technician')
    elif operation=='equipment-new': s.require_role(u,'manager')
    else:
        s.require_role(u,'system')
        if operation=='user' and pk:
            subject=get_object_or_404(User,pk=pk)
            initial.update(username=subject.username,role=subject.role,active=subject.is_active,labs=subject.managed_labs.all())
        if operation=='lab' and pk:
            subject=get_object_or_404(Laboratory,pk=pk)
            initial.update({f:getattr(subject,f) for f in ['code','name','location','opening']})
        if operation=='config':
            subject=BusinessConfig.objects.get(pk=1)
            initial.update({f:getattr(subject,f) for f in ['advance_days','slot_minutes','max_hours','max_future','grace_minutes']})
    form=ACTION_FORMS[operation](request.POST or None,initial=initial)
    if operation=='equipment-new': form.fields['laboratory'].queryset=u.managed_labs.all()
    status=200
    if request.method=='POST':
        if form.is_valid():
            d=form.cleaned_data.copy(); key=str(d.pop('key'))
            try:
                if operation=='reserve': obj=s.reserve(u,pk,key=key,**d); url=f'/reservations/{obj.pk}/'
                elif operation=='review': obj=s.review(u,pk,d['approve']=='yes',d['reason'],key=key); url=f'/reservations/{pk}/'
                elif operation=='cancel': obj=s.cancel(u,pk,d['reason'],key=key); url=f'/reservations/{pk}/'
                elif operation=='issue':
                    d.pop('normal_checked'); obj=s.issue(u,pk,key=key,**d); url=f'/loans/{obj.pk}/'
                elif operation=='receive': obj=s.receive(u,pk,key=key,**d); url=f'/loans/{pk}/'
                elif operation=='incident': obj=s.report_incident(u,pk,key=key,**d); url=f'/incidents/{obj.pk}/'
                elif operation=='confirm': obj=s.confirm_incident(u,pk,key=key,**d); url=f'/incidents/{pk}/'
                elif operation=='work-new': obj=s.create_work_order(u,pk,d['assignee'].pk,d['reason'],key=key); url=f'/work/{obj.pk}/'
                elif operation=='progress': obj=s.progress_work(u,pk,d['submit']=='yes',d['text'],key=key); url=f'/work/{pk}/'
                elif operation=='accept': obj=s.accept_work(u,pk,d['approved']=='yes',d['reason'],key=key); url=f'/work/{pk}/'
                elif operation=='equipment': obj=s.maintain_equipment(u,pk,key=key,**d); url=f'/equipment/{pk}/'
                elif operation=='equipment-new':
                    d['laboratory_id']=d.pop('laboratory').pk
                    obj=s.add_equipment(u,key=key,**d); url=f'/equipment/{obj.pk}/'
                elif operation=='user':
                    d['labs']=list(d['labs'].values_list('pk',flat=True)); obj=s.save_user(u,pk,key=key,**d); url='/users/'
                elif operation=='lab': obj=s.save_lab(u,pk,key=key,**d); url='/labs/'
                elif operation=='qualification':
                    d['user_id']=d.pop('user').pk; obj=s.save_qualification(u,key=key,**d); url='/foundation/'
                elif operation=='config': obj=s.save_config(u,key=key,**d); url='/foundation/'
                messages.success(request,'操作已成功保存。')
                return redirect(url)
            except s.BusinessError as exc:
                form.add_error(exc.field if exc.field in form.fields else None,str(exc)); status=400
            except ValidationError as exc:
                form.add_error(None,exc); status=400
            except OperationalError:
                form.add_error(None,'数据库暂时不可用，事务未确认。请保留此表单稍后重试，同一请求标识可防止重复。'); status=503
        else: status=400
    return render(request,'form.html',{'title':TITLES[operation],'form':form,'subject':subject,'hint':hint},status=status)

def error403(request,exception): return render(request,'error.html',{'title':'无权限','message':'当前账号没有访问此资源或操作的权限。'},status=403)
def error404(request,exception): return render(request,'error.html',{'title':'记录不存在','message':'记录不存在或不在您的可访问范围内。'},status=404)
def error500(request): return render(request,'error.html',{'title':'服务暂时异常','message':'未能完成请求，请稍后重试。重复提交请使用原表单。'},status=500)
