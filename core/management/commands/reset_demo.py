"""Archive simulated active cases through business services; never delete history."""
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone
from core.models import *
from core import services as s

class Command(BaseCommand):
    help='仅收尾现有模拟案例，保留全部历史；随后可 seed_demo --refresh-cases 生成新案例'
    @transaction.atomic
    def handle(self,*args,**opts):
        BusinessConfig.objects.select_for_update().get(pk=1)
        manager=User.objects.filter(username='demo_labadmin',demo=True,role='manager',is_active=True).first()
        if not manager:raise CommandError('请先初始化模拟管理员')
        now=timezone.now();count=0
        for e in Equipment.objects.filter(demo=True):
            if not manager.managed_labs.filter(pk=e.laboratory_id).exists():continue
            # A real user's history on a demo-labelled device is still protected.
            if e.reservations.filter(demo=False).exists() or e.reservations.filter(applicant__demo=False).exists():continue
            if e.incidents.filter(reporter__demo=False).exists() or e.work_orders.filter(assignee__demo=False).exists():continue
            for loan in Loan.objects.filter(equipment=e,closed_at__isnull=True,borrower__demo=True):
                if loan.issued_at>now:continue
                s.receive(manager,loan.pk,'normal','模拟案例收尾：正常接收演示设备',now=now);count+=1
            for r in e.reservations.filter(demo=True,status__in=['pending','approved'],start__gt=now):
                s.cancel(r.applicant,r.pk,'模拟案例重置：保留历史',now=now);count+=1
            for w in e.work_orders.exclude(status='closed'):
                if not w.assignee.demo or not w.assignee.is_active:continue
                if w.status in ['open','progress']:s.progress_work(w.assignee,w.pk,True,'模拟重置：维修处理完成',now=now)
                s.accept_work(manager,w.pk,True,'模拟重置：管理员验收',now=now);count+=1
            for i in e.incidents.exclude(status='resolved'):
                if not i.work_orders.exclude(status='closed').exists():s.confirm_incident(manager,i.pk,'resolve','模拟重置：异常核验解决',now=now)
        self.stdout.write(f'已收尾 {count} 条模拟业务，全部历史保留；未删除任何数据。')
