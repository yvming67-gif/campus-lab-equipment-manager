import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from django.test import TransactionTestCase
from django.db import connections,connection
from core.models import *
from core import services as s
from .test_business import Fixture

class ConcurrencyTests(Fixture,TransactionTestCase):
    def setUp(self): self.setup_data()
    def race(self,functions):
        self.assertEqual(connection.vendor,'postgresql')
        barrier=threading.Barrier(len(functions));pids=[];lock=threading.Lock()
        def run(fn):
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()');pid=cursor.fetchone()[0]
                with lock: pids.append(pid)
                barrier.wait(timeout=10)
                try: return ('ok',fn())
                except s.BusinessError as exc: return ('blocked',str(exc))
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool: result=list(pool.map(run,functions))
        self.assertEqual(len(set(pids)),len(functions),'必须使用独立 PostgreSQL 连接')
        return result
    def test_actual_parallel_approval_one_winner(self):
        a=self.reserve();b=self.reserve(self.other)
        result=self.race([lambda:s.review(self.manager,a.pk,True,'并发审核甲',now=self.now),
                          lambda:s.review(self.manager2,b.pk,True,'并发审核乙',now=self.now)])
        self.assertCountEqual([x[0] for x in result],['ok','blocked'])
        self.assertEqual(Reservation.objects.filter(status='approved').count(),1)
        self.assertEqual(Review.objects.count(),1)
    def test_parallel_personal_quota(self):
        for i in range(2): self.reserve(start=self.now+timedelta(hours=1+i*2),end=self.now+timedelta(hours=2+i*2))
        result=self.race([lambda:self.reserve(start=self.now+timedelta(hours=5),end=self.now+timedelta(hours=6)),
                          lambda:self.reserve(e=self.e2,start=self.now+timedelta(hours=7),end=self.now+timedelta(hours=8))])
        self.assertCountEqual([x[0] for x in result],['ok','blocked'])
        self.assertEqual(Reservation.objects.filter(applicant=self.student).count(),3)
    def test_parallel_idempotent_submit(self):
        result=self.race([lambda:self.reserve(key='same-request'),lambda:self.reserve(key='same-request')])
        self.assertEqual([x[0] for x in result],['ok','ok'])
        self.assertEqual(result[0][1].pk,result[1][1].pk)
        self.assertEqual(Reservation.objects.count(),1)
    def test_parallel_maintenance_and_approval(self):
        r=self.reserve()
        result=self.race([lambda:s.review(self.manager,r.pk,True,'审核',now=self.now),
            lambda:s.maintain_equipment(self.manager2,self.e.pk,self.e.name,self.e.category,'','',False,False,'disabled','维护',now=self.now)])
        r.refresh_from_db();self.e.refresh_from_db()
        self.assertEqual(r.status,'cancelled');self.assertEqual(self.e.service,'disabled')
        self.assertFalse(Reservation.objects.filter(status='approved').exists())
