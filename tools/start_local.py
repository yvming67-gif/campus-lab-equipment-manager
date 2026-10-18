"""Windows 本地课程演示启动器。数据库、密钥和依赖只写入本项目私有目录。"""
import argparse,hashlib,json,os,secrets,subprocess,sys,time,urllib.request,urllib.error,venv,webbrowser,zipfile
from pathlib import Path
from windows_paths import ascii_alias
ROOT=Path(__file__).resolve().parents[1];LOCAL=ROOT/".local";LOCAL.mkdir(exist_ok=True)
CREATE_NO_WINDOW=getattr(subprocess,"CREATE_NO_WINDOW",0)
def run(args,**kw):return subprocess.run([str(a) for a in args],check=True,creationflags=CREATE_NO_WINDOW,**kw)
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--setup",action="store_true");parser.add_argument("--no-browser",action="store_true");args=parser.parse_args()
    runtime=LOCAL/"runtime.json";cfgfile=LOCAL/"database.json"
    if not runtime.exists():
        if not args.setup:raise SystemExit("First run: python tools/start_local.py --setup")
        if os.name!="nt":raise SystemExit("Use an existing PostgreSQL server and follow README on non-Windows platforms.")
        venvpath=ROOT/".venv";venv.create(venvpath,with_pip=True);python=venvpath/"Scripts/python.exe";run([python,"-m","pip","install","-r",ROOT/"requirements.txt"])
        archive=LOCAL/"postgres-17.11.zip"
        if not archive.exists():
            print("Downloading official PostgreSQL 17.11 portable binaries (about 364MB)...")
            urllib.request.urlretrieve("https://sbp.enterprisedb.com/getfile.jsp?fileid=1260616",archive)
        expected="80379b2c04d51c30225532e0ae04509899141e9957ed096fe749d7fd9df8f82f"
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise SystemExit("PostgreSQL download checksum mismatch. Remove only this archive and retry.")
        base=LOCAL/"postgres";base.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                parts=Path(item.filename).parts
                if len(parts)>1 and parts[0]=="pgsql" and parts[1] in ["bin","lib","share"]:
                    target=(base/item.filename).resolve()
                    if not target.is_relative_to(base.resolve()):raise SystemExit("Unsafe archive path")
                    z.extract(item,base)
        database_root=ascii_alias(ROOT);pg=database_root/".local/postgres/pgsql/bin";data=database_root/".local/pgdata";password=secrets.token_urlsafe(32);pw=database_root/".local/init-password.tmp";pw.write_text(password,encoding="utf-8")
        try:run([pg/"initdb.exe","-D",data,"-U","campus","--pwfile="+str(pw),"--encoding=UTF8","--locale=C","--auth-host=scram-sha-256","--auth-local=scram-sha-256"],stdout=subprocess.DEVNULL)
        finally:pw.unlink(missing_ok=True)
        cfgfile.write_text(json.dumps({"host":"127.0.0.1","port":55432,"name":"campus_lab","user":"campus","password":password}),encoding="utf-8")
        runtime.write_text(json.dumps({"portable":True,"pg_bin":str(pg),"pg_data":str(data),"python":str(python)}),encoding="utf-8")
    r=json.loads(runtime.read_text("utf-8"))
    if r.get("portable"):
        database_root=ascii_alias(ROOT);r.update({"pg_bin":str(database_root/".local/postgres/pgsql/bin"),"pg_data":str(database_root/".local/pgdata"),"python":str(ROOT/".venv/Scripts/python.exe")});runtime.write_text(json.dumps(r),encoding="utf-8")
    cfg=json.loads(cfgfile.read_text("utf-8"));pg=Path(r["pg_bin"]);env=os.environ.copy();env.update({"PGHOST":cfg["host"],"PGPORT":str(cfg["port"]),"PGDATABASE":cfg["name"],"PGUSER":cfg["user"],"PGPASSWORD":cfg["password"],"PGCLIENTENCODING":"UTF8","DEBUG":"1"})
    ready=subprocess.run([str(pg/"pg_isready.exe"),"-h",cfg["host"],"-p",str(cfg["port"])],stdout=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW).returncode==0
    if not ready:run([pg/"pg_ctl.exe","-D",r["pg_data"],"-l",LOCAL/"postgres.log","-o",f"-h 127.0.0.1 -p {cfg['port']} -c timezone=Asia/Shanghai","-w","start"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    check=subprocess.run([str(pg/"psql.exe"),"-d","postgres","-tAc",f"SELECT 1 FROM pg_database WHERE datname='{cfg['name']}'"],env=env,capture_output=True,text=True,creationflags=CREATE_NO_WINDOW)
    if "1" not in check.stdout:run([pg/"createdb.exe",cfg["name"]],env=env)
    python=r["python"];run([python,ROOT/"manage.py","migrate","--noinput"],cwd=ROOT,env=env);run([python,ROOT/"manage.py","seed_demo","--if-empty"],cwd=ROOT,env=env)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/login/",timeout=2) as existing:
            content=existing.read().decode("utf-8")
        if "实验资源中心" in content:
            print("This local demo is already running: http://127.0.0.1:8000")
            if not args.no_browser:webbrowser.open("http://127.0.0.1:8000")
            return
        raise SystemExit("Port 8000 is used by another service.")
    except (OSError,urllib.error.URLError):pass
    logs=[];children=[]
    try:
        for name,commands in [("web",["runserver","127.0.0.1:8000","--noreload"]),("jobs",["run_jobs","--loop"])]:
            log=(LOCAL/(name+".log")).open("ab");logs.append(log);children.append(subprocess.Popen([python,str(ROOT/"manage.py")]+commands,cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=CREATE_NO_WINDOW))
        print("Open http://127.0.0.1:8000 — demo password: Demo@2026. Ctrl+C stops web and worker.")
        time.sleep(2)
        if not args.no_browser:webbrowser.open("http://127.0.0.1:8000")
        while all(child.poll() is None for child in children):time.sleep(1)
    except KeyboardInterrupt:pass
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=10)
        for log in logs:log.close()
        print("Web and worker stopped. PostgreSQL stays local; see README for database shutdown.")
if __name__=="__main__":main()
