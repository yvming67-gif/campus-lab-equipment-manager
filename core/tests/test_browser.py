"""Real Chromium UI acceptance via agent-browser, against isolated PostgreSQL.

Run separately: manage.py test core.tests.test_browser. No production clock endpoint.
"""
import json
import base64
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from core.models import *
from .test_business import Fixture

class BrowserAcceptance(Fixture,StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('agent-browser'): raise RuntimeError('浏览器验收需要安装 agent-browser 0.27.2 及 Chromium，见 README')
        super().setUpClass()
    def setUp(self):
        self.setup_data()
        self.now=(timezone.localtime()+timedelta(days=1)).replace(hour=9,minute=0,second=0,microsecond=0)
        self.password=secrets.token_urlsafe(16)
        for u in [self.student,self.other,self.manager,self.manager2,self.tech]:
            u.set_password(self.password);u.save()
        self.sessions=[];self.evidence=[]
        self.clock=patch('core.services.current_time',return_value=self.now)
        self.mock_clock=self.clock.start()
    def tearDown(self):
        self.clock.stop()
        out=Path(settings.BASE_DIR)/'artifacts'/'browser'
        out.mkdir(parents=True,exist_ok=True)
        (out/(self._testMethodName+'.json')).write_text(json.dumps(self.evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        for session in self.sessions:
            try:self.browser(session,'close')
            except Exception:pass
        super().tearDown()
    def browser(self,session,*args):
        executable=shutil.which('agent-browser')
        if os.name=='nt':
            native=Path(executable).parent/'node_modules'/'agent-browser'/'bin'/'agent-browser-win32-x64.exe'
            if native.exists(): executable=str(native)
        # Native CLI avoids Windows .cmd wrapper/daemon holding inherited pipes.
        import tempfile
        with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            result=subprocess.run([executable,'--session',session,*map(str,args)],
                stdout=stdout,stderr=stderr,timeout=45)
            stdout.seek(0);stderr.seek(0)
            result.stdout=stdout.read().decode('utf-8',errors='replace')
            result.stderr=stderr.read().decode('utf-8',errors='replace')
        if result.returncode: raise AssertionError(f'浏览器命令 {args[0]} 失败：{result.stderr}')
        if args[0]=='snapshot':self.evidence.append({'session':session,'snapshot':result.stdout})
        return result.stdout
    def login(self,user):
        session='campus-qa-'+secrets.token_hex(4);self.sessions.append(session)
        self.browser(session,'open',self.live_server_url+'/login/')
        self.browser(session,'snapshot','-i')
        self.browser(session,'fill','#id_username',user.username)
        self.browser(session,'fill','#id_password',self.password)
        self.browser(session,'click','button[type=submit]')
        self.browser(session,'wait','--url',self.live_server_url+'/')
        self.assertIn(user.get_role_display()+'工作台',self.browser(session,'snapshot','-i'))
        return session
    def submit(self,session):
        state=self.browser(session,'eval','document.querySelector(".business-form").checkValidity()')
        self.evidence.append({'form_valid':state})
        self.browser(session,'scrollintoview','.business-form button[type=submit]')
        self.browser(session,'snapshot','-i')
        self.browser(session,'find','role','button','click','--name','确认并保存')
        self.browser(session,'wait','--load','networkidle')
        self.browser(session,'snapshot')
        return self.browser(session,'get','url').strip()
    def create_reservation(self,session,equipment=None):
        e=equipment or self.e
        self.browser(session,'open',self.live_server_url+'/equipment/')
        self.browser(session,'snapshot','-i')
        self.browser(session,'find','text',e.tag,'click','--exact')
        self.browser(session,'snapshot','-i')
        self.browser(session,'find','text','提交预约','click','--exact')
        self.browser(session,'snapshot','-i')
        # CDP text insertion does not populate native datetime-local widgets on
        # Windows Chrome. Set native widget values in the browser and dispatch
        # normal input/change events; submit still uses the actual visible form.
        dates={'id_start':(self.now+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
               'id_end':(self.now+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')}
        code='Object.entries('+json.dumps(dates)+').forEach(([id,value])=>{const e=document.getElementById(id);e.value=value;e.dispatchEvent(new Event("input",{bubbles:true}));e.dispatchEvent(new Event("change",{bubbles:true}));});'
        self.browser(session,'eval','-b',base64.b64encode(code.encode()).decode())
        self.assertIn(dates['id_start'],self.browser(session,'get','value','#id_start'))
        self.browser(session,'fill','#id_purpose','浏览器真实页面验收')
        url=self.submit(session)
        self.assertRegex(url,r'/reservations/\d+/$')
        return Reservation.objects.get(pk=int(re.search(r'/reservations/(\d+)/',url).group(1)))
    def approve(self,session,r):
        self.browser(session,'open',f'{self.live_server_url}/reservations/{r.pk}/')
        self.browser(session,'snapshot','-i')
        self.browser(session,'find','text','审核预约','click','--exact')
        self.browser(session,'snapshot','-i')
        self.browser(session,'select','#id_approve','yes')
        self.browser(session,'fill','#id_reason','培训、时段与用途核验通过')
        self.submit(session)
        r.refresh_from_db();self.assertEqual(r.status,'approved')
    def issue(self,session,r):
        self.mock_clock.return_value=r.start
        self.browser(session,'open',f'{self.live_server_url}/reservations/{r.pk}/')
        self.browser(session,'snapshot','-i')
        self.browser(session,'find','text','办理借出','click','--exact')
        self.browser(session,'snapshot','-i')
        self.browser(session,'fill','#id_borrower_username',r.applicant.username)
        self.browser(session,'fill','#id_equipment_tag',r.equipment.tag)
        self.browser(session,'check','#id_accessories_checked')
        self.browser(session,'check','#id_normal_checked')
        self.browser(session,'fill','#id_inspection','核对身份与标签，配件齐全，外观及功能正常')
        url=self.submit(session);self.assertRegex(url,r'/loans/\d+/$')
        return Loan.objects.get(reservation=r)
    def receive(self,session,loan,condition):
        self.mock_clock.return_value=loan.due_at
        self.browser(session,'find','text','办理归还','click','--exact')
        self.browser(session,'snapshot','-i')
        self.browser(session,'select','#id_condition',condition)
        self.browser(session,'fill','#id_notes','配件齐全，正常归还' if condition=='normal' else '外壳损坏，已接收设备及配件')
        self.submit(session)
        loan.refresh_from_db();self.assertIsNotNone(loan.closed_at)
    def test_flow_1_query_reserve_approve_issue_normal_return(self):
        student=self.login(self.student);r=self.create_reservation(student)
        manager=self.login(self.manager);self.approve(manager,r)
        loan=self.issue(manager,r);self.receive(manager,loan,'normal')
        r.refresh_from_db();self.assertEqual(r.status,'completed')
        self.browser(student,'open',f'{self.live_server_url}/loans/{loan.pk}/')
        self.assertIn('正常',self.browser(student,'snapshot'))
        self.evidence.append({'verified':'预约、审核、借出、正常归还，学生重新查询可见','reservation_id':r.pk})
    def test_flow_2_abnormal_return_repair_accept_restore(self):
        student=self.login(self.student);r=self.create_reservation(student)
        manager=self.login(self.manager);self.approve(manager,r)
        loan=self.issue(manager,r);self.receive(manager,loan,'damage')
        i=Incident.objects.get(loan=loan)
        self.browser(manager,'open',f'{self.live_server_url}/incidents/{i.pk}/')
        self.browser(manager,'snapshot','-i')
        self.browser(manager,'find','text','创建维修工单','click','--exact')
        self.browser(manager,'snapshot','-i')
        self.browser(manager,'select','#id_assignee',self.tech.pk)
        self.browser(manager,'fill','#id_reason','修复外壳并执行功能测试')
        self.submit(manager);w=WorkOrder.objects.get(incident=i)
        tech=self.login(self.tech)
        self.browser(tech,'open',f'{self.live_server_url}/work/{w.pk}/')
        self.browser(tech,'snapshot','-i')
        self.browser(tech,'find','text','更新维修 / 提交验收','click','--exact')
        self.browser(tech,'snapshot','-i')
        self.browser(tech,'select','#id_submit','yes')
        self.browser(tech,'fill','#id_text','完成修复，功能测试通过')
        self.submit(tech)
        self.browser(manager,'open',f'{self.live_server_url}/work/{w.pk}/')
        self.browser(manager,'snapshot','-i')
        self.browser(manager,'find','text','管理员验收','click','--exact')
        self.browser(manager,'snapshot','-i')
        self.browser(manager,'select','#id_approved','yes')
        self.browser(manager,'fill','#id_reason','验收合格，检查无其他阻塞')
        self.submit(manager)
        self.e.refresh_from_db();self.assertEqual(self.e.service,'normal')
        self.assertTrue(ReturnRecord.objects.filter(loan=loan).exists())
        self.browser(student,'open',f'{self.live_server_url}/equipment/{self.e.pk}/')
        self.assertIn('正常',self.browser(student,'snapshot'))
        self.evidence.append({'verified':'异常归还、创建工单、维修提交、管理员验收、恢复正常，归还事实保留'})
    def test_flow_3_two_applicants_simultaneous_ui_approval(self):
        a_session=self.login(self.student);b_session=self.login(self.other)
        a=self.create_reservation(a_session);b=self.create_reservation(b_session)
        managers=[self.login(self.manager),self.login(self.manager2)]
        for session,r in zip(managers,[a,b]):
            self.browser(session,'open',f'{self.live_server_url}/act/review/{r.pk}/')
            self.browser(session,'snapshot','-i')
            self.browser(session,'select','#id_approve','yes')
            self.browser(session,'fill','#id_reason','竞争预约并发审核')
        # Two actual browser sessions submit independent HTTP requests concurrently.
        import threading
        barrier=threading.Barrier(2)
        def click(session):
            barrier.wait(timeout=10)
            return self.submit(session)
        with ThreadPoolExecutor(max_workers=2) as pool:urls=list(pool.map(click,managers))
        self.assertEqual(Reservation.objects.filter(status='approved').count(),1)
        self.assertEqual(Review.objects.count(),1)
        self.assertEqual(sum('/reservations/' in url for url in urls),1)
        self.assertEqual(sum('/act/review/' in url for url in urls),1)
        self.evidence.append({'verified':'两申请人同设备同时间；两个管理员浏览器同时提交，仅一个批准','urls':urls})
