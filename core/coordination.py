"""Controlled exceptions to the normal workflow. Original commitments remain facts."""
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import *
from . import services as s
from .request_context import correlated


def resource_profile(resource):
    if resource.kind == 'ROOM':
        return {'capacity':resource.room.capacity}
    e=resource.equipment
    return {'risk':e.risk,'qualification_id':e.qualification_id,'portable':e.portable,'access_room_id':e.access_room_id}


def check_calendar(resource,start,end):
    a,b=start.astimezone(s.TZ),end.astimezone(s.TZ)
    exception=CalendarException.objects.filter(lab_id=resource.lab_id,day=a.date(),active=True).first()
    if exception:
        s.require(not exception.closed,'该日实验室关闭')
        s.require(exception.starts_at<=a.time().replace(tzinfo=None) and b.time().replace(tzinfo=None)<=exception.ends_at,'超出该日例外开放时段')
        return True
    return False


def check_dependency(resource,start,end,applicant=None,at=None,check_actual=False):
    at=at or timezone.now()
    if resource.kind=='ROOM':
        if check_actual:
            using=Loan.objects.filter(reservation__resource__equipment__access_room=resource,reservation__resource__equipment__portable=False,return_record__isnull=True,handed_over_at__lte=at)
            if applicant:using=using.exclude(reservation__applicant=applicant)
            s.require(not using.exists(),'固定设备仍由他人实际使用，不能开始场地使用，请协调真实结束')
        fixed=Reservation.objects.filter(resource__equipment__access_room=resource,resource__equipment__portable=False,status__in=s.ACTIVE,starts_at__lt=end,ends_at__gt=start)
        if applicant:fixed=fixed.exclude(applicant=applicant)
        s.require(not fixed.exists(),'场地时段与固定设备使用冲突，请联系管理员协调')
        return
    if resource.kind!='EQUIPMENT' or resource.equipment.portable:return
    room=resource.equipment.access_room
    s.require(room is not None and room.kind=='ROOM' and room.lab_id==resource.lab_id,'固定设备需关联同实验室场地')
    s.require(room.service_status=='NORMAL' and room.lab.is_active,'固定设备所在场地不可用')
    if check_actual:
        using=RoomUse.objects.filter(reservation__resource=room,ended_at__isnull=True,started_at__lte=at)
        if applicant:using=using.exclude(reservation__applicant=applicant)
        s.require(not using.exists(),'依赖场地仍由他人实际使用，不能交接固定设备')
    check_calendar(room,start,end)
    clashes=Reservation.objects.filter(resource=room,status__in=s.ACTIVE,starts_at__lt=end,ends_at__gt=start)
    if applicant:clashes=clashes.exclude(applicant=applicant)
    s.require(not clashes.exists(),'固定设备取放与场地占用冲突，请联系管理员协调')


def flag_affected(queryset,actor,reason,at):
    """Caller holds the resource/lab or applicant gate. Only booking rows are locked."""
    for booking in queryset.select_for_update(of=('self',)).order_by('pk'):
        old={'review_required':booking.review_required,'version':booking.version}
        booking.review_required=True;booking.review_reason=reason;booking.version+=1
        booking.save(update_fields=['review_required','review_reason','version'])
        event=s.audit(actor,booking,'IMPACT_REVIEW',old,{'review_required':True,'version':booking.version},reason,at)
        s.notify(event,[booking.applicant]+list(s.lab_managers(booking.resource)),'ARRANGEMENT_IMPACT',f'预约{booking.code}暂停交接待复核：{reason}',at)


@correlated
@transaction.atomic
def recheck(actor,booking_id,accepted,reason,expected=None,at=None):
    at=at or timezone.now();booking,user,resource=s._booking_lock(booking_id)
    s.check_manager(actor,resource);s.require(actor.pk!=user.pk,'不能复核本人预约');s._version(booking,expected)
    s.require(booking.status=='CONFIRMED' and booking.review_required,'该预约无需复核或已结束')
    s.require(reason.strip(),'请填写复核依据')
    if accepted:
        policy=Policy.objects.get(pk=1)
        deadline=booking.late_start_until or booking.starts_at+timedelta(minutes=policy.grace_minutes)
        s.require(at<booking.ends_at and at<=deadline,'预约领取截止已过，请协调取消后重新申请')
        s.validate(booking,policy,at,exclude=booking.pk,time_at=min(at,booking.starts_at))
    record=ReservationReview.objects.create(reservation=booking,actor=actor,accepted=accepted,reason=reason,snapshot=resource_profile(resource),created_at=at)
    old={'status':booking.status,'review_required':True}
    if accepted:booking.review_required=False;booking.review_reason=''
    else:booking.status='CANCELLED';booking.reason=reason;booking.review_required=False;booking.review_reason=''
    booking.version+=1;booking.save(update_fields=['status','reason','review_required','review_reason','version'])
    event=s.audit(actor,booking,'RECHECK',old,{'status':booking.status,'review_required':booking.review_required,'review_id':record.pk},reason,at)
    s.notify(event,[user]+list(s.lab_managers(resource)),'RECHECK',f'预约{booking.code}复核：'+('通过' if accepted else '取消')+'。'+reason,at)
    return record


@correlated
@transaction.atomic
def coordinate(actor,booking_id,action,reason,arrived_at=None,at=None):
    at=at or timezone.now();booking,user,resource=s._booking_lock(booking_id);s.check_manager(actor,resource)
    s.require(reason.strip(),'协调原因必填');s.require(booking.status in s.FUTURE,'仅未开始使用的预约可以协调')
    old={'status':booking.status,'arrived_at':booking.arrived_at,'late_start_until':booking.late_start_until,'review_required':booking.review_required,'review_reason':booking.review_reason}
    if action=='WAIT':
        p=Policy.objects.get(pk=1);s.require(booking.status=='CONFIRMED','待审核申请不能登记等待')
        s.require(arrived_at is not None and timezone.is_aware(arrived_at),'请记录有效到场时间')
        s.require(booking.starts_at<=arrived_at<=min(at,booking.starts_at+timedelta(minutes=p.grace_minutes)) and at<booking.ends_at,'到场超出原宽限或预约已结束')
        if resource.kind=='EQUIPMENT':
            delayed=Loan.objects.filter(reservation__resource=resource,handed_over_at__lte=arrived_at,due_at__lte=arrived_at).exclude(reservation=booking).filter(Q(return_record__isnull=True)|Q(return_record__returned_at__gt=arrived_at)).exists()
        else:
            delayed=RoomUse.objects.filter(reservation__resource=resource,started_at__lte=arrived_at,reservation__ends_at__lte=arrived_at).exclude(reservation=booking).filter(Q(ended_at__isnull=True)|Q(ended_at__gt=arrived_at)).exists()
        s.require(delayed,'未查到前序延误事实，不可放宽领取时限')
        booking.arrived_at=arrived_at;booking.late_start_until=booking.ends_at
    elif action=='CANCEL':booking.status='CANCELLED';booking.reason=reason;booking.review_required=False;booking.review_reason=''
    else:raise s.BusinessError('协调动作无效')
    booking.version+=1;s.finish_booking(booking,actor,['status','reason','arrived_at','late_start_until','version'],at) if action=='CANCEL' else booking.save(update_fields=['status','reason','arrived_at','late_start_until','version'])
    new={'status':booking.status,'arrived_at':booking.arrived_at,'late_start_until':booking.late_start_until,'review_required':booking.review_required,'review_reason':booking.review_reason}
    item=Coordination.objects.create(reservation=booking,actor=actor,action=action,reason=reason,old_state=s.json_state(old),new_state=s.json_state(new),created_at=at)
    event=s.audit(actor,booking,'COORDINATE',old,new,reason,at)
    s.notify(event,[user]+list(s.lab_managers(resource)),'COORDINATE',f'预约{booking.code}已协调：{reason}。使用结束时间不延长。',at)
    return item


@correlated
@transaction.atomic
def calendar_exception(actor,lab_id,day,closed,starts_at,ends_at,reason,at=None):
    at=at or timezone.now();s.require(actor.has_role('SYS_ADMIN'),'仅系统管理员维护单日开放例外')
    lab=Lab.objects.select_for_update().get(pk=lab_id)
    s.require(day>=at.astimezone(s.TZ).date() and reason.strip(),'生效日期不能过去，原因必填')
    s.require(closed or (starts_at and ends_at and starts_at<ends_at),'例外开放时段无效')
    old=list(CalendarException.objects.filter(lab=lab,day=day,active=True).values('id','closed','starts_at','ends_at'))
    CalendarException.objects.filter(lab=lab,day=day,active=True).update(active=False)
    obj=CalendarException.objects.create(lab=lab,day=day,closed=closed,starts_at=starts_at,ends_at=ends_at,actor=actor,reason=reason,created_at=at)
    s.audit(actor,lab,'CALENDAR_EXCEPTION',{'versions':old},{'id':obj.pk,'day':day,'closed':closed,'start':starts_at,'end':ends_at},reason,at)
    affected=Reservation.objects.filter(resource__lab=lab,status__in=s.FUTURE,starts_at__date=day)
    if not closed:affected=affected.filter(Q(starts_at__time__lt=starts_at)|Q(ends_at__time__gt=ends_at))
    flag_affected(affected,actor,'开放日历例外：'+reason,at)
    for b in Reservation.objects.filter(resource__lab=lab,status='IN_USE',starts_at__date=day):
        ev=s.audit(actor,b,'CALENDAR_RECALL',{'status':'IN_USE'},{'status':'IN_USE'},reason,at)
        s.notify(ev,[b.applicant]+list(s.lab_managers(b.resource)),'RECALL','场地开放发生变化，请立即联系管理员；实际使用记录保留。',at)
    return obj


@correlated
@transaction.atomic
def reassign_work(actor,work_id,technician_id,reason,at=None):
    at=at or timezone.now();initial=WorkOrder.objects.select_related('incident').get(pk=work_id)
    resource=Resource.objects.select_for_update(no_key=True).get(pk=initial.incident.resource_id);s.check_manager(actor,resource)
    work=WorkOrder.objects.select_for_update().get(pk=work_id);tech=User.objects.get(pk=technician_id)
    s.require(work.status!='CLOSED' and tech.has_role('TECHNICIAN') and reason.strip(),'工单已关闭、维修人员无效或缺少依据')
    old=work.assigned_to;work.assigned_to=tech;work.save(update_fields=['assigned_to'])
    WorkOrderEvent.objects.create(work_order=work,actor=actor,old_status=work.status,new_status=work.status,note=f'重新指派：{old.school_no} → {tech.school_no}；{reason}',created_at=at)
    ev=s.audit(actor,work,'REASSIGN',{'assigned_to':old.pk},{'assigned_to':tech.pk},reason,at)
    s.notify(ev,[old,tech]+list(s.lab_managers(resource)),'REASSIGN',f'工单{work.code}已重新指派：{reason}',at)
    return work


@correlated
@transaction.atomic
def respond_incident(actor,incident_id,kind,content,at=None):
    at=at or timezone.now();item=Incident.objects.select_for_update(of=('self',)).select_related('resource','reservation').get(pk=incident_id)
    s.require(s.incident_access(actor,item) and content.strip(),'无权处理或说明为空')
    s.require(kind in dict(IncidentResponse._meta.get_field('kind').choices),'回应类型无效')
    if kind=='REVIEW':
        s.check_manager(actor,item.resource)
        s.require(actor.pk!=item.reporter_id and (not item.reservation_id or actor.pk!=item.reservation.applicant_id),'独立复核不能由报告人或借用人办理')
    obj=IncidentResponse.objects.create(incident=item,actor=actor,kind=kind,content=content,created_at=at)
    ev=s.audit(actor,item,'INCIDENT_RESPONSE',new={'response_id':obj.pk,'kind':kind},reason=content,at=at)
    recipients=[item.reporter]+list(s.lab_managers(item.resource))
    if item.reservation_id:recipients.append(item.reservation.applicant)
    s.notify(ev,recipients,'INCIDENT_RESPONSE',f'异常{item.code}有新的确认、异议或复核记录',at)
    return obj
