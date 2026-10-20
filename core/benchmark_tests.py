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
            result={"equipment_count":10008,"history_count":100000,"sessions":50,"arrival_rate_requests_second":10,"query_samples":len(query),"submit_samples":len(submit),"query_max_seconds":round(max(query),4),"submit_max_seconds":round(max(submit),4),"sync_samples":len(sync),"sync_max_seconds":round(max(sync),4),"maintenance_sync_samples":len(maintenance_sync),"maintenance_sync_max_seconds":round(max(maintenance_sync),4),"scope":"真实本机HTTP与PostgreSQL，模拟数据；非真实校园网络与真实用户调研"}
            (settings.LOCAL/"benchmark-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");print("BENCHMARK "+json.dumps(result,ensure_ascii=True))
        finally:
            proc.terminate();proc.wait(timeout=10);log.close()
