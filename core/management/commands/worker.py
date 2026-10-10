import time
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import close_old_connections
from core.jobs import schedule, deliver

class Command(BaseCommand):
    help='运行数据库后台任务；--once 单次执行；--at ISO时间 可控演示时间'
    def add_arguments(self,p):
        p.add_argument('--once',action='store_true')
        p.add_argument('--at')
        p.add_argument('--mode',choices=['success','fail'])
        p.add_argument('--interval',type=int,default=15)
    def handle(self,*a,**o):
        fixed=parse_datetime(o['at']) if o['at'] else None
        if o['at'] and (not fixed or timezone.is_naive(fixed) or not o['once']):
            raise CommandError('--at 须为带时区 ISO 时间，并与 --once 一起使用')
        while True:
            try:
                now=fixed or timezone.now()
                expired=schedule(now); sent=deliver(now,mode=o['mode'])
                if o['once'] or expired or sent: self.stdout.write(f'失效 {expired} 条；发送尝试 {sent} 次')
            except KeyboardInterrupt: return
            except Exception as exc:
                if o['once']: raise
                self.stderr.write(f'后台任务失败，将在下轮恢复：{type(exc).__name__}')
            finally: close_old_connections()
            if o['once']: break
            time.sleep(max(1,o['interval']))
