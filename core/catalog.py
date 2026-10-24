"""Scoped, searchable management catalog. This module does not change calendars."""
from django import forms
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from .models import Lab, OpeningPeriod, CalendarException


class CalendarCatalogFilter(forms.Form):
    directory_q = forms.CharField(label='实验室名称、编号或地点',required=False,max_length=100)
    directory_lab = forms.ModelChoiceField(label='实验室',required=False,queryset=Lab.objects.none(),empty_label='全部有权实验室')
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
        openings=OpeningPeriod.objects.filter(lab__in=labs).filter(Q(valid_to__isnull=True)|Q(valid_to__gte=data['calendar_from']))
        exceptions=CalendarException.objects.filter(lab__in=labs,active=True,day__gte=data['calendar_from'])
        if data['calendar_to']:
            openings=openings.filter(valid_from__lte=data['calendar_to'])
            exceptions=exceptions.filter(day__lte=data['calendar_to'])
    else:labs=labs.none()
    result={'catalog_form':form}
    for key,qs,size,parameter in [
        ('lab',labs.order_by('code','pk'),12,'labs_page'),
        ('opening',openings.select_related('lab').order_by('lab__code','weekday','valid_from','pk'),30,'openings_page'),
        ('exception',exceptions.select_related('lab').order_by('day','lab__code','pk'),20,'exceptions_page')]:
        page=Paginator(qs,size).get_page(request.GET.get(parameter,1))
        query=params.copy();query['section']='calendars';query.pop(parameter,None)
        result[key+'_catalog_page']=page;result[key+'_catalog_filters']=query.urlencode()
    result.update(directory_labs=result['lab_catalog_page'].object_list,
                  opening_periods=result['opening_catalog_page'].object_list,
                  calendar_exceptions=result['exception_catalog_page'].object_list)
    return result
