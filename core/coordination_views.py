from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render,redirect,get_object_or_404
from django.http import Http404,JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from . import services as s, coordination as c
from .models import *
from .forms import CoordinationForm,RecheckForm,CalendarExceptionForm,WorkCreateForm,IncidentResponseForm
from .views import scoped_bookings,scoped_incidents,scoped_work,protected


@login_required
def booking_operation(request,pk,action):
    booking=get_object_or_404(scoped_bookings(request.user),pk=pk);protected(s.manager(request.user,booking.resource))
    if action not in ['coordinate','recheck']:raise Http404
    form=(CoordinationForm if action=='coordinate' else RecheckForm)(request.POST or None,initial={'version':booking.version,'arrived_at':timezone.localtime()})
    if request.method=='POST' and form.is_valid():
        try:
            d=form.cleaned_data
            if action=='coordinate':c.coordinate(request.user,pk,d['action'],d['reason'],d['arrived_at'])
            else:c.recheck(request.user,pk,d['accepted']=='yes',d['reason'],d['version'])
            messages.success(request,'协调或复核已保存，原审批和交接历史保留');return redirect('booking',pk=pk)
        except s.BusinessError as error:form.add_error(None,str(error))
    return render(request,'form.html',{'title':'预约协调' if action=='coordinate' else '预约复核','form':form,'subtitle':booking.code})


@login_required
def calendar(request):
    protected(request.user.has_role('SYS_ADMIN'))
    form=CalendarExceptionForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            d=form.cleaned_data;c.calendar_exception(request.user,d['lab'].pk,d['day'],d['closed'],d['starts_at'],d['ends_at'],d['reason'])
            messages.success(request,'单日例外已保存，受影响预约已通知待协调');return redirect('manage')
        except s.BusinessError as error:form.add_error(None,str(error))
    return render(request,'form.html',{'title':'单日开放例外','form':form,'subtitle':'例外替代所选日期的整日开放安排；今天的变更也会重算今天已发生时段的报表，原始使用记录保留。保存前请核对日期、范围及原因。'})


@login_required
def reassign(request,pk):
    work=get_object_or_404(scoped_work(request.user),pk=pk);protected(s.manager(request.user,work.incident.resource))
    form=WorkCreateForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            c.reassign_work(request.user,pk,form.cleaned_data['technician'].pk,form.cleaned_data['reason'])
            messages.success(request,'维修任务已重新指派，历史责任保留');return redirect('work-detail',pk=pk)
        except s.BusinessError as error:form.add_error(None,str(error))
    return render(request,'form.html',{'title':'重新指派维修','form':form,'subtitle':work.code})


@login_required
def respond(request,pk):
    item=get_object_or_404(scoped_incidents(request.user),pk=pk)
    form=IncidentResponseForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            c.respond_incident(request.user,pk,form.cleaned_data['kind'],form.cleaned_data['reason'])
            messages.success(request,'确认、异议或复核结论已追加');return redirect('incident',pk=pk)
        except s.BusinessError as error:form.add_error(None,str(error))
    return render(request,'form.html',{'title':'异常确认与异议','form':form,'subtitle':'独立复核需另一名有范围管理员，不覆盖原事实。'})


def operations_health_snapshot():
    from .operations import healthy_worker
    from django.conf import settings
    import json
    marker=settings.LOCAL/'last-backup.json';fresh=False
    if marker.exists():
        try:
            last=timezone.datetime.fromisoformat(json.loads(marker.read_text('utf-8'))['last_success'])
            fresh=timezone.now()-last<timezone.timedelta(hours=24)
        except (OSError,ValueError,KeyError,TypeError):pass
    worker=healthy_worker()
    import shutil,os
    folder=settings.LOCAL/'backups'
    backup_bytes=sum(p.stat().st_size for p in folder.glob('backup-*.zip') if p.is_file() and not p.is_symlink())
    free_bytes=shutil.disk_usage(settings.LOCAL).free
    try:
        capacity=int(os.environ.get('CAMPUS_BACKUP_CAPACITY_BYTES',str(10*1024**3)))
        minimum_free=int(os.environ.get('CAMPUS_BACKUP_MIN_FREE_BYTES',str(1024**3)))
        if capacity<=0 or minimum_free<=0:raise ValueError
    except ValueError:
        return {'error': '备份容量预警阈值配置无效'}, 503
    storage_ok=backup_bytes<capacity and free_bytes>=minimum_free
    backlog=Delivery.objects.filter(status__in=['PENDING','RETRY','PROCESSING'],next_attempt_at__lt=timezone.now()-timezone.timedelta(minutes=5)).count()
    replica=False
    if marker.exists():
        try:replica=bool(json.loads(marker.read_text('utf-8')).get('replica_verified'))
        except (OSError,ValueError):pass
    failed=Delivery.objects.filter(status='FAILED').count()
    summary = {'worker_recent':worker, 'backup_recent':fresh, 'delivery_failed':failed,
               'delivery_overdue':backlog, 'replica_verified':replica, 'storage_ok':storage_ok,
               'backup_bytes':backup_bytes, 'free_bytes':free_bytes}
    status = 200 if worker and fresh and not backlog and not failed and storage_ok else 503
    return summary, status


@login_required
@never_cache
def operations_health(request):
    protected(request.user.has_role("SYS_ADMIN"))
    summary, status = operations_health_snapshot()
    return JsonResponse(summary, status=status)


@login_required
@never_cache
@require_GET
def operations_overview(request):
    protected(request.user.has_role("SYS_ADMIN"))
    summary, status = operations_health_snapshot()
    heartbeat = JobHeartbeat.objects.filter(name="business-jobs").only("last_success", "last_error_at").first()
    return render(request, "operations_overview.html", {
        "title": "运行健康", "health": summary, "attention": status != 200,
        "heartbeat": heartbeat, "as_of": timezone.now(),
    })
