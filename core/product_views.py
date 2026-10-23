"""Cross-resource day schedule: public occupancy, never public applicant data."""
from datetime import datetime, time, timedelta
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from .models import Lab, Resource, Reservation, OpeningPeriod, CalendarException
from .services import TZ, ACTIVE


class ScheduleQuery(forms.Form):
    day = forms.DateField(label='日期', widget=forms.DateInput(attrs={'type':'date'}))
    kind = forms.ChoiceField(label='资源类型', required=False, choices=[('', '全部类型')]+Resource.KIND)
    lab = forms.ModelChoiceField(label='实验室', required=False, queryset=Lab.objects.order_by('code'), empty_label='全部实验室')
    q = forms.CharField(label='资源名称或编号', required=False, max_length=100)

    def clean_day(self):
        value = self.cleaned_data['day']
        if not 1900 <= value.year <= 2100:
            raise forms.ValidationError('日程日期须在1900年至2100年之间')
        return value


@login_required
def schedule(request):
    params = request.GET.copy()
    if 'day' not in params: params['day'] = timezone.localdate().isoformat()
    form = ScheduleQuery(params)
    rows, page, previous, following = [], None, '', ''
    if form.is_valid():
        data = form.cleaned_data; day = data['day']
        a = datetime.combine(day, time(), TZ); z = a+timedelta(days=1)
        qs = Resource.objects.select_related('lab').order_by('lab__code','kind','code')
        if data['kind']: qs = qs.filter(kind=data['kind'])
        if data['lab']: qs = qs.filter(lab=data['lab'])
        if data['q']: qs = qs.filter(Q(code__icontains=data['q'])|Q(name__icontains=data['q']))
        page = Paginator(qs, 20).get_page(request.GET.get('page',1))
        items = list(page.object_list); ids = [r.pk for r in items]; labs = {r.lab_id for r in items}
        slots = Reservation.objects.filter(resource_id__in=ids,status__in=ACTIVE,starts_at__lt=z).filter(Q(ends_at__gt=a)|Q(status='IN_USE')).order_by('starts_at','pk')
        grouped = {pk: [] for pk in ids}
        for b in slots:
            left = max(a,b.starts_at); right = min(z,b.ends_at)
            grouped[b.resource_id].append({'start':b.starts_at,'end':b.ends_at,'status':b.status,
                'label':b.get_status_display(),'recheck':b.review_required,
                'carry':b.status=='IN_USE' and b.ends_at<=a,
                'left':round(max(0,(left-a).total_seconds())/864,3),
                'width':round(max(0,(right-left).total_seconds())/864,3)})
        openings = list(OpeningPeriod.objects.filter(lab_id__in=labs,weekday=day.isoweekday(),valid_from__lte=day).filter(Q(valid_to__isnull=True)|Q(valid_to__gte=day)).order_by('starts_at'))
        exceptions = {e.lab_id:e for e in CalendarException.objects.filter(lab_id__in=labs,day=day,active=True)}
        for r in items:
            exception = exceptions.get(r.lab_id)
            times = ([] if exception.closed else [(exception.starts_at,exception.ends_at)]) if exception else [(o.starts_at,o.ends_at) for o in openings if o.lab_id==r.lab_id]
            windows = [{'start':x,'end':y,'left':(x.hour*60+x.minute)/14.4,'width':((y.hour-x.hour)*60+y.minute-x.minute)/14.4} for x,y in times]
            rows.append({'resource':r,'events':grouped[r.pk],'windows':windows,'exception':bool(exception)})
        for key, delta in [('previous',-1),('following',1)]:
            target = day+timedelta(days=delta)
            if 1900<=target.year<=2100:
                query=params.copy();query['day']=target.isoformat();query.pop('page',None)
                if key=='previous':previous=query.urlencode()
                else:following=query.urlencode()
    query=request.GET.copy();query.pop('page',None)
    return render(request,'schedule.html',{'title':'资源日程','form':form,'rows':rows,'page':page,'filters':query.urlencode(),'previous':previous,'following':following})
