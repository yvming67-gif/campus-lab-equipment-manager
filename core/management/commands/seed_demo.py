import json
import secrets
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from core.models import *
from core import services as s

class Command(BaseCommand):
    help='幂等创建模拟数据；--refresh-cases 仅追加新的相对当前时间案例，不删除历史或非模拟数据'
    def add_arguments(self,p): p.add_argument('--refresh-cases',action='store_true')
    @transaction.atomic
    def handle(self,*args,**opts):
        BusinessConfig.objects.get_or_create(pk=1)
        BusinessConfig.objects.select_for_update().get(pk=1)
        account_file=Path(settings.BASE_DIR)/'.local'/'demo-accounts.json'
        account_file.parent.mkdir(exist_ok=True)
        credentials=json.loads(account_file.read_text(encoding='utf-8')) if account_file.exists() else {}
        users={}
        for name,role in [('student1','student'),('student2','student'),('student3','student'),('teacher1','teacher'),
                          ('labadmin','manager'),('labadmin2','manager'),('repair1','technician'),('sysadmin','system')]:
            username='demo_'+name
            u=User.objects.filter(username=username).first()
            if u and not u.demo: raise CommandError(f'账号 {username} 已存在且不属于模拟数据，停止初始化')
            if not u:
                password=secrets.token_urlsafe(16)
                u=User.objects.create_user(username,password=password,role=role,demo=True)
                credentials[username]={'password':password,'role':u.get_role_display()}
            elif username not in credentials:
                self.stderr.write(f'{username} 已存在但本地密码文件缺失；保留密码，可用 changepassword 重置')
            users[name]=u
        labs=[]
        for i,(name,location) in enumerate([('电子测量实验室','教学楼 A201'),('智能机器人实验室','实训楼 B302'),('数字媒体实验室','创新楼 C105')],1):
            code=f'DEMO-LAB-{i:02}'
            lab=Laboratory.objects.filter(code=code).first()
            if lab and not lab.demo: raise CommandError('非模拟实验室编号冲突，停止初始化')
            if not lab: lab=Laboratory.objects.create(code=code,name=name,location=location,demo=True)
            labs.append(lab)
        users['labadmin'].managed_labs.set(labs)
        users['labadmin2'].managed_labs.set(labs)
        now=timezone.now()
        for name in ['student1','teacher1']:
            for code in ['robot','safety','camera']:
                Qualification.objects.get_or_create(user=users[name],code=code,defaults={'expires_at':now+timedelta(days=365)})
        devices=[]
        types=[('数字示波器','示波器'),('环境传感器套件','传感器套件'),('移动机器人平台','机器人平台'),('数码摄影设备','摄影设备'),('便携式计算机','便携式计算机')]
        for i in range(15):
            tag=f'DEMO-EQ-{i+1:03}'
            e=Equipment.objects.filter(tag=tag).first()
            if e and not e.demo: raise CommandError('非模拟设备标签冲突，停止初始化')
            if not e:
                title,category=types[i%5]
                code='robot' if i%5==2 else ('camera' if i%5==3 else '')
                e=Equipment.objects.create(tag=tag,name=f'{title} {i//5+1:02}',category=category,laboratory=labs[i//5],
                    qualification=code,high_value=i%5 in [2,3],dangerous=i%5==2,demo=True,
                    description='模拟设备，仅供系统分析与设计课程演示。')
                for accessory in ['电源适配器','连接线','收纳箱']: Accessory.objects.create(equipment=e,name=accessory)
            devices.append(e)
        first=not Reservation.objects.filter(demo=True).exists()
        manager=users['labadmin']
        if first or opts['refresh_cases']:
            # Only use free demo devices. Historical examples call the same service rules with injected clocks.
            if opts['refresh_cases']:
                self.stdout.write('保留全部既有数据，仅为可用模拟设备追加案例。')
            tomorrow=(timezone.localtime(now)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
            for index,kind in [(0,'pending'),(1,'approved'),(4,'cancelled')]:
                e=devices[index]
                if Reservation.objects.filter(equipment=e,status__in=['pending','approved','borrowed']).exists() or e.service!='normal': continue
                r=s.reserve(users['student2'],e.pk,tomorrow+timedelta(hours=index),tomorrow+timedelta(hours=index+1),'模拟预约案例',now=now)
                if kind=='approved': s.review(manager,r.pk,True,'模拟人工审核通过',now=now)
                if kind=='cancelled': s.cancel(users['student2'],r.pk,'课程计划调整',now=now)
            base=timezone.localtime(now).replace(minute=0 if now.minute<30 else 30,second=0,microsecond=0)
            for index,kind in [(5,'out'),(6,'overdue'),(9,'damage'),(10,'missing'),(11,'acceptance')]:
                e=devices[index]
                if e.service!='normal' or Loan.objects.filter(equipment=e,closed_at__isnull=True).exists(): continue
                start=base-timedelta(hours=3 if kind=='overdue' else 1)
                applicant=users['student3'] if kind=='overdue' else users['teacher1']
                # Separate windows for the same applicant in historical returned cases.
                if kind in ['damage','missing','acceptance']: start=base-timedelta(days=index-7)
                end=start+timedelta(hours=2)
                try:
                    r=s.reserve(applicant,e.pk,start,end,'模拟借还案例',now=start-timedelta(hours=1))
                    s.review(manager,r.pk,True,'模拟人工审核',now=start-timedelta(minutes=30))
                    loan=s.issue(manager,r.pk,applicant.username,e.tag,True,'外观正常，功能与配件齐全',now=start+timedelta(minutes=5))
                    if kind in ['damage','missing','acceptance']:
                        s.receive(manager,loan.pk,'missing' if kind=='missing' else 'damage','模拟损坏 / 缺件，已完成实物接收',now=end-timedelta(minutes=5))
                        incident=Incident.objects.get(loan=loan)
                        w=s.create_work_order(manager,incident.pk,users['repair1'].pk,'模拟维修任务',now=end)
                        if kind=='acceptance': s.progress_work(users['repair1'],w.pk,True,'完成修复及功能复测，等待验收',now=end+timedelta(hours=1))
                except s.BusinessError as exc:
                    self.stderr.write(f'跳过案例 {e.tag}：{exc}')
            if devices[13].service=='normal': s.set_service(devices[13],'disabled',manager,'模拟停用设备',now)
            if devices[14].service=='normal': s.set_service(devices[14],'quarantine',manager,'模拟异常隔离设备',now)
        account_file.write_text(json.dumps(credentials,ensure_ascii=False,indent=2),encoding='utf-8')
        self.stdout.write(f'初始化完成：{len(labs)} 个实验室，{len(devices)} 台设备。账号信息仅在 .local/demo-accounts.json。')
