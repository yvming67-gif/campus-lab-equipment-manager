from datetime import timedelta
from .models import *

def reservations_for(user):
    q=Reservation.objects.select_related('equipment__laboratory','applicant')
    if user.role in ['student','teacher']: return q.filter(applicant=user)
    if user.role == 'manager': return q.filter(equipment__laboratory__in=user.managed_labs.all())
    return q.none()

def loans_for(user):
    q=Loan.objects.select_related('equipment__laboratory','borrower','reservation')
    if user.role in ['student','teacher']: return q.filter(borrower=user)
    if user.role == 'manager': return q.filter(equipment__laboratory__in=user.managed_labs.all())
    return q.none()

def incidents_for(user):
    q=Incident.objects.select_related('equipment','reporter')
    if user.role in ['student','teacher']: return q.filter(reporter=user)
    if user.role=='manager': return q.filter(equipment__laboratory__in=user.managed_labs.all())
    if user.role=='technician': return q.filter(work_orders__assignee=user).distinct()
    return q.none()

def work_for(user):
    q=WorkOrder.objects.select_related('equipment','assignee','incident')
    if user.role=='manager': return q.filter(equipment__laboratory__in=user.managed_labs.all())
    if user.role=='technician': return q.filter(assignee=user)
    return q.none()

def overlap_seconds(a,b,start,end):
    return max(0,(min(b,end)-max(a,start)).total_seconds())

def utilisation(equipment,start,end,now):
    """Past observation only; exclude non-normal service time from both numerator/denominator."""
    finish=min(end,now)
    if finish<=start: return 0,0,0
    downtime=list(equipment.downtimes.filter(start__lt=finish).filter(Q(end__gt=start)|Q(end__isnull=True)))
    total=(finish-start).total_seconds()
    excluded=sum(overlap_seconds(d.start,d.end or finish,start,finish) for d in downtime)
    available_seconds=max(0,total-excluded)
    used=0
    for loan in Loan.objects.filter(equipment=equipment,issued_at__lt=finish).filter(Q(closed_at__gt=start)|Q(closed_at__isnull=True)):
        a=max(loan.issued_at,start); b=min(loan.closed_at or now,finish)
        duration=max(0,(b-a).total_seconds())
        duration-=sum(overlap_seconds(d.start,d.end or finish,a,b) for d in downtime)
        used+=max(0,duration)
    return round(used/3600,2),round(available_seconds/3600,2),round(100*used/available_seconds,2) if available_seconds else 0
