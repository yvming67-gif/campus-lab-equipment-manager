from datetime import datetime,timedelta
from django.db.models import Q
from django.utils import timezone
from .models import *
from .services import TZ, BusinessError

def intersection(a,b,c,d):return max(0,(min(b,d)-max(a,c)).total_seconds())
def utilization(resources,start,end,at=None):
    at=at or timezone.now()
    if start>=end or end>at or (end-start).days>31:raise BusinessError("报表仅统计已发生的时间，单次跨度不超过31天")
    rows=[]
    for r in resources:
        windows=[];day=start.astimezone(TZ).date();last=end.astimezone(TZ).date()
        openings=list(OpeningPeriod.objects.filter(lab=r.lab));periods=list(r.service_periods.all())
        while day<=last:
            for op in openings:
                if op.weekday==day.isoweekday() and op.valid_from<=day and (not op.valid_to or day<=op.valid_to):
                    a=max(start,datetime.combine(day,op.starts_at,TZ),r.created_at);b=min(end,datetime.combine(day,op.ends_at,TZ))
                    if a<b:
                        for sp in periods:
                            if sp.status=="NORMAL":
                                x=max(a,sp.starts_at);y=min(b,sp.ends_at or end)
                                if x<y:windows.append((x,y))
            day+=timedelta(days=1)
        capacity=sum((b-a).total_seconds() for a,b in windows)
        uses=[]
        if r.kind=="EQUIPMENT":
            for loan in Loan.objects.filter(reservation__resource=r,handed_over_at__lt=end).select_related("return_record"):
                z=loan.return_record.returned_at if hasattr(loan,"return_record") else min(at,end)
                uses.append((loan.handed_over_at,z))
        else:
            uses=[(x.started_at,x.ended_at or min(at,end)) for x in RoomUse.objects.filter(reservation__resource=r,started_at__lt=end)]
        actual=sum(intersection(a,b,c,d) for a,b in uses for c,d in windows)
        booked=sum(intersection(b.starts_at,b.ends_at,c,d) for b in Reservation.objects.filter(resource=r,status__in=["CONFIRMED","IN_USE","COMPLETED"]) for c,d in windows)
        rows.append({"resource":r,"capacity":round(capacity/3600,2),"actual":round(actual/3600,2),"booked":round(booked/3600,2),"rate":round(actual/capacity*100,1) if capacity else None})
    return rows
