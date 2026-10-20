import json,secrets,uuid,hashlib
from datetime import datetime,timedelta
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
from django.views.decorators.http import require_POST
from .models import *
from .forms import *
from . import services as s
from .reports import utilization
from .demo_identity import verify_demo_password

def paginate(request,queryset,size=30):
    page=Paginator(queryset,size).get_page(request.GET.get('page',1))
    query=request.GET.copy();query.pop('page',None)
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
    accounts=User.objects.filter(is_active=True).order_by("pk")
    error=""
    if request.method=="POST":
        username=request.POST.get("username","")
        user=verify_demo_password(username,request.POST.get("password",""))
        if user:
            ticket=signing.dumps({"sub":user.school_no,"nonce":request.session["idp_nonce"]},key=settings.MOCK_IDP_KEY,salt="mock-idp")
            return redirect(reverse("callback")+"?"+urlencode({"ticket":ticket}))
        error="模拟账号或演示密码不正确，或账号已停用"
    return render(request,"identity.html",{"title":"模拟学校身份认证","accounts":accounts,"error":error})

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
    login(request,user,backend="django.contrib.auth.backends.ModelBackend");s.audit(user,user,"LOGIN",new={"identity":"MOCK"},reason="课程模拟身份断言校验通过")
    return redirect("dashboard")

@require_POST
def signout(request):
    logout(request);return redirect("login")

@login_required
def dashboard(request):
    now=timezone.now();mine=Reservation.objects.filter(applicant=request.user)
    context={"title":"工作台","pending":mine.filter(status="PENDING").count(),"confirmed":mine.filter(status="CONFIRMED").count(),"using":mine.filter(status="IN_USE").count(),"recent":scoped_bookings(request.user)[:6],"today":now,"resource_count":Resource.objects.count(),"lab_count":Lab.objects.count(),"policy":Policy.objects.get(pk=1),"overdue":s.overdue_for(request.user,now)}
    if request.user.has_role("LAB_ADMIN"):context["to_review"]=scoped_bookings(request.user).filter(status="PENDING").exclude(applicant=request.user).count()
    return render(request,"dashboard.html",context)

@login_required
def resources(request):
    form=QueryForm(request.GET or None);qs=Resource.objects.select_related("lab","equipment__qualification","room").order_by("kind","code")
    times=None
    if form.is_bound and form.is_valid():
        d=form.cleaned_data
        if d["q"]:qs=qs.filter(Q(name__icontains=d["q"])|Q(code__icontains=d["q"]))
        if d["kind"]:qs=qs.filter(kind=d["kind"])
        if d["lab"]:qs=qs.filter(lab=d["lab"])
        if d["starts_at"]:times=(d["starts_at"],d["ends_at"])
    page,filters=paginate(request,qs)
    items=list(page.object_list)
    for r in items:
        r.available_label="请选择目标时段查询"
        r.available=None
        if times:r.available,r.available_label=s.availability(r,*times,applicant=request.user)
    return render(request,"resources.html",{"title":"资源目录","form":form,"resources":items,"page":page,"filters":filters,"times":times,"policy":Policy.objects.get(pk=1)})

@login_required
def resource_detail(request,pk):
    r=get_object_or_404(Resource.objects.select_related("lab","equipment__qualification","room"),pk=pk)
    start=timezone.localtime()+timedelta(days=1);start=start.replace(hour=10,minute=0,second=0,microsecond=0)
    form=BookingForm(request.POST or None,initial={"starts_at":start,"ends_at":start+timedelta(hours=1),"request_key":uuid.uuid4()})
    if r.kind=="EQUIPMENT":form.fields["participants"].widget=forms.HiddenInput()
    if request.method=="POST" and form.is_valid():
        try:
            b=s.create_reservation(request.user,r.pk,**form.cleaned_data)
            messages.success(request,"预约已保存："+b.get_status_display());return redirect("booking",pk=b.pk)
        except s.BusinessError as e:form.add_error(None,str(e))
        except IntegrityError:form.add_error(None,"时段或请求已被其他操作占用，请刷新后重试")
    return render(request,"resource.html",{"title":r.name,"resource":r,"form":form,"can_apply":request.user.has_role("TEACHER") if r.kind=="ROOM" else request.user.has_role("STUDENT","TEACHER"),"manageable":s.manager(request.user,r),"reservations":r.reservations.filter(status__in=s.ACTIVE).order_by("starts_at")[:15]})

@login_required
def bookings(request):
    qs=scoped_bookings(request.user);q=request.GET.get("q","");status=request.GET.get("status","");lab=request.GET.get("lab","");day=request.GET.get("day","")
    if q:qs=qs.filter(Q(code__icontains=q)|Q(resource__name__icontains=q)|Q(resource__code__icontains=q)|Q(applicant__display_name__icontains=q))
    if status:qs=qs.filter(status=status)
    if lab.isdigit():qs=qs.filter(resource__lab_id=lab)
    if day:
        try:qs=qs.filter(starts_at__date=datetime.strptime(day,"%Y-%m-%d").date())
        except ValueError:messages.error(request,"日期格式应为年-月-日")
    page,filters=paginate(request,qs.order_by("-created_at","-pk"))
    return render(request,"bookings.html",{"title":"预约与使用记录","bookings":page.object_list,"page":page,"filters":filters,"statuses":Reservation.STATUS,"labs":Lab.objects.all()})

@login_required
def booking(request,pk):
    b=get_object_or_404(scoped_bookings(request.user),pk=pk)
    return render(request,"booking.html",{"title":"预约详情","b":b,"manageable":s.manager(request.user,b.resource),"owner":b.applicant_id==request.user.pk,"notes":b.review_notes.all(),"incidents":b.incident_set.all(),"events":AuditEvent.objects.filter(entity_type="reservation",entity_id=b.pk).select_related("actor").order_by("created_at")})

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
    return render(request,"form.html",{"title":titles[action],"form":form,"subtitle":f"{b.code} · {b.resource.name} · {b.applicant.display_name}","back":reverse("booking",args=[pk]),"booking":b,"action":action})

@login_required
def incidents(request):
    page,filters=paginate(request,scoped_incidents(request.user).order_by("-reported_at","-pk"))
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
    page,filters=paginate(request,scoped_work(request.user).order_by("-created_at","-pk"))
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
    protected(request.user.has_role("LAB_ADMIN","DIRECTOR"));now=timezone.now();start=(now-timedelta(days=7)).replace(hour=0,minute=0,second=0,microsecond=0);end=now
    error="";page=None
    try:
        if request.GET.get("start"):start=timezone.make_aware(datetime.strptime(request.GET["start"],"%Y-%m-%d"))
        if request.GET.get("end"):end=min(now,timezone.make_aware(datetime.strptime(request.GET["end"],"%Y-%m-%d"))+timedelta(days=1))
        qs=Resource.objects.select_related("lab")
        if not request.user.has_role("DIRECTOR"):qs=qs.filter(lab__labgrant__user=request.user)
        page=Paginator(qs.order_by("code"),50).get_page(request.GET.get("page",1));rows=utilization(page.object_list,start,end)
    except (ValueError,s.BusinessError) as e:rows=[];error=str(e)
    return render(request,"reports.html",{"title":"利用与运行报表","rows":rows,"start":start,"end":end,"error":error,"page":page})

@login_required
def manage(request):
    protected(request.user.has_role("LAB_ADMIN","SYS_ADMIN"))
    return render(request,"manage.html",{"title":"资源与配置","resources":Resource.objects.filter(lab__labgrant__user=request.user).select_related("lab").order_by("code") if request.user.has_role("LAB_ADMIN") else [],"users":User.objects.prefetch_related("roles").all() if request.user.has_role("SYS_ADMIN") else [],"labs":Lab.objects.all(),"policy":Policy.objects.get(pk=1)})

@login_required
def manage_form(request,action,pk=None):
    protected(request.user.has_role("LAB_ADMIN") if action in ["resource","hold"] else request.user.has_role("SYS_ADMIN"))
    if action not in ["resource","hold","grant","policy","opening","lab"]:raise Http404
    initial={};r=None
    if action in ["resource","hold"] and pk:
        r=get_object_or_404(Resource,pk=pk);protected(s.manager(request.user,r))
        initial={"code":r.code,"name":r.name,"kind":r.kind,"lab":r.lab,"description":r.description}
        if r.kind=="ROOM":initial["capacity"]=r.room.capacity
        else:initial.update({"category":r.equipment.category,"risk":r.equipment.risk,"qualification":r.equipment.qualification,"portable":r.equipment.portable,"access_room":r.equipment.access_room,"handover_location":r.equipment.handover_location,"accessories":"\n".join(f"{a.code},{a.name},{a.quantity}" for a in r.accessories.filter(is_active=True))})
    if action=="grant" and pk:
        u=get_object_or_404(User,pk=pk);initial={"user":u,"roles":u.roles.all(),"labs":Lab.objects.filter(labgrant__user=u),"active":u.is_active}
    if action=="policy":initial=Policy.objects.get(pk=1).snapshot()
    cls={"resource":ResourceForm,"hold":ActionForm,"grant":GrantForm,"policy":PolicyForm,"opening":OpeningForm,"lab":LabForm}[action]
    form=cls(request.POST or None,initial=initial)
    if action=="hold":form.fields["hold"]=forms.ChoiceField(label="手工服务限制",choices=[("","解除手工限制（仍检查异常与工单）"),("DISABLED","停用"),("SCRAPPED","报废")])
    if action=="resource":form.fields["lab"].queryset=Lab.objects.filter(labgrant__user=request.user)
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
            messages.success(request,"设置已保存，变更依据已记录");return redirect("manage")
        except (s.BusinessError,IntegrityError) as e:form.add_error(None,str(e) if isinstance(e,s.BusinessError) else "编号重复或记录已变化，请检查")
    return render(request,"form.html",{"title":{"resource":"维护资源档案","hold":"调整服务限制","grant":"维护用户角色与管理范围","policy":"预约与通知参数","opening":"开放日历版本","lab":"新增实验室"}[action],"form":form,"subtitle":"变更将保留操作者与依据，既有借还事实及审计不可覆盖。"})

@login_required
def deliveries(request):
    protected(request.user.has_role("LAB_ADMIN","SYS_ADMIN"))
    qs=Delivery.objects.filter(channel="MOCK_EXTERNAL").select_related("notification__recipient","notification__audit")
    if not request.user.has_role("SYS_ADMIN"):qs=qs.filter(notification__audit__lab__labgrant__user=request.user)
    if request.method=="POST":
        raw_pk=request.POST.get("pk", "")
        if not raw_pk.isdigit() or len(raw_pk)>18:
            messages.error(request,"请选择有效投递记录")
            return redirect("deliveries")
        task=get_object_or_404(qs,pk=int(raw_pk))
        try:s.record_manual_contact(request.user,task.pk,request.POST.get("reason",""));messages.success(request,"人工联系结果已追加至审计")
        except s.BusinessError as e:messages.error(request,str(e))
        return redirect("deliveries")
    page,filters=paginate(request,qs.order_by("-pk"))
    return render(request,"deliveries.html",{"title":"外部通知投递","deliveries":page.object_list,"page":page,"filters":filters})
