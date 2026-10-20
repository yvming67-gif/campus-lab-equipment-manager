"""独立测试库中的 HTTP 性能与状态同步验证，不写入演示库。"""
import json,os,secrets,subprocess,time,urllib.request,urllib.parse,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from django.test import TransactionTestCase,Client
from django.core.management import call_command
from django.db import connection
from django.conf import settings
from django.utils import timezone
from .models import *
from . import services as s
class HttpBenchmark(TransactionTestCase):
    def test_course_load(self):
        call_command("seed_demo",verbosity=0);now=timezone.now();lab=Lab.objects.first();role=Role.objects.get(code="STUDENT")
        users=[User(username=f"load{i}",school_no=f"LOAD-{i}",display_name=f"模拟负载{i}",password="!") for i in range(50)];User.objects.bulk_create(users);UserRole.objects.bulk_create([UserRole(user=u,role=role) for u in users])
        resources=[Resource(code=f"LOAD-EQ-{i:05}",name=f"模拟设备{i}",kind="EQUIPMENT",lab=lab) for i in range(10000)];Resource.objects.bulk_create(resources,batch_size=1000)
        EquipmentDetail.objects.bulk_create([EquipmentDetail(resource=r,category="性能样本") for r in resources],batch_size=1000)
        history=[];past=now-timedelta(days=100)
        for i in range(100000):history.append(Reservation(applicant=users[i%50],resource=resources[i%10000],starts_at=past,ends_at=past+timedelta(hours=1),purpose="性能历史模拟",status="REJECTED",approval_mode="MANUAL"))
        Reservation.objects.bulk_create(history,batch_size=1000);del history
        cookies=[]
        for u in users:
            c=Client();c.force_login(u);cookies.append("sessionid="+c.cookies["sessionid"].value+"; csrftoken="+("A"*32))
        env=os.environ.copy();db=connection.settings_dict;env.update({"PGDATABASE":db["NAME"],"PGHOST":db["HOST"],"PGPORT":str(db["PORT"]),"PGUSER":db["USER"],"PGPASSWORD":db["PASSWORD"],"DEBUG":"1","CAMPUS_USE_EXTERNAL_DATABASE":"1","CAMPUS_LOCAL_DIR":str(settings.LOCAL)})
        import sys,socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0));port=probe.getsockname()[1]
        log=(settings.LOCAL/"benchmark-web.log").open("wb");proc=subprocess.Popen([sys.executable,str(settings.BASE_DIR/"manage.py"),"runserver",f"127.0.0.1:{port}","--noreload"],env=env,cwd=settings.BASE_DIR,stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        def request(path,who=0,data=None):
            headers={"Cookie":cookies[who],"X-CSRFToken":"A"*32};blob=urllib.parse.urlencode(data).encode() if data else None
            req=urllib.request.Request(f"http://127.0.0.1:{port}"+path,data=blob,headers=headers);start=time.perf_counter()
            with urllib.request.urlopen(req,timeout=10) as response:body=response.read()
            return time.perf_counter()-start,body.decode("utf-8")
        try:
            for _ in range(100):
                try:request("/",0);break
                except Exception:
                    if proc.poll() is not None:raise RuntimeError("Benchmark server stopped")
                    time.sleep(.1)
            else:raise RuntimeError("Benchmark server not ready")
            start=timezone.localtime(now+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0);end=start+timedelta(hours=1)
            def sample(i):
                if i<200:return ("query",*request("/resources/?"+urllib.parse.urlencode({"q":resources[i%100].code,"starts_at":start.strftime("%Y-%m-%dT%H:%M"),"ends_at":end.strftime("%Y-%m-%dT%H:%M")}),i%50))
                j=i-200
                return ("submit",*request(f"/resources/{resources[j].pk}/",j%50,{"starts_at":start.strftime("%Y-%m-%dT%H:%M"),"ends_at":end.strftime("%Y-%m-%dT%H:%M"),"purpose":"HTTP负载模拟","participants":1,"request_key":str(uuid.uuid4())}))
            futures=[];begin=time.monotonic()
            with ThreadPoolExecutor(max_workers=50) as pool:
                for i in range(300):
                    delay=begin+i*.1-time.monotonic()
                    if delay>0:time.sleep(delay)
                    futures.append(pool.submit(sample,i))
                samples=[f.result() for f in futures]
            query=[x[1] for x in samples if x[0]=="query"];submit=[x[1] for x in samples if x[0]=="submit"]
            for kind,elapsed,body in samples:
                self.assertIn("资源目录" if kind=="query" else "预约详情",body)
                if kind=="submit":self.assertIn("已确认",body)
                self.assertLessEqual(elapsed,3 if kind=="query" else 5)
            # Broader workload: existing precision-search sample is retained unchanged.
            from django.contrib.sessions.backends.db import SessionStore
            mini=SessionStore();mini['channel']='campus-mini-v1';mini['mini_user_id']=users[0].pk;mini.set_expiry(7200);mini.create()
            for username in ['admin','director']:
                c=Client();c.force_login(User.objects.get(username=username));cookies.append('sessionid='+c.cookies['sessionid'].value)
            paths=[('directory','/resources/',0),('times','/resources/?'+urllib.parse.urlencode({'starts_at':start.strftime('%Y-%m-%dT%H:%M'),'ends_at':end.strftime('%Y-%m-%dT%H:%M')}),0),('pending','/bookings/?status=PENDING',50),('report','/reports/',51)]
            def mixed_sample(i):
                if i%5==4:
                    url='/api/mini/v1/resources/' if i%2 else '/api/mini/v1/bookings/'
                    req=urllib.request.Request(f'http://127.0.0.1:{port}'+url,headers={'Authorization':'Bearer '+mini.session_key})
                    tick=time.perf_counter()
                    with urllib.request.urlopen(req,timeout=10) as response:body=json.loads(response.read())
                    self.assertTrue(body['ok']);self.assertIsInstance(body['data']['items'],list)
                    return ('mini',time.perf_counter()-tick)
                kind,path,who=paths[i%5];elapsed,body=request(path,who)
                self.assertIn({'directory':'资源目录','times':'资源目录','pending':'预约与使用记录','report':'利用与运行报表'}[kind],body)
                return kind,elapsed
            mixed=[];begin=time.monotonic()
            with ThreadPoolExecutor(max_workers=50) as pool:
                futures=[]
                for i in range(200):
                    delay=begin+i*.1-time.monotonic()
                    if delay>0:time.sleep(delay)
                    futures.append(pool.submit(mixed_sample,i))
                mixed=[f.result() for f in futures]
            distributions={}
            for kind in ['directory','times','pending','report','mini']:
                values=sorted(t for name,t in mixed if name==kind)
                p95=values[max(0,__import__('math').ceil(len(values)*.95)-1)]
                self.assertLessEqual(p95,3,kind)
                distributions[kind]={'samples':len(values),'p50':round(values[len(values)//2],4),'p95':round(p95,4),'max':round(max(values),4),'errors':0}
            sync=[]
            for b in Reservation.objects.filter(resource__code__startswith="LOAD-EQ-",status="CONFIRMED"):
                tick=time.perf_counter();s.cancel(b.applicant,b.pk,"状态同步模拟")
                elapsed,body=request("/resources/?"+urllib.parse.urlencode({"q":b.resource.code,"starts_at":start.strftime("%Y-%m-%dT%H:%M"),"ends_at":end.strftime("%Y-%m-%dT%H:%M")}),0)
                self.assertIn("时段可预约",body);sync.append(time.perf_counter()-tick)
            self.assertEqual(len(sync),100);self.assertLessEqual(max(sync),2)
            maintenance_sync=[];admin=User.objects.get(username="admin");tech=User.objects.get(username="tech");r=Resource.objects.get(code="EQ-OSC-02")
            for number in range(50):
                incident=s.report_incident(admin,r.pk,"FAULT",f"维护同步模拟{number}");s.verify_incident(admin,incident.pk,"CONFIRM","已核实")
                tick=time.perf_counter();work=s.create_work_order(admin,incident.pk,tech.pk,"安排维修")
                _,body=request("/resources/?"+urllib.parse.urlencode({"q":r.code,"starts_at":start.strftime("%Y-%m-%dT%H:%M"),"ends_at":end.strftime("%Y-%m-%dT%H:%M")}),0);self.assertIn("维护中",body);maintenance_sync.append(time.perf_counter()-tick)
                s.update_work(tech,work.pk,"START","检查");s.update_work(tech,work.pk,"SUBMIT","修复")
                tick=time.perf_counter();s.accept_work(admin,work.pk,True,"复测通过")
                _,body=request("/resources/?"+urllib.parse.urlencode({"q":r.code,"starts_at":start.strftime("%Y-%m-%dT%H:%M"),"ends_at":end.strftime("%Y-%m-%dT%H:%M")}),0);self.assertIn("时段可预约",body);maintenance_sync.append(time.perf_counter()-tick)
            self.assertEqual(len(maintenance_sync),100);self.assertLessEqual(max(maintenance_sync),2)
            result={"mixed":distributions,"mixed_arrival_rate_requests_second":10,"mixed_samples":200,"mixed_error_rate":0,"equipment_count":10008,"history_count":100000,"sessions":50,"arrival_rate_requests_second":10,"query_samples":len(query),"submit_samples":len(submit),"query_max_seconds":round(max(query),4),"submit_max_seconds":round(max(submit),4),"sync_samples":len(sync),"sync_max_seconds":round(max(sync),4),"maintenance_sync_samples":len(maintenance_sync),"maintenance_sync_max_seconds":round(max(maintenance_sync),4),"scope":"真实本机HTTP与PostgreSQL，模拟数据；非真实校园网络与真实用户调研"}
            (settings.LOCAL/"benchmark-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");print("BENCHMARK "+json.dumps(result,ensure_ascii=True))
        finally:
            proc.terminate();proc.wait(timeout=10);log.close()
