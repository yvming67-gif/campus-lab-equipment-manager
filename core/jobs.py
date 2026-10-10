"""Database outbox with leased claims; external calls occur outside business tx."""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import BusinessConfig, Notification, Loan, MessageDelivery
from .services import expire_locked, notify, trace_id

def schedule(now=None):
    now=now or timezone.now()
    with transaction.atomic():
        BusinessConfig.objects.select_for_update().get(pk=1)
        token=trace_id.set(f'worker:{now.isoformat()}')
        try:
            expired=expire_locked(now)
            for loan in Loan.objects.filter(closed_at__isnull=True):
                if loan.due_at <= now:
                    notify(loan.borrower,f'loan:{loan.pk}:overdue',f'{loan.equipment.tag} 已逾期，请尽快归还。',now)
                elif loan.due_at-now <= timedelta(hours=1):
                    notify(loan.borrower,f'loan:{loan.pk}:due',f'{loan.equipment.tag} 将于一小时内到期，请准备归还。',now)
            return expired
        finally: trace_id.reset(token)

def simulated_send(notification, mode=None):
    """Local fake provider honours event_key idempotency; never sends SMS/mail."""
    mode=mode or settings.SIMULATED_MESSAGE_MODE
    if mode == 'fail': raise RuntimeError('模拟外部消息服务不可用')
    if mode != 'success': raise RuntimeError('模拟消息模式无效')
    MessageDelivery.objects.get_or_create(event_key=notification.event_key)

def deliver(now=None, mode=None, limit=100, sender=None):
    now=now or timezone.now()
    count=0
    # Claim bounded rows. Lease allows recovery after process termination.
    for _ in range(limit):
        with transaction.atomic():
            q=Notification.objects.select_for_update(skip_locked=True).filter(
                status__in=['pending','sending'],next_attempt__lte=now).order_by('id')
            n=q.filter(status='pending').first() or q.filter(status='sending',lease_until__lte=now).first()
            if not n: break
            lease=uuid.uuid4()
            n.status='sending'; n.lease_until=now+timedelta(minutes=2); n.lease_token=lease
            n.save(update_fields=['status','lease_until','lease_token'])
        error=''
        try:
            if sender: sender(n)
            else: simulated_send(n,mode)
        except Exception as exc: error=str(exc)[:200]
        with transaction.atomic():
            n=Notification.objects.select_for_update().get(pk=n.pk)
            if n.lease_token != lease: continue
            n.attempts += 1
            if not error:
                n.status='sent'; n.sent_at=now; n.last_error=''
            else:
                n.last_error=error
                if n.attempts >= 4: n.status='failed'
                else:
                    n.status='pending'
                    n.next_attempt=now+timedelta(minutes=[1,5,15][n.attempts-1])
            n.lease_until=None; n.lease_token=None; n.save()
        count+=1
    return count
