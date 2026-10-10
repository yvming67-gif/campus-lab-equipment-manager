import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from core.models import Equipment,Reservation,Loan,ReturnRecord,Incident,WorkOrder,Audit

class Command(BaseCommand):
    help='保存/核对业务数据摘要，用于真实服务和数据库重启验收'
    def add_arguments(self,p):p.add_argument('phase',choices=['before','after'])
    def handle(self,*args,**o):
        result={}
        for model in [Equipment,Reservation,Loan,ReturnRecord,Incident,WorkOrder,Audit]:
            rows=list(model.objects.order_by('pk').values())
            result[model.__name__]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,cls=DjangoJSONEncoder).encode()).hexdigest()}
        local=Path(settings.BASE_DIR)/'.local'/'persistence-before.json'
        if o['phase']=='before':local.write_text(json.dumps(result,indent=2),encoding='utf-8');self.stdout.write('已保存重启前业务数据摘要')
        else:
            expected=json.loads(local.read_text(encoding='utf-8'))
            if result!=expected:raise CommandError('业务数据在重启前后不同，请检查后台任务或其他变更')
            artifact=Path(settings.BASE_DIR)/'artifacts'/'persistence.json'
            artifact.write_text(json.dumps({'verified_at':timezone.now().isoformat(),'result':'PASS','models':result},indent=2),encoding='utf-8')
            self.stdout.write('重启后七类业务对象数量和 SHA256 数据摘要一致')
