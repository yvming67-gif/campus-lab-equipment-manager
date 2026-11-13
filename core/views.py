import json,secrets,uuid,hashlib
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login,logout
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied,ImproperlyConfigured
from django.db import IntegrityError,transaction
from django.db.models import Q,Count
from django.http import FileResponse,Http404,JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .models import *
from .forms import *
from . import services as s
from .reports import DEFAULT_REPORT_DAYS, report_window, utilization
from .demo_identity import (
    PRIVATE_SESSION, authenticate_private_demo, public_demo_user_allowed,
    valid_private_demo_token, verify_demo_password, web_demo_accounts,
)
from .schedules import resource_schedule
from .workbench import tasks_for, booking_queue, acceptance_queue
from .catalog import calendar_catalog, filter_resources
from .experience import slot_options, approval_context, next_step
from . import ui_navigation as ui
from . import safety as safety_service
from .opening_calendar import weekly_schedule

def paginate(request,queryset,size=30,*,page_param='page'):
    # A stable primary-key order prevents rows moving between pages when a
    # query has no explicit business ordering.
    if not queryset.ordered:
        queryset=queryset.order_by("pk")
    page=Paginator(queryset,size).get_page(request.GET.get(page_param,1))
    query=request.GET.copy();query.pop(page_param,None)
    return page,query.urlencode()


def protected(ok):
    if not ok:raise PermissionDenied

def scoped_bookings(user):
    qs=Reservation.objects.select_related("resource__lab","applicant").prefetch_related("review_notes__actor")
    if user.has_role("LAB_ADMIN"):return qs.filter(Q(applicant=user)|Q(resource__lab__labgrant__user=user)).distinct()
    return qs.filter(applicant=user)

def scoped_incidents(user):
    q=Q(reporter=user)|Q(reservation__applicant=user)
    if user.has_role("LAB_ADMIN"):q|=Q(resource__lab__labgrant__user=user)
    if user.has_role("TECHNICIAN"):q|=Q(work_orders__assigned_to=user)
    return Incident.objects.filter(q).select_related("resource__lab","reporter").distinct()

def scoped_work(user):
    qs=WorkOrder.objects.select_related("incident__resource__lab","assigned_to","incident__reporter")
    q=Q(pk__isnull=True)
    if user.has_role("LAB_ADMIN"):q|=Q(incident__resource__lab__labgrant__user=user)
    if user.has_role("TECHNICIAN"):q|=Q(assigned_to=user)
    return qs.filter(q).distinct()

def login_page(request):
    if request.user.is_authenticated:return redirect("dashboard")
    if request.method=="POST":
        request.session["idp_nonce"]=secrets.token_urlsafe(24)
        return redirect("identity")
    return render(request,"login.html",{"title":"统一身份入口","demo":settings.DEMO_MODE})

@never_cache
def identity(request):
    if not settings.DEMO_MODE:
        if not request.session.get("idp_nonce"):return redirect("login")
        try:
            from .adapters import identity_adapter
            url=identity_adapter().begin(request,request.session["idp_nonce"],request.build_absolute_uri(reverse("callback")))
            from urllib.parse import urlsplit
            target=urlsplit(url)
            if target.scheme!='https' or not target.hostname or target.username or target.password:raise ValueError('不安全的身份跳转地址')
            return redirect(url)
        except (ImproperlyConfigured,ValueError):
            return render(request,"login.html",{"title":"统一身份入口","demo":False,"error":"学校身份服务尚未完成配置或验证，请联系运维人员"},status=503)
    if not request.session.get("idp_nonce"):return redirect("login")
    accounts = web_demo_accounts()
    error = ""
    username = request.POST.get("username", "")
    if request.method == "POST":
        password = request.POST.get("password", "")
        user, private_token = authenticate_private_demo(username, password)
        if not user:
            user = verify_demo_password(username, password)
        if user:
            assertion = {"sub": user.school_no, "nonce": request.session["idp_nonce"]}
            request.session["pending_demo_private"] = private_token
            ticket = signing.dumps(assertion, key=settings.MOCK_IDP_KEY, salt="mock-idp")
            return redirect(reverse("callback") + "?" + urlencode({"ticket": ticket}))
        error = "账号或密码不正确，或账号已停用。"
    return render(request, "identity.html", {
        "title": "演示账号登录", "accounts": accounts, "error": error,
        "username": username, "public_demo": settings.PUBLIC_DEMO_MODE,
    })


@never_cache
def callback(request):
    if not settings.DEMO_MODE:
        nonce=request.session.pop("idp_nonce",None)
        protected(bool(nonce))
        try:
            from .adapters import identity_adapter
            # Provider must verify signature, issuer, audience, expiry and one-use code/state.
            assertion=identity_adapter().complete(request,nonce,request.build_absolute_uri(reverse("callback")))
            protected(assertion.get("state")==nonce and isinstance(assertion.get("school_no"),str))
            user=get_object_or_404(User,school_no=assertion["school_no"],is_active=True)
        except (ImproperlyConfigured,ValueError,KeyError):raise PermissionDenied
        login(request,user,backend="django.contrib.auth.backends.ModelBackend")
        s.audit(user,user,"LOGIN",new={"identity":"SCHOOL"},reason="学校身份适配器校验通过；使用既有本地授权")
        return redirect("dashboard")
    try:
        assertion=signing.loads(request.GET.get("ticket",""),key=settings.MOCK_IDP_KEY,salt="mock-idp",max_age=60)
        protected(assertion["nonce"]==request.session.pop("idp_nonce",None))
        user=get_object_or_404(User,school_no=assertion["sub"],is_active=True)
    except (signing.BadSignature,KeyError):raise PermissionDenied
    private_token = request.session.pop("pending_demo_private", "")
    if not public_demo_user_allowed(user):
        protected(valid_private_demo_token(user, private_token))
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    if private_token:
        request.session[PRIVATE_SESSION] = private_token
    s.audit(user, user, "LOGIN", new={"identity": "MOCK"}, reason="课程模拟身份断言校验通过")
    return redirect("dashboard")

@require_POST
def signout(request):
    logout(request);return redirect("login")

@login_required
def dashboard(request):
    now=timezone.now();mine=Reservation.objects.filter(applicant=request.user)
    context={"title":"工作台","pending":mine.filter(status="PENDING").count(),"confirmed":mine.filter(status="CONFIRMED").count(),"using":mine.filter(status="IN_USE").count(),"recent":scoped_bookings(request.user)[:6],"today":now,"overdue":s.overdue_for(request.user,now),
             "is_student":request.user.has_role("STUDENT"),"is_teacher":request.user.has_role("TEACHER"),"is_lab_admin":request.user.has_role("LAB_ADMIN"),"is_technician":request.user.has_role("TECHNICIAN"),"is_director":request.user.has_role("DIRECTOR"),"is_sys_admin":request.user.has_role("SYS_ADMIN")}
    if request.user.has_role("LAB_ADMIN"):context["to_review"]=scoped_bookings(request.user).filter(status="PENDING").exclude(applicant=request.user).count()
    if request.user.has_role("DIRECTOR"):
        context.update({"director_pending":Reservation.objects.filter(status="PENDING").count(),"director_incidents":Incident.objects.filter(status="PENDING").count(),"director_acceptance":WorkOrder.objects.filter(status="AWAITING_ACCEPTANCE").count()})
        context["default_report_days"] = DEFAULT_REPORT_DAYS
    if request.user.has_role("TECHNICIAN"):
        assigned = scoped_work(request.user).filter(assigned_to=request.user, status__in=["OPEN", "IN_PROGRESS"]).order_by("pk")
        context["technician_open"] = assigned.count()
        context["technician_jobs"] = assigned[:5]
    if request.user.has_role("TEACHER"):
        context["teacher_room_upcoming"]=mine.filter(resource__kind="ROOM",status__in=["CONFIRMED","IN_USE"],ends_at__gt=now).count()
        context["teacher_rooms"] = mine.filter(resource__kind="ROOM", ends_at__gt=now,
            status__in=["PENDING", "CONFIRMED", "IN_USE"]).select_related("resource__lab").order_by("starts_at")[:5]
    scoped=scoped_bookings(request.user)
    context['tasks']=tasks_for(request.user,scoped,scoped_incidents(request.user),scoped_work(request.user),now)
    return render(request,"dashboard.html",context)

@login_required
def resources(request):
    # The directory renders resource metadata, not accessory rows. Keep accessory
    # loading on the detail page so ordinary catalog visits do not fetch unused data.
    form=QueryForm(request.GET or None);qs=Resource.objects.select_related("lab","equipment__qualification","equipment__access_room__room__building__campus","room__building__campus")
    times=None;d={};catalog_total=None
    if form.is_bound and form.is_valid():
        d=form.cleaned_data
        qs=filter_resources(qs,d)
        if d["starts_at"]:times=(d["starts_at"],d["ends_at"])
    elif form.is_bound:
        qs=qs.none()
    room_directory = d.get("kind") == "ROOM"
    room_filters_open = room_directory and any(value for key, value in d.items() if key != "kind")
    if room_directory:
        qs = qs.order_by("lab__code", "code", "pk")
    page,filters=paginate(request,qs)
    if not (form.is_bound and not form.is_valid()):
        catalog_total=Resource.objects.count() if any(d.get(key) for key in ('q','kind','lab','campus','building','starts_at')) else page.paginator.count
    items=list(page.object_list)
    for r in items:
        r.service_label=s.resource_service_label(r)
        r.reservation_open,r.reservation_block_reason=s.resource_reservation_state(r)
        r.available_label="尚未选择目标时段"
        r.available=None
        if times:r.available,r.available_label=s.availability(r,*times,applicant=request.user)
        query=request.GET.copy();query.pop('page',None)
        r.detail_url=reverse('resource',args=[r.pk])+('?' + query.urlencode() if query else '')
    chips=ui.filter_chips('resources',request.GET,ui.CATALOG_KEYS,[('q','资源搜索',d.get('q')),('kind','类型',dict(Resource.KIND).get(d.get('kind'))),('lab','管理单元',d.get('lab')),('campus','校区',d.get('campus')),('building','楼宇',d.get('building')),('category','类别',d.get('category')),('facility','设施或配件',d.get('facility')),('capacity_min','最小容量',d.get('capacity_min')),('capacity_max','最大容量',d.get('capacity_max')),('service_status','服务状态',dict(Resource.STATUS).get(d.get('service_status')))])
    if times:chips.append({'label':'目标时段','value':f'{timezone.localtime(times[0]):%m-%d %H:%M}—{timezone.localtime(times[1]):%m-%d %H:%M}', 'url':ui.link('resources',request.GET,ui.CATALOG_KEYS,drop=('starts_at','ends_at'))})
    return render(request,"resources.html",{"title":"教室目录" if room_directory else "资源目录","form":form,"resources":items,"page":page,"filters":filters,"times":times,"policy":Policy.objects.get(pk=1),"room_directory":room_directory,"room_filters_open":room_filters_open,"directory_kind":d.get("kind"),
        'schedule_url':ui.catalog_schedule_link(request.GET,d),'query_scope':'当前可见资源目录','query_count':page.paginator.count,'query_total':catalog_total,'query_chips':chips,'query_invalid':form.is_bound and not form.is_valid()})

@login_required
def resource_detail(request,pk):
    r=get_object_or_404(Resource.objects.select_related("lab","equipment__qualification","equipment__access_room__room__building__campus","room__building__campus").prefetch_related('accessories'),pk=pk)
    start=timezone.localtime()+timedelta(days=1);start=start.replace(hour=10,minute=0,second=0,microsecond=0)
    end=start+timedelta(hours=1);selection_error=''
    if request.method=='GET' and (request.GET.get('starts_at') or request.GET.get('ends_at')):
        selection=QueryForm(request.GET)
        if selection.is_valid() and selection.cleaned_data.get('starts_at'):
            start,end=selection.cleaned_data['starts_at'],selection.cleaned_data['ends_at']
        else:selection_error='所选时间无效，请重新选择完整起止时段。'
    form=BookingForm(request.POST or None,initial={"starts_at":start,"ends_at":end,"request_key":uuid.uuid4(),"expected_approval_mode":"MANUAL" if r.requires_approval else "AUTO"})
    if r.kind=="EQUIPMENT":form.fields["participants"].widget=forms.HiddenInput()
    if request.method=="POST" and form.is_valid():
        try:
            values=dict(form.cleaned_data)
            values["expected_approval_mode"]=values.get("expected_approval_mode") or None
            b=s.create_reservation(request.user,r.pk,**values)
            messages.success(request,"预约已保存："+b.get_status_display());return redirect("booking",pk=b.pk)
        except s.BusinessError as e:form.add_error(None,str(e))
        except IntegrityError:form.add_error(None,"时段或请求已被其他操作占用，请刷新后重试")
    schedule_day=request.GET.get("schedule_day","");schedule_error=""
    try:schedule=resource_schedule(r,schedule_day)
    except ValueError as error:schedule=r.reservations.none();schedule_error=str(error)
    schedule_page,schedule_filters=paginate(request,schedule,15,page_param="schedule_page")
    policy=Policy.objects.get(pk=1);slot_day=request.GET.get('slot_day',timezone.localtime(start).date().isoformat())
    duration=request.GET.get('duration_minutes','60');options=None;slot_error=''
    can_apply=request.user.has_role('TEACHER') if r.kind=='ROOM' else request.user.has_role('STUDENT','TEACHER')
    reservation_open,reservation_block_reason=s.resource_reservation_state(r)
    service_label=s.resource_service_label(r)
    if can_apply and reservation_open:
        try:
            options=slot_options(r,request.user,slot_day,duration)
            for item in options['items']:
                query=request.GET.copy();query.pop('schedule_page',None)
                for key,value in {'slot_day':slot_day,'duration_minutes':duration,'starts_at':item['starts_at'],'ends_at':item['ends_at']}.items():query[key]=value
                item['url']=reverse('resource',args=[pk])+'?'+query.urlencode()+'#reservation-form'
        except (ValueError,s.BusinessError) as error:slot_error=str(error)
    back=request.GET.copy()
    for key in list(back):
        if key not in ui.CATALOG_KEYS:back.pop(key,None)
    published_safety=safety_service.latest_published_procedures(r)
    current_safety_ids={item.pk for item in safety_service.current_procedures(r)}
    safety_entries=[{"procedure":item,"is_current":item.pk in current_safety_ids,
                     "acknowledged":safety_service.acknowledged(request.user,item),
                     "scope_label":item.scope_label} for item in published_safety]
    manageable=s.manager(request.user,r)
    context={"title":r.name,"resource":r,"form":form,"can_apply":can_apply,"reservation_open":reservation_open,"reservation_block_reason":reservation_block_reason,"service_label":service_label,"manageable":manageable,"safety_entries":safety_entries,"opening_schedule":weekly_schedule(r) if r.kind=="ROOM" else [],"reservations":schedule_page.object_list,"schedule_page":schedule_page,"schedule_filters":schedule_filters,"schedule_day":schedule_day,"schedule_error":schedule_error,
             'slots':options,'slot_day':slot_day,'slot_error':slot_error,'duration_minutes':str(duration),'selection_error':selection_error,'policy':policy,
             'catalog_back':reverse('resources')+('?' + back.urlencode() if back else ''),
             'catalog_filters':[(key,value) for key,value in request.GET.items() if key in ui.CATALOG_KEYS and key not in ['starts_at','ends_at']]}
    return render(request,"resource.html",context)

@login_required
def safety_manage(request,pk):
    resource=get_object_or_404(Resource.objects.select_related("lab"),pk=pk)
    return _safety_manage(request,resource=resource)

@login_required
def lab_safety_manage(request,pk):
    lab=get_object_or_404(Lab,pk=pk)
    return _safety_manage(request,lab=lab)

def _safety_manage(request,resource=None,lab=None):
    target=lab or resource
    protected(bool(target) and s.manager(request.user,target))
    scope_filter={"lab_scope":lab} if lab else {"resource":resource}
    target_id=lab.pk if lab else resource.pk
    manage_route="safety-manage-lab" if lab else "safety-manage"
    draft=SafetyProcedure.objects.filter(**scope_filter,status="DRAFT",created_by=request.user).order_by("-version").first()
    initial={}
    if draft:
        initial={name:getattr(draft,name) for name in ["title","risk_categories","applicable_users","prerequisites","required_ppe","pre_use_checks","operating_cautions","prohibited_actions","incident_route","source_title","source_reference","source_version","source_effective_date","effective_from","review_due_date","requires_acknowledgement","change_reason"]}
    form=SafetyProcedureForm(request.POST or None,initial=initial)
    if request.method=="POST" and form.is_valid():
        try:
            procedure,_=safety_service.save_draft(request.user,resource.pk if resource else None,form.cleaned_data,draft.pk if draft else None,lab_id=lab.pk if lab else None)
            if request.POST.get("action")=="submit":
                safety_service.submit_for_review(request.user,procedure.pk)
                messages.success(request,"安全信息已提交审核；正式内容须由另一位授权管理员复核后发布")
            else:messages.success(request,"安全信息草稿已保存，尚未对申请人展示")
            return redirect(manage_route,pk=target_id)
        except (s.BusinessError,IntegrityError) as error:form.add_error(None,str(error) if isinstance(error,s.BusinessError) else "版本状态已变化，请刷新后重试")
    versions=SafetyProcedure.objects.filter(**scope_filter).select_related("created_by","reviewed_by").order_by("-version")[:10]
    target_name=lab.name if lab else f"{resource.lab.name} / {resource.name}"
    return_url=reverse("resources")+(f"?lab={lab.pk}" if lab else f"?lab={resource.lab_id}")
    return render(request,"safety_manage.html",{"title":"维护安全信息","target":target,"target_name":target_name,"return_url":return_url,"resource":resource,"lab":lab,"scope_label":"管理单元通用" if lab else "资源专属","form":form,"draft":draft,"versions":versions,"manage_route":manage_route,"target_id":target_id})

@login_required
@require_POST
def safety_review(request,pk,action):
    procedure=get_object_or_404(SafetyProcedure.objects.select_related("resource__lab","lab_scope"),pk=pk)
    target=procedure.lab_scope if procedure.lab_scope_id else procedure.resource
    protected(s.manager(request.user,target))
    manage_route="safety-manage-lab" if procedure.lab_scope_id else "safety-manage"
    target_id=procedure.lab_scope_id or procedure.resource_id
    try:
        if action=="submit":safety_service.submit_for_review(request.user,procedure.pk)
        elif action=="publish":safety_service.publish(request.user,procedure.pk,request.POST.get("review_note"))
        elif action=="return":safety_service.return_for_revision(request.user,procedure.pk,request.POST.get("review_note"))
        elif action=="revise":
            revision=safety_service.start_revision(request.user,procedure.pk)
            messages.success(request,f"已创建安全信息修订草稿 v{revision.version}；原退回版本保持不变")
            return redirect(manage_route,pk=target_id)
        else:raise Http404
        messages.success(request,"安全信息状态及复核记录已保存")
    except s.BusinessError as error:messages.error(request,str(error))
    return redirect(manage_route,pk=target_id)

@login_required
@require_POST
def safety_ack(request,pk,procedure_id):
    resource=get_object_or_404(Resource.objects.select_related("lab"),pk=pk)
    try:
        procedure=get_object_or_404(SafetyProcedure.objects.select_related("resource","lab_scope"),pk=procedure_id,status="PUBLISHED")
        safety_service.acknowledge(request.user,procedure.pk,resource=resource)
        messages.success(request,"已记录你确认阅读的安全信息版本；这不代表培训、考核或准入许可")
    except s.BusinessError as error:messages.error(request,str(error))
    return redirect("resource",pk=resource.pk)

@login_required
def bookings(request):
    qs=scoped_bookings(request.user);q=request.GET.get("q","");status=request.GET.get("status","");lab=request.GET.get("lab","");day=request.GET.get("day","");kind=request.GET.get("kind","")
    if request.GET.get('scope')=='mine':qs=qs.filter(applicant=request.user)
    queue=request.GET.get('queue','')
    if queue:
        protected(request.user.has_role('LAB_ADMIN'))
        qs=booking_queue(qs.filter(resource__lab__labgrant__user=request.user).distinct(),queue)
        if queue in ['review','recheck']:qs=qs.exclude(applicant=request.user)
    scope_total=qs.count() if any((q,status,lab,day,kind)) else None;selected_day=ui.parse_day(day);query_invalid=False
    if q:qs=qs.filter(Q(code__icontains=q)|Q(resource__name__icontains=q)|Q(resource__code__icontains=q)|Q(applicant__display_name__icontains=q))
    if status:qs=qs.filter(status=status)
    if lab:
        lab_id=s.positive_integer(lab)
        if lab_id is None:qs=qs.none();query_invalid=True;messages.error(request,"请选择有效的实验室")
        else:qs=qs.filter(resource__lab_id=lab_id)
    if day:
        if selected_day:qs=qs.filter(starts_at__date=selected_day)
        else:qs=qs.none();query_invalid=True;messages.error(request,"日期格式应为有效的年-月-日")
    if kind:
        if kind in ["ROOM","EQUIPMENT"]:qs=qs.filter(resource__kind=kind)
        else:qs=qs.none();query_invalid=True;messages.error(request,"资源类型参数无效")
    sort=request.GET.get('sort','oldest' if queue=='review' else 'newest')
    if sort not in ['oldest','newest','starts']:sort='oldest' if queue=='review' else 'newest'
    order={'oldest':('created_at','pk'),'newest':('-created_at','-pk'),'starts':('starts_at','pk')}[sort]
    page,filters=paginate(request,qs.order_by(*order))
    if scope_total is None:scope_total=page.paginator.count
    labs=list(Lab.objects.order_by('code'));lab_labels={str(l.pk):l.name for l in labs}
    scope_label='本人及获授权实验室的预约记录' if request.user.has_role('LAB_ADMIN') and request.GET.get('scope')!='mine' else '本人预约记录'
    if queue:scope_label+=' · 当前工作台队列'
    chips=ui.filter_chips('bookings',request.GET,ui.RECORD_KEYS,[('q','记录搜索',q),('status','状态',dict(Reservation.STATUS).get(status,'无效状态')),('lab','实验室',lab_labels.get(lab,'无效实验室')),('day','开始日期',day),('kind','资源类型',dict(Resource.KIND).get(kind,'无效类型'))])
    return render(request,"bookings.html",{"title":"预约与使用记录","bookings":page.object_list,"page":page,"filters":filters,"statuses":Reservation.STATUS,"labs":labs,'sort':sort,
        'date_nav':ui.date_navigation('bookings',request.GET,ui.RECORD_KEYS,selected=selected_day,all_dates=True),'schedule_url':ui.records_schedule_link(request.GET,request.user),
        'query_scope':scope_label,'query_total':scope_total,'query_count':page.paginator.count,'query_chips':chips,'query_invalid':query_invalid})

@login_required
def booking(request,pk):
    b=get_object_or_404(scoped_bookings(request.user),pk=pk)
    manageable=s.manager(request.user,b.resource)
    checks=approval_context(b,request.user) if manageable and b.applicant_id!=request.user.pk and b.status=='PENDING' else None
    return render(request,"booking.html",{"title":"预约详情","b":b,"manageable":manageable,"owner":b.applicant_id==request.user.pk,"notes":b.review_notes.all(),"incidents":b.incident_set.all(),"events":AuditEvent.objects.filter(entity_type="reservation",entity_id=b.pk).select_related("actor").order_by("created_at"),'next_step':next_step(b),'approval_checks':checks})

@login_required
def booking_action(request,pk,action):
    b=get_object_or_404(scoped_bookings(request.user),pk=pk);now=timezone.localtime().replace(microsecond=0)
    manager_actions={"review","checkout","return","room-start","room-end"}
    if action in manager_actions:protected(s.manager(request.user,b.resource))
    else:protected(b.applicant_id==request.user.pk)
    initial={"version":b.version,"arrived_at":now,"service_started_at":now,"returned_at":now,"ended_at":now,"participants":b.participants}
    classes={"review":ReviewForm,"checkout":CheckoutForm,"return":ReturnForm,"room-start":RoomStartForm,"room-end":RoomEndForm,"cancel":ActionForm,"supplement":ActionForm}
    titles={"review":"审核预约","checkout":"办理设备借出","return":"办理设备归还","room-start":"登记场地使用开始","room-end":"登记场地使用结束","cancel":"取消预约","supplement":"补充申请说明"}
    if action not in classes:raise Http404
    kwargs={"loan":b.loan} if action=="return" and hasattr(b,"loan") else {}
    form=classes[action](request.POST or None,initial=initial,**kwargs)
    if request.method=="POST" and form.is_valid():
        d=form.cleaned_data
        try:
            if action=="review":
                if d["choice"]=="info":s.review_note(request.user,pk,d["reason"])
                else:s.approve(request.user,pk,d["choice"]=="approve",d["reason"],d["version"])
            elif action=="cancel":s.cancel(request.user,pk,d["reason"],request.POST.get("version"))
            elif action=="supplement":s.review_note(request.user,pk,d["reason"])
            elif action=="checkout":
                result=s.checkout(request.user,pk,**d)
                if result is None:
                    messages.warning(request,"未借出：检查未通过，资源已隔离，请查看关联异常并处理")
                    return redirect("booking",pk=pk)
            elif action=="return":
                quantities={int(k[4:]):v for k,v in d.items() if k.startswith("qty_")};d={k:v for k,v in d.items() if not k.startswith("qty_")};s.return_equipment(request.user,pk,return_quantities=quantities,**d)
            elif action=="room-start":s.start_room(request.user,pk,d["school_no"],d["participants"],d["note"],d["arrived_at"])
            elif action=="room-end":s.end_room(request.user,pk,**d)
            messages.success(request,"办理结果已保存，记录与状态已同步");return redirect("booking",pk=pk)
        except s.BusinessError as e:form.add_error(None,str(e))
        except IntegrityError:form.add_error(None,"记录已被其他操作更新，请刷新后重试")
    checks=approval_context(b,request.user) if action=='review' and b.status=='PENDING' and b.applicant_id!=request.user.pk else None
    return render(request,"form.html",{"title":titles[action],"form":form,"subtitle":f"{b.code} · {b.resource.name} · {b.applicant.display_name}","back":reverse("booking",args=[pk]),"booking":b,"action":action,'approval_checks':checks})

@login_required
def incidents(request):
    qs=scoped_incidents(request.user)
    if request.GET.get('queue')=='managed':
        protected(request.user.has_role('LAB_ADMIN'))
        qs=qs.filter(resource__lab__labgrant__user=request.user).distinct()
    status=request.GET.get('status','')
    if status in dict(Incident.STATUS):qs=qs.filter(status=status)
    page,filters=paginate(request,qs.order_by("-reported_at","-pk"))
    return render(request,"incidents.html",{"title":"异常追踪","incidents":page.object_list,"page":page,"filters":filters})

@login_required
def incident_new(request):
    protected(request.user.has_role("STUDENT","TEACHER","LAB_ADMIN"))
    form=IncidentForm(request.POST or None,initial={"resource":request.GET.get("resource"),"reservation":request.GET.get("reservation"),"occurred_at":timezone.localtime().replace(second=0,microsecond=0)})
    form.fields["reservation"].queryset=scoped_bookings(request.user)
    if request.method=="POST" and form.is_valid():
        d=form.cleaned_data
        try:
            s.require(d["occurred_at"]<=timezone.now(),"发现时间不得晚于当前时间")
            item=s.report_incident(request.user,d["resource"].pk,d["kind"],d["description"],d["reservation"].pk if d["reservation"] else None,occurred_at=d["occurred_at"])
            messages.success(request,"异常已记录并通知负责人；是否隔离以核实和资源状态为准");return redirect("incident",pk=item.pk)
        except s.BusinessError as e:form.add_error(None,str(e))
    return render(request,"form.html",{"title":"提交资源异常","form":form,"subtitle":"请描述可核查的事实，场地和设备分别记录。"})

@login_required
def incident(request,pk):
    item=get_object_or_404(scoped_incidents(request.user),pk=pk)
    return render(request,"incident.html",{"title":"异常详情","item":item,"manageable":s.manager(request.user,item.resource),"evidence":item.evidence.all(),"work":item.work_orders.all()})

@login_required
def incident_action(request,pk,action):
    item=get_object_or_404(scoped_incidents(request.user),pk=pk)
    if action in ["verify","work"]:protected(s.manager(request.user,item.resource))
    classes={"verify":VerifyForm,"work":WorkCreateForm,"evidence":EvidenceForm}
    titles={"verify":"核实与处置异常","work":"创建与指派维修工单","evidence":"追加异常证据"}
    if action not in classes:raise Http404
    form=classes[action](request.POST or None,request.FILES or None)
    if request.method=="POST" and form.is_valid():
        try:
            d=form.cleaned_data
            if action=="verify":s.verify_incident(request.user,pk,d["choice"],d["reason"])
            elif action=="work":s.create_work_order(request.user,pk,d["technician"].pk,d["reason"])
            else:s.upload_evidence(request.user,pk,d["file"])
            messages.success(request,"处置记录已保存");return redirect("incident",pk=pk)
        except s.BusinessError as e:form.add_error(None,str(e))
    return render(request,"form.html",{"title":titles[action],"form":form,"subtitle":item.resource.name,"multipart":True})

@login_required
def evidence_download(request,pk):
    ev=get_object_or_404(Evidence.objects.select_related("incident__resource__lab"),pk=pk)
    protected(s.incident_access(request.user,ev.incident));s.audit(request.user,ev.incident,"EVIDENCE_READ",new={"evidence_id":ev.pk},reason="读取受控证据")
    return FileResponse(ev.file.open("rb"),as_attachment=True,filename=ev.original_name)

@login_required
def work_list(request):
    qs=scoped_work(request.user)
    status=request.GET.get('status','')
    if status=='ACTIVE':qs=qs.filter(status__in=['OPEN','IN_PROGRESS'],assigned_to=request.user)
    elif status in dict(WorkOrder.STATUS):qs=qs.filter(status=status)
    if request.GET.get('queue')=='acceptance':
        protected(request.user.has_role('LAB_ADMIN'))
        qs=acceptance_queue(qs,request.user)
    page,filters=paginate(request,qs.order_by("-created_at","-pk"))
    return render(request,"work.html",{"title":"维修任务","work":page.object_list,"page":page,"filters":filters})

@login_required
def work_detail(request,pk):
    w=get_object_or_404(scoped_work(request.user),pk=pk)
    return render(request,"work_detail.html",{"title":"维修任务详情","w":w,"manageable":s.manager(request.user,w.incident.resource),"assigned":w.assigned_to_id==request.user.pk,"events":w.events.select_related("actor").order_by("created_at")})

@login_required
def work_action(request,pk,action):
    w=get_object_or_404(scoped_work(request.user),pk=pk)
    protected(s.manager(request.user,w.incident.resource) if action=="accept" else request.user.has_role("TECHNICIAN") and w.assigned_to_id==request.user.pk)
    if action not in ["accept","update"]:raise Http404
    form=(AcceptanceForm if action=="accept" else WorkUpdateForm)(request.POST or None)
    if request.method=="POST" and form.is_valid():
        try:
            d=form.cleaned_data
            if action=="accept":s.accept_work(request.user,pk,d["choice"]=="pass",d["reason"])
            else:s.update_work(request.user,pk,d["choice"],d["reason"])
            messages.success(request,"维修过程已记录");return redirect("work-detail",pk=pk)
        except s.BusinessError as e:form.add_error(None,str(e))
    return render(request,"form.html",{"title":"验收维修" if action=="accept" else "处理维修任务","form":form,"subtitle":w.incident.resource.name})

@login_required
def notifications(request):
    if request.method=="POST":
        request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now());return redirect("notifications")
    page,filters=paginate(request,request.user.notifications.prefetch_related("deliveries").order_by("-created_at","-pk"))
    return render(request,"notifications.html",{"title":"消息中心","notifications":page.object_list,"page":page,"filters":filters})

@login_required
def audit_list(request):
    protected(request.user.has_role("LAB_ADMIN","SYS_ADMIN","DIRECTOR"))
    qs=AuditEvent.objects.select_related("actor","lab")
    if not request.user.has_role("SYS_ADMIN","DIRECTOR"):qs=qs.filter(lab__labgrant__user=request.user)
    if request.GET.get("q"):qs=qs.filter(Q(action__icontains=request.GET["q"])|Q(reason__icontains=request.GET["q"])|Q(entity_type__icontains=request.GET["q"]))
    page,filters=paginate(request,qs.order_by("-created_at","-pk"))
    return render(request,"audit.html",{"title":"审计记录","events":page.object_list,"page":page,"filters":filters})

@login_required
def reports(request):
    protected(request.user.has_role("LAB_ADMIN", "DIRECTOR"))
    now = timezone.now()
    start_input = request.GET.get("start", "")
    end_input = request.GET.get("end", "")
    default_start, _ = report_window(now)
    start_input = start_input or default_start.date().isoformat()
    end_input = end_input or now.astimezone(s.TZ).date().isoformat()
    rows, error, page, end = [], "", None, None
    try:
        start, end = report_window(now, start_input, end_input)
        resources = Resource.objects.select_related("lab")
        if not request.user.has_role("DIRECTOR"):
            resources = resources.filter(lab__labgrant__user=request.user)
        result_page = Paginator(resources.order_by("code"), 50).get_page(request.GET.get("page", 1))
        rows = utilization(result_page.object_list, start, end, at=now)
        page = result_page
    except s.BusinessError as exc:
        error = str(exc)
    return render(request, "reports.html", {
        "title": "利用与运行报表", "rows": rows, "end": end, "error": error, "page": page,
        "start_input": start_input, "end_input": end_input, "default_report_days": DEFAULT_REPORT_DAYS,
    })

@login_required
def manage(request):
    protected(request.user.has_role("LAB_ADMIN","SYS_ADMIN"))
    resource_q=request.GET.get("resource_q","").strip();user_q=request.GET.get("user_q","").strip()
    resource_lab=request.GET.get("resource_lab","");user_role=request.GET.get("user_role","")
    resources=Resource.objects.filter(lab__labgrant__user=request.user).select_related("lab","room__building__campus","equipment__access_room__room__building__campus").distinct() if request.user.has_role("LAB_ADMIN") else Resource.objects.none()
    users=User.objects.prefetch_related("roles").all() if request.user.has_role("SYS_ADMIN") else User.objects.none()
    if resource_q:resources=resources.filter(Q(code__icontains=resource_q)|Q(name__icontains=resource_q))
    if resource_lab:
        if resource_lab.isascii() and resource_lab.isdecimal() and len(resource_lab)<=19 and 0<int(resource_lab)<=9223372036854775807:resources=resources.filter(lab_id=resource_lab)
        else:resources=resources.none();messages.error(request,"请选择有效的实验室")
    if user_q:users=users.filter(Q(username__icontains=user_q)|Q(school_no__icontains=user_q)|Q(display_name__icontains=user_q))
    if user_role:users=users.filter(roles__code=user_role).distinct()
    resource_page,resource_filters=paginate(request,resources.order_by("code","pk"),page_param="resources_page")
    user_page,user_filters=paginate(request,users.order_by("school_no","pk"),page_param="users_page")
    section=request.GET.get('section','overview')
    if section not in ['overview','resources','people','calendars','access','locations']:section='overview'
    if section in ['people','access','locations']:protected(request.user.has_role('SYS_ADMIN'))
    visible_labs=Lab.objects.all() if request.user.has_role('SYS_ADMIN') else Lab.objects.filter(labgrant__user=request.user).distinct()
    context_extra={'section':section,'directory_labs':visible_labs.order_by('code'),

        'qualification_types':QualificationType.objects.order_by('code'),
        'role_directory':Role.objects.prefetch_related('users').order_by('code') if request.user.has_role('SYS_ADMIN') else Role.objects.none(),
        'campuses':Campus.objects.prefetch_related('buildings').order_by('code') if request.user.has_role('SYS_ADMIN') else Campus.objects.none(),
        'buildings':Building.objects.select_related('campus').prefetch_related('rooms').order_by('campus__code','code') if request.user.has_role('SYS_ADMIN') else Building.objects.none()}
    if section=='calendars':context_extra.update(calendar_catalog(request,visible_labs))
    return render(request,"manage.html",{**context_extra,"title":"管理中心","resources":resource_page.object_list,"users":user_page.object_list,"labs":Lab.objects.all(),"resource_labs":Lab.objects.filter(labgrant__user=request.user).distinct().order_by("code"),"roles":Role.objects.order_by("code"),"policy":Policy.objects.get(pk=1),"resource_page":resource_page,"resource_filters":resource_filters,"user_page":user_page,"user_filters":user_filters,"resource_q":resource_q,"user_q":user_q,"resource_lab":resource_lab,"user_role":user_role})

@login_required
def manage_form(request,action,pk=None):
    protected(request.user.has_role("LAB_ADMIN","SYS_ADMIN") if action in ["resource","hold","opening"] else request.user.has_role("SYS_ADMIN"))
    if action not in ["resource","hold","grant","policy","opening","lab","campus","building"]:raise Http404
    initial={};r=None;location_obj=None
    if action in ["resource","hold"] and pk:
        r=get_object_or_404(Resource,pk=pk);protected(s.manager(request.user,r))
        initial={"code":r.code,"name":r.name,"kind":r.kind,"lab":r.lab,"description":r.description}
        if r.kind=="ROOM":initial.update({"building":r.room.building,"floor_label":r.room.floor_label,"room_number":r.room.room_number,"room_location":r.room.location_label,"capacity":r.room.capacity,"space_category":r.room.space_category,"facilities":r.room.facilities})
        else:initial.update({"category":r.equipment.category,"risk":r.equipment.risk,"qualification":r.equipment.qualification,"portable":r.equipment.portable,"access_room":r.equipment.access_room,"handover_location":r.equipment.handover_location,"accessories":"\n".join(f"{a.code},{a.name},{a.quantity}" for a in r.accessories.filter(is_active=True))})
    if action=="grant" and pk:
        u=get_object_or_404(User,pk=pk);initial={"user":u,"roles":u.roles.all(),"labs":Lab.objects.filter(labgrant__user=u),"active":u.is_active}
    if action=="policy":initial=Policy.objects.get(pk=1).snapshot()
    if action=="campus" and pk:
        location_obj=get_object_or_404(Campus,pk=pk)
        initial={"code":location_obj.code,"name":location_obj.name,"active":location_obj.is_active}
    if action=="building" and pk:
        location_obj=get_object_or_404(Building.objects.select_related("campus"),pk=pk)
        initial={"campus":location_obj.campus,"code":location_obj.code,"name":location_obj.name,"address_label":location_obj.address_label,"active":location_obj.is_active}
    cls={"resource":ResourceForm,"hold":ActionForm,"grant":GrantForm,"policy":PolicyForm,"opening":OpeningForm,"lab":LabForm,"campus":CampusForm,"building":BuildingForm}[action]
    form=cls(request.POST or None,initial=initial)
    if action=="hold":form.fields["hold"]=forms.ChoiceField(label="手工服务限制",choices=[("","解除手工限制（仍检查异常与工单）"),("DISABLED","停用"),("SCRAPPED","报废")])
    if action=="resource":
        form.fields["lab"].queryset=Lab.objects.filter(labgrant__user=request.user)
        form.fields["building"].queryset=Building.objects.filter(is_active=True,campus__is_active=True).select_related("campus").order_by("campus__code","code")
    if action=="campus":
        form.fields["code"].disabled=bool(location_obj)
    if action=="building":
        form.fields["campus"].queryset=Campus.objects.all().order_by("code")
        if location_obj:
            form.fields["campus"].disabled=True
            form.fields["code"].disabled=True
        else:
            form.fields["campus"].queryset=Campus.objects.filter(is_active=True).order_by("code")
    if action=="opening":
        is_system_admin = request.user.has_role("SYS_ADMIN")
        is_lab_admin = request.user.has_role("LAB_ADMIN")
        granted_labs = Lab.objects.filter(labgrant__user=request.user)
        form.fields["lab"].queryset = Lab.objects.all() if is_system_admin else granted_labs
        room_options = Resource.objects.none()
        if is_lab_admin:
            room_options = Resource.objects.filter(kind="ROOM", lab__in=granted_labs)
        selected_lab_id = s.positive_integer(request.POST.get("lab"))
        if selected_lab_id:
            room_options = room_options.filter(lab_id=selected_lab_id)
        form.fields["resource"].queryset = room_options.order_by("lab__code", "code")
        form.fields["resource"].required = not is_system_admin
    if request.method=="POST" and form.is_valid():
        d=form.cleaned_data
        try:
            if action=="resource":s.save_resource(request.user,d,pk)
            elif action=="hold":s.set_resource_hold(request.user,pk,d["hold"],d["reason"])
            elif action=="grant":s.grant_user(request.user,d)
            elif action=="policy":s.save_policy(request.user,d,d["reason"])
            elif action=="opening":s.change_opening(request.user,d)
            elif action=="lab":
                with transaction.atomic():
                    obj=Lab.objects.create(code=d["code"],name=d["name"],location=d["location"],description=d["description"]);s.audit(request.user,obj,"LAB_CREATE",new=d,reason=d["reason"])
            elif action=="campus":
                with transaction.atomic():
                    if location_obj:
                        old={"code":location_obj.code,"name":location_obj.name,"is_active":location_obj.is_active}
                        location_obj.name=d["name"];location_obj.is_active=d["active"]
                        location_obj.save(update_fields=["name","is_active"]);obj=location_obj;action_code="CAMPUS_EDIT"
                    else:
                        obj=Campus.objects.create(code=d["code"],name=d["name"],is_active=d["active"]);old={};action_code="CAMPUS_CREATE"
                    s.audit(request.user,obj,action_code,old,{"code":obj.code,"name":obj.name,"is_active":obj.is_active},d["reason"])
            elif action=="building":
                with transaction.atomic():
                    if location_obj:
                        old={"campus_id":location_obj.campus_id,"code":location_obj.code,"name":location_obj.name,"address_label":location_obj.address_label,"is_active":location_obj.is_active}
                        location_obj.name=d["name"];location_obj.address_label=d["address_label"];location_obj.is_active=d["active"]
                        location_obj.save(update_fields=["name","address_label","is_active"]);obj=location_obj;action_code="BUILDING_EDIT"
                    else:
                        obj=Building.objects.create(campus=d["campus"],code=d["code"],name=d["name"],address_label=d["address_label"],is_active=d["active"]);old={};action_code="BUILDING_CREATE"
                    s.audit(request.user,obj,action_code,old,{"campus_id":obj.campus_id,"campus":obj.campus.name,"code":obj.code,"name":obj.name,"address_label":obj.address_label,"is_active":obj.is_active},d["reason"])
            messages.success(request,"设置已保存，变更依据已记录");return redirect("manage")
        except (s.BusinessError,IntegrityError) as e:form.add_error(None,str(e) if isinstance(e,s.BusinessError) else "编号重复或记录已变化，请检查")
    title={"resource":"维护资源档案","hold":"调整服务限制","grant":"维护用户角色与管理范围","policy":"预约与通知参数","opening":"开放日历版本","lab":"新增资源管理单元","campus":"维护校区档案" if location_obj else "新增校区档案","building":"维护楼宇档案" if location_obj else "新增楼宇档案"}[action]
    return render(request,"form.html",{"title":title,"form":form,"subtitle":"位置主数据必须依据经核验的资产清册录入；模拟目录应明确标注。变更原因与操作者将留痕，既有业务事实不覆盖。"})

@login_required
def deliveries(request):
    protected(request.user.has_role("LAB_ADMIN","SYS_ADMIN"))
    channel="MOCK_EXTERNAL" if settings.DEMO_MODE else "EXTERNAL"
    qs=Delivery.objects.filter(channel=channel).select_related("notification__recipient","notification__audit")
    if not request.user.has_role("SYS_ADMIN"):qs=qs.filter(notification__audit__lab__labgrant__user=request.user)
    if request.method=="POST":
        raw_pk=request.POST.get("pk", "")
        delivery_id=s.positive_integer(raw_pk)
        if delivery_id is None:
            messages.error(request,"请选择有效投递记录")
            return redirect("deliveries")
        task=get_object_or_404(qs,pk=delivery_id)
        try:s.record_manual_contact(request.user,task.pk,request.POST.get("reason",""));messages.success(request,"人工联系结果已追加至审计")
        except s.BusinessError as e:messages.error(request,str(e))
        return redirect("deliveries")
    page,filters=paginate(request,qs.order_by("-pk"))
    return render(request,"deliveries.html",{"title":"外部通知投递","deliveries":page.object_list,"page":page,"filters":filters})
