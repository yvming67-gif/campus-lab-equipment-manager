"""Scoped, searchable management catalog. This module does not change calendars."""
from django import forms
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from .models import Lab, OpeningPeriod, CalendarException
from .opening_calendar import merge_time_windows


def filter_resources(queryset, values):
    """Apply the shared web/mini catalog filters without changing availability rules."""
    if values.get("q"):
        term=values["q"]
        queryset=queryset.filter(Q(name__icontains=term)|Q(code__icontains=term))
    if values.get("kind"):
        queryset=queryset.filter(kind=values["kind"])
    if values.get("campus"):
        campus=values["campus"]
        queryset=queryset.filter(
            Q(kind="ROOM",room__building__campus=campus)|
            Q(kind="EQUIPMENT",equipment__portable=False,equipment__access_room__room__building__campus=campus)
        )
    if values.get("building"):
        building=values["building"]
        queryset=queryset.filter(
            Q(kind="ROOM",room__building=building)|
            Q(kind="EQUIPMENT",equipment__portable=False,equipment__access_room__room__building=building)
        )
    if values.get("lab"):
        queryset=queryset.filter(lab=values["lab"])
    if values.get("category"):
        term=values["category"]
        queryset=queryset.filter(Q(equipment__category__icontains=term)|Q(room__space_category__icontains=term))
    if values.get("facility"):
        term=values["facility"]
        queryset=queryset.filter(Q(room__facilities__icontains=term)|Q(accessories__name__icontains=term)|Q(description__icontains=term))
    if values.get("capacity_min"):
        queryset=queryset.filter(kind="ROOM",room__capacity__gte=values["capacity_min"])
    if values.get("capacity_max"):
        queryset=queryset.filter(kind="ROOM",room__capacity__lte=values["capacity_max"])
    if values.get("service_status"):
        queryset=queryset.filter(service_status=values["service_status"])
    return queryset.distinct().order_by("kind","code")


class CalendarCatalogFilter(forms.Form):
    directory_q = forms.CharField(label='管理单元名称、编号或地点',required=False,max_length=100)
    directory_lab = forms.ModelChoiceField(label='管理单元',required=False,queryset=Lab.objects.none(),empty_label='全部有权管理单元')
    calendar_from = forms.DateField(label='日历起始日期',widget=forms.DateInput(attrs={'type':'date'}))
    calendar_to = forms.DateField(label='日历结束日期（可留空）',required=False,widget=forms.DateInput(attrs={'type':'date'}))

    def clean(self):
        data=super().clean();a,b=data.get('calendar_from'),data.get('calendar_to')
        if a and b and a>b:self.add_error('calendar_to','结束日期不得早于起始日期')
        return data


def calendar_catalog(request,visible_labs):
    params=request.GET.copy()
    if 'calendar_from' not in params:params['calendar_from']=timezone.localdate().isoformat()
    form=CalendarCatalogFilter(params)
    form.fields['directory_lab'].queryset=visible_labs.order_by('code')
    labs=visible_labs;openings=OpeningPeriod.objects.none();exceptions=CalendarException.objects.none()
    if form.is_valid():
        data=form.cleaned_data
        if data['directory_q']:
            q=data['directory_q'];labs=labs.filter(Q(code__icontains=q)|Q(name__icontains=q)|Q(location__icontains=q))
        if data['directory_lab']:labs=labs.filter(pk=data['directory_lab'].pk)
        openings=OpeningPeriod.objects.filter(lab__in=labs).select_related('lab','resource').filter(Q(valid_to__isnull=True)|Q(valid_to__gte=data['calendar_from']))
        exceptions=CalendarException.objects.filter(lab__in=labs,active=True,day__gte=data['calendar_from'])
        if data['calendar_to']:
            openings=openings.filter(valid_from__lte=data['calendar_to'])
            exceptions=exceptions.filter(day__lte=data['calendar_to'])
    else:labs=labs.none()
    result={'catalog_form':form}
    for key,qs,size,parameter in [
        ('lab',labs.order_by('code','pk'),12,'labs_page'),
        ('exception',exceptions.select_related('lab').order_by('day','lab__code','pk'),20,'exceptions_page')]:
        page=Paginator(qs,size).get_page(request.GET.get(parameter,1))
        query=params.copy();query['section']='calendars';query.pop(parameter,None)
        result[key+'_catalog_page']=page;result[key+'_catalog_filters']=query.urlencode()
    opening_keys=openings.values('lab_id','resource_id','weekday','valid_from','valid_to').distinct().order_by(
        'lab_id','resource_id','weekday','valid_from','valid_to')
    opening_page=Paginator(opening_keys,30).get_page(request.GET.get('openings_page',1))
    group_query=Q(pk__in=[])
    for key in opening_page.object_list:
        group_query|=Q(lab_id=key['lab_id'],resource_id=key['resource_id'],weekday=key['weekday'],
                       valid_from=key['valid_from'],valid_to=key['valid_to'])
    opening_groups={}
    for row in openings.filter(group_query).order_by('lab__code','resource__code','weekday','valid_from','starts_at'):
        key=(row.lab_id,row.resource_id,row.weekday,row.valid_from,row.valid_to)
        group=opening_groups.setdefault(key,{'lab':row.lab,'resource':row.resource,'weekday':row.weekday,
            'valid_from':row.valid_from,'valid_to':row.valid_to,'closed':False,'windows':[]})
        if row.closed_all_day:
            group['closed']=True
        else:
            group['windows'].append((row.starts_at,row.ends_at))
    for group in opening_groups.values():
        group['windows']=merge_time_windows(group['windows'])
    query=params.copy();query['section']='calendars';query.pop('openings_page',None)
    result['opening_catalog_page']=opening_page;result['opening_catalog_filters']=query.urlencode()
    result['opening_period_groups']=list(opening_groups.values())
    result.update(directory_labs=result['lab_catalog_page'].object_list,
                  opening_periods=result['opening_period_groups'],
                  calendar_exceptions=result['exception_catalog_page'].object_list)
    return result
