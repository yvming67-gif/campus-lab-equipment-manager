import json,subprocess,sys,time,logging,os
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
            try:
                result=run_jobs()
                if opts['loop']:logging.getLogger('campus.jobs').info('scan counts=%s',result)
                else:self.stdout.write(str(result));self.stdout.flush()
            except Exception as error:
                logging.getLogger('campus.jobs').error('scan_failed code=%s',type(error).__name__)
                from core.models import JobHeartbeat
                try:JobHeartbeat.objects.update_or_create(name='business-jobs',defaults={'last_error_at':timezone.now(),'error_code':type(error).__name__})
                except Exception:pass
                if not opts['loop']:raise
            now=timezone.now()
            if backup is not None and backup.poll() is not None:
                if backup.returncode!=0:
                    retry_after=now+timedelta(hours=1)
                    logging.getLogger('campus.jobs').error('backup_failed exit=%s',backup.returncode)
                if log:log.close()
                backup=None
            last=None
            if marker.exists():
                try:last=timezone.datetime.fromisoformat(json.loads(marker.read_text("utf-8"))["last_success"])
                except (ValueError,KeyError):pass
            if opts["loop"] and (settings.LOCAL/"runtime.json").exists() and backup is None and (not retry_after or now>=retry_after) and (not last or now-last>=timedelta(hours=23)):
                from core.operations import rotate_log
                rotate_log(settings.LOCAL/"backup-job.log")
                backup_env=os.environ.copy()
                if not settings.DEMO_MODE:
                    if not backup_env.get('CAMPUS_BACKUP_USER') or not backup_env.get('CAMPUS_BACKUP_PASSWORD'):
                        logging.getLogger('campus.jobs').error('backup_identity_not_configured')
                        retry_after=now+timedelta(hours=1)
                        time.sleep(30);continue
                    backup_env['PGUSER']=backup_env['CAMPUS_BACKUP_USER'];backup_env['PGPASSWORD']=backup_env['CAMPUS_BACKUP_PASSWORD']
                log=(settings.LOCAL/"backup-job.log").open("ab");backup=subprocess.Popen([sys.executable,str(settings.BASE_DIR/"tools/backup_restore.py")],env=backup_env,stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if not opts["loop"]:break
            time.sleep(30)
