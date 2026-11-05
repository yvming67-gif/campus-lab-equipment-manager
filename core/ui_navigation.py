"""Read-only navigation metadata; it never decides resource availability or access."""
import re
from datetime import date, timedelta

from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

CATALOG_KEYS = ('q', 'kind', 'lab', 'starts_at', 'ends_at')
RECORD_KEYS = ('q', 'status', 'lab', 'day', 'scope', 'queue', 'sort', 'page')
CONTEXT_KEYS = ('origin', 'record_user', 'catalog_starts_at', 'catalog_ends_at') + tuple('record_' + k for k in RECORD_KEYS)
SCHEDULE_KEYS = ('day', 'q', 'kind', 'lab') + CONTEXT_KEYS


def pick_query(params, keys):
    query = QueryDict(mutable=True)
    for key in keys:
        value = params.get(key, '')
        if value:
            query[key] = value
    return query


def link(name, params, keys, *, updates=None, drop=()):
    query = pick_query(params, keys)
    for key in drop:
        query.pop(key, None)
    for key, value in (updates or {}).items():
        if value is None or value == '':
            query.pop(key, None)
        else:
            query[key] = str(value)
    return reverse(name) + ('?' + query.urlencode() if query else '')


def parse_day(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value or ''):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def date_navigation(name, params, keys, *, selected=None, all_dates=False):
    today = timezone.localdate()
    anchor = selected if selected and 1900 <= selected.year <= 2100 else today
    monday = anchor - timedelta(days=anchor.weekday())
    days = []
    for offset in range(7):
        value = monday + timedelta(days=offset)
        if not 1900 <= value.year <= 2100:
            continue
        days.append({'date': value, 'weekday': '一二三四五六日'[value.weekday()],
                     'today': value == today, 'selected': value == selected,
                     'url': link(name, params, keys, updates={'day': value.isoformat()}, drop=('page',))})
    weeks = {}
    for key, delta in [('previous', -7), ('following', 7)]:
        value = anchor + timedelta(days=delta)
        weeks[key] = link(name, params, keys, updates={'day': value.isoformat()}, drop=('page',)) if 1900 <= value.year <= 2100 else ''
    return {'days': days, **weeks,
            'today_url': link(name, params, keys, updates={'day': today.isoformat()}, drop=('page',)),
            'all_url': link(name, params, keys, drop=('day', 'page')) if all_dates else '',
            'all_selected': all_dates and selected is None}


def filter_chips(name, params, keys, definitions):
    return [{'label': label, 'value': str(value), 'url': link(name, params, keys, drop=(key, 'page'))}
            for key, label, value in definitions if params.get(key) and value]


def catalog_schedule_link(params, cleaned):
    target = pick_query(params, ('q', 'kind', 'lab'))
    target['origin'] = 'catalog'
    start, end = cleaned.get('starts_at'), cleaned.get('ends_at')
    if start and end:
        target['day'] = timezone.localtime(start).date().isoformat()
        target['catalog_starts_at'] = params['starts_at']
        target['catalog_ends_at'] = params['ends_at']
    return link('schedule', target, SCHEDULE_KEYS)


def records_schedule_link(params, user):
    target = QueryDict(mutable=True)
    target['origin'] = 'records'
    target['record_user'] = str(user.pk)
    for key, value in pick_query(params, RECORD_KEYS).items():
        target['record_' + key] = value
    for key in ('day', 'lab'):
        if params.get(key):
            target[key] = params[key]
    return link('schedule', target, SCHEDULE_KEYS)


def schedule_context(params, user):
    """Context only. The destination views always apply their own permissions."""
    safe = pick_query(params, SCHEDULE_KEYS)
    origin = safe.get('origin')
    if origin == 'records' and safe.get('record_user') == str(user.pk):
        back = QueryDict(mutable=True)
        for key in RECORD_KEYS:
            if safe.get('record_' + key):
                back[key] = safe['record_' + key]
        return {'label': '返回预约记录（恢复原筛选）', 'url': link('bookings', back, RECORD_KEYS),
                'notice': '此处展示公开的有效占用。申请人或编号搜索、预约状态和工作台队列只用于预约记录；返回时恢复原筛选。',
                'hidden': [(key, value) for key, value in safe.items() if key in CONTEXT_KEYS]}
    catalog = pick_query(safe, ('q', 'kind', 'lab'))
    timed = origin == 'catalog' and safe.get('catalog_starts_at') and safe.get('catalog_ends_at')
    if timed:
        catalog['starts_at'], catalog['ends_at'] = safe['catalog_starts_at'], safe['catalog_ends_at']
    return {'label': '返回资源目录（保留原时段）' if timed else '资源目录',
            'url': link('resources', catalog, CATALOG_KEYS),
            'notice': '日程按一天展示公开占用；返回目录保留原查询起止时段，预约提交仍重新检查。' if timed else '资源目录与日程共用实验室、资源类型及资源关键词；占用空白不代表可以直接预约。',
            'hidden': [(key, value) for key, value in safe.items() if key in ('origin', 'catalog_starts_at', 'catalog_ends_at')] if origin == 'catalog' else []}
