"""Read-only work queues. State transitions remain in the transaction services."""
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from .models import AuditEvent


def acceptance_queue(qs, user):
    performed = AuditEvent.objects.filter(entity_type='workorder',actor=user,action__in=['START','PROGRESS','SUBMIT']).values('entity_id')
    return qs.filter(incident__resource__lab__labgrant__user=user,status='AWAITING_ACCEPTANCE').exclude(assigned_to=user).exclude(pk__in=performed).distinct()


def booking_queue(qs, name, now=None):
    now = now or timezone.now()
    rules = {
        'review': Q(status='PENDING'),
        'recheck': Q(status='CONFIRMED', review_required=True),
        'handover': Q(status='CONFIRMED', starts_at__date=timezone.localdate(now), review_required=False),
        'overdue': Q(status='IN_USE', ends_at__lt=now),
    }
    return qs.filter(rules[name]) if name in rules else qs


def tasks_for(user, bookings, incidents, work, now=None):
    now = now or timezone.now()
    cards = []
    if user.has_role('LAB_ADMIN'):
        managed = bookings.filter(resource__lab__labgrant__user=user).distinct()
        for key, name, note in [
            ('review', '待审核申请', '先核对用途、资格与可用时段'),
            ('recheck', '待复核承诺', '条件变化后先复核，再交接'),
            ('handover', '今日待交接', '已确认不代表已经领取或入场'),
            ('overdue', '超期未结束', '核实实物与人员，协调后续使用'),
        ]:
            qs = booking_queue(managed, key, now)
            if key in ['review','recheck']: qs = qs.exclude(applicant=user)
            cards.append({'name': name, 'count': qs.count(), 'note': note,
                          'url': reverse('bookings')+'?queue='+key})
        pending = incidents.filter(resource__lab__labgrant__user=user, status='PENDING').distinct()
        cards.append({'name': '异常待核实', 'count': pending.count(), 'note': '核实事实后决定隔离、处置或维修', 'url': reverse('incidents')+'?queue=managed&status=PENDING'})
        acceptance = acceptance_queue(work,user)
        cards.append({'name': '维修待验收', 'count': acceptance.count(), 'note': '执行人与验收人分离', 'url': reverse('work')+'?queue=acceptance&status=AWAITING_ACCEPTANCE'})
    if user.has_role('TECHNICIAN'):
        cards.append({'name': '我的维修任务', 'count': work.filter(assigned_to=user, status__in=['OPEN','IN_PROGRESS']).count(),
                      'note': '更新处理过程，提交后等待独立验收', 'url': reverse('work')+'?status=ACTIVE'})
    return cards
