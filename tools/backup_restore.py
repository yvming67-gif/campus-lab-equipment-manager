"""一致性备份及隔离恢复演练：不覆盖开发数据库，不备份有效会话。"""
import argparse,hashlib,json,os,secrets,subprocess,sys,tempfile,time,zipfile
from pathlib import Path
from windows_paths import ascii_alias
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django
django.setup()
from django.conf import settings
from django.db import connection,transaction
from core.models import Evidence
LOCAL=settings.LOCAL
def call(args,env):subprocess.run([str(x) for x in args],env=env,check=True,capture_output=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--restore-test",action="store_true");parser.add_argument("--replica",default=os.environ.get("CAMPUS_BACKUP_REPLICA"));args=parser.parse_args();start=time.perf_counter()
    runtime=json.loads((LOCAL/"runtime.json").read_text("utf-8"));db=settings.DATABASES["default"];pg=Path(runtime["pg_bin"]);env=os.environ.copy();env.update({"PGHOST":db["HOST"],"PGPORT":str(db["PORT"]),"PGUSER":db["USER"],"PGPASSWORD":db["PASSWORD"]})
    folder=LOCAL/"backups";folder.mkdir(exist_ok=True);stamp=time.strftime("%Y%m%d-%H%M%S")+"-"+secrets.token_hex(3);target=folder/("backup-"+stamp+".zip")
    with tempfile.TemporaryDirectory(dir=LOCAL) as temp:
        temp=Path(temp)
        native_temp=ascii_alias(LOCAL)/temp.name if os.name=="nt" else temp
        dump=native_temp/"database.dump"
        with transaction.atomic():
            with connection.cursor() as c:c.execute("LOCK TABLE core_evidence IN SHARE MODE")
            attachments=list(Evidence.objects.values("id","file","sha256"))
            call([pg/"pg_dump.exe","-Fc","--no-owner","--exclude-table-data=django_session","-d",db["NAME"],"-f",dump],env)
            manifest={"database_sha256":hashlib.sha256(dump.read_bytes()).hexdigest(),"attachments":attachments,"session_data":"excluded","created":stamp}
            with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
                z.write(dump,"database.dump")
                for item in attachments:
                    file=settings.MEDIA_ROOT/item["file"]
                    if hashlib.sha256(file.read_bytes()).hexdigest()!=item["sha256"]:raise RuntimeError("Attachment checksum mismatch")
                    z.write(file,"media/"+item["file"])
                z.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        outcome={"archive":str(target),"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"attachments":len(attachments)}
        if args.restore_test:
            restore_name="campus_restore_"+stamp.replace("-","");call([pg/"createdb.exe",restore_name],env)
            call([pg/"pg_restore.exe","--single-transaction","--exit-on-error","--no-owner","-d",restore_name,dump],env)
            import psycopg
            with psycopg.connect(host=db["HOST"],port=db["PORT"],user=db["USER"],password=db["PASSWORD"],dbname=restore_name) as restored:
                with connection.cursor() as source,restored.cursor() as dest:
                    source.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'django_session'");tables=[row[0] for row in source.fetchall()];counts={}
                    for table in tables:
                        source.execute('SELECT COUNT(*) FROM "'+table+'"');a=source.fetchone()[0];dest.execute('SELECT COUNT(*) FROM "'+table+'"');b=dest.fetchone()[0]
                        if a!=b:raise RuntimeError("Restored table count differs: "+table)
                        counts[table]=b
                    dest.execute("SELECT COUNT(*) FROM django_session");assert dest.fetchone()[0]==0
                    dest.execute("SELECT file,sha256 FROM core_evidence")
                    with zipfile.ZipFile(target) as z:
                        for file,digest in dest.fetchall():assert hashlib.sha256(z.read("media/"+file)).hexdigest()==digest
            outcome.update({"restore_database":restore_name,"table_counts":counts,"verified":True})
        if args.replica:
            import shutil
            from restore_archive import inspect_archive
            destination=Path(args.replica).resolve()
            if destination==folder.resolve() or destination.is_relative_to(folder.resolve()) or folder.resolve().is_relative_to(destination):raise ValueError("副本必须位于独立目录")
            destination.mkdir(parents=True,exist_ok=True);replica=destination/target.name
            if replica.exists():raise ValueError("副本已存在，不覆盖")
            shutil.copy2(target,replica);inspect_archive(replica)
            if hashlib.sha256(replica.read_bytes()).hexdigest()!=outcome["sha256"]:raise RuntimeError("副本摘要不符")
            outcome["replica_verified"]=True
        from django.utils import timezone
        (LOCAL/'last-backup.json').write_text(json.dumps({'last_success':timezone.now().isoformat(),'replica_verified':outcome.get('replica_verified',False)}),'utf-8')
        outcome["elapsed_seconds"]=round(time.perf_counter()-start,3);(folder/("result-"+stamp+".json")).write_text(json.dumps(outcome,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({k:v for k,v in outcome.items() if k not in ["table_counts"]},ensure_ascii=True))
if __name__=="__main__":main()
