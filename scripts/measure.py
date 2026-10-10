"""Real HTTP course-load measurements; writes only explicitly labelled demo data."""
import json
import os
import secrets
import time
import urllib.request
import urllib.parse
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.utils import timezone
from core.models import *
base='http://127.0.0.1:8000'
accounts=json.loads((Path('.local')/'demo-accounts.json').read_text(encoding='utf-8'))
password=accounts['demo_student1']['password']
def session(username,password):
    cookies=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    opener.open(base+'/login/').read()
    token=next(c.value for c in cookies if c.name=='csrftoken')
    opener.open(base+'/login/',urllib.parse.urlencode({'username':username,'password':password,'csrfmiddlewaretoken':token}).encode()).read()
    return opener,cookies
def percentile(values,p):return round(sorted(values)[max(0,__import__('math').ceil(len(values)*p)-1)]*1000,2)
def query(_):
    opener,_=session('demo_student1',password)
    times=[]
    for path in ['/equipment/','/equipment/?page=2','/labs/','/reservations/','/notifications/']*3:
        started=time.perf_counter();response=opener.open(base+path);response.read()
        if response.status!=200:raise RuntimeError('查询失败')
        times.append(time.perf_counter()-started)
    return times
with ThreadPoolExecutor(max_workers=8) as pool:queries=sum(list(pool.map(query,range(8))),[])
users=[];results=[]
for i in range(16):
    username='demo_load_'+secrets.token_hex(6);pwd=secrets.token_urlsafe(20)
    u=User.objects.create_user(username,password=pwd,role='student',demo=True);users.append((u,pwd))
equipment=Equipment.objects.filter(demo=True,service='normal',qualification='').first()
start=(timezone.localtime()+timedelta(days=7)).replace(hour=10,minute=0,second=0,microsecond=0)
def post(item):
    user,pwd=item;opener,cookies=session(user.username,pwd)
    opener.open(base+f'/act/reserve/{equipment.pk}/').read()
    token=next(c.value for c in cookies if c.name=='csrftoken')
    import uuid
    data={'key':str(uuid.uuid4()),'csrfmiddlewaretoken':token,'start':start.strftime('%Y-%m-%dT%H:%M'),
          'end':(start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),'purpose':'模拟负载验收'}
    began=time.perf_counter()
    response=opener.open(base+f'/act/reserve/{equipment.pk}/',urllib.parse.urlencode(data).encode());response.read()
    elapsed=time.perf_counter()-began
    if '/reservations/' not in response.url:raise RuntimeError('提交未成功')
    visible=time.perf_counter();body=opener.open(response.url).read().decode()
    if '待审核' not in body:raise RuntimeError('重新查询未体现成功状态')
    visibility=time.perf_counter()-visible
    return elapsed,visibility
with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(post,users))
# Retain history; cancel only this script's newly created, labelled reservations.
from core.services import cancel
for user,_ in users:
    for r in Reservation.objects.filter(applicant=user,purpose='模拟负载验收',status='pending'):
        cancel(user,r.pk,'负载验证结束，仅取消本工具新建模拟预约')
    user.is_active=False;user.save(update_fields=['is_active'])
report={'at':timezone.now().isoformat(),'environment':{'python':sys.version,'database':'PostgreSQL 17.11','server':'Django runserver --noreload, Windows loopback'},
    'data':{'equipment':Equipment.objects.count(),'laboratories':Laboratory.objects.count(),'reservations':Reservation.objects.count(),'users':User.objects.count()},
    'query':{'concurrent_users':8,'requests':len(queries),'p95_ms':percentile(queries,.95),'max_ms':round(max(queries)*1000,2)},
    'submit':{'concurrent_users':4,'requests':len(results),'p95_ms':percentile([x[0] for x in results],.95)},
    'visibility':{'max_ms':round(max(x[1] for x in results)*1000,2)}}
report['pass']=report['query']['p95_ms']<=3000 and report['submit']['p95_ms']<=5000 and report['visibility']['max_ms']<=5000
Path('artifacts').mkdir(exist_ok=True)
Path('artifacts/performance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
if not report['pass']:raise SystemExit(1)
