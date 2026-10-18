import json,subprocess,sys,time
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.services import run_jobs
class Command(BaseCommand):
    help="处理过期、提醒、投递；本地循环运行时每23小时触发一次完整备份"
    def add_arguments(self,parser):parser.add_argument("--loop",action="store_true")
    def handle(self,*args,**opts):
        backup=None;log=None;retry_after=None;marker=settings.LOCAL/"last-backup.json"
        while True:
            result=run_jobs();self.stdout.write(str(result));self.stdout.flush();now=timezone.now()
            if backup is not None and backup.poll() is not None:
                if backup.returncode==0:marker.write_text(json.dumps({"last_success":now.isoformat()}),encoding="utf-8")
                else:retry_after=now+timedelta(hours=1)
                if log:log.close()
                backup=None
            last=None
            if marker.exists():
                try:last=timezone.datetime.fromisoformat(json.loads(marker.read_text("utf-8"))["last_success"])
                except (ValueError,KeyError):pass
            if opts["loop"] and (settings.LOCAL/"runtime.json").exists() and backup is None and (not retry_after or now>=retry_after) and (not last or now-last>=timedelta(hours=23)):
                log=(settings.LOCAL/"backup-job.log").open("ab");backup=subprocess.Popen([sys.executable,str(settings.BASE_DIR/"tools/backup_restore.py")],stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if not opts["loop"]:break
            time.sleep(30)
