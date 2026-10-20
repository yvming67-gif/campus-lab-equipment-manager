"""Operational status has a separate, authorized surface from public readiness."""
from datetime import timedelta
import logging
from django.db import transaction
from django.utils import timezone
from .models import JobHeartbeat,Reservation,WorkOrder,User,Policy,AuditEvent
from . import services as s


def escalations(at):
    counts=0
    pending=Reservation.objects.filter(status='PENDING').filter(created_at__lte=at-timedelta(hours=24)) | Reservation.objects.filter(status='PENDING',starts_at__lte=at+timedelta(hours=1))
    for booking in pending.distinct():
        key=f'approval-escalation:{booking.pk}:{at.astimezone(s.TZ).date()}'
        with transaction.atomic():
            # Same booking lock order as approval; dedupe survives a restart.
            b,_,_=s._booking_lock(booking.pk)
            if b.status!='PENDING' or AuditEvent.objects.filter(dedupe_key=key).exists():continue
            event=s.audit(None,b,'APPROVAL_ESCALATION',new={'status':b.status},reason='申请临近开始或等待超过24小时，请有范围的值守管理员处理',at=at,dedupe_key=key)
            s.notify(event,list(s.lab_managers(b.resource)),'APPROVAL_ESCALATION',f'待办{b.code}需值守或备岗处理；开始前未获批将失效',at);counts+=1
    return counts


def healthy_worker(at=None):
    at=at or timezone.now();item=JobHeartbeat.objects.filter(name='business-jobs').first()
    return bool(item and item.last_success and at-item.last_success<=timedelta(seconds=90) and (not item.last_error_at or item.last_error_at<item.last_success))


def rotate_log(path,maximum=5*1024*1024,keep=5):
    if not path.exists() or path.stat().st_size<maximum:return
    for index in range(keep,0,-1):
        source=path if index==1 else path.with_name(path.name+'.'+str(index-1))
        if source.exists():source.replace(path.with_name(path.name+'.'+str(index)))
