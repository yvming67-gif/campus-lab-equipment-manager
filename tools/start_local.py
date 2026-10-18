"""Windows课程演示：独立安装、私有数据库和真实HTTP就绪检查。"""
import argparse,ctypes,hashlib,json,os,secrets,socket,struct,subprocess,sys,time,urllib.request,urllib.error,venv,webbrowser,zipfile
from pathlib import Path
from windows_paths import ascii_alias
ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/".local"
NO_WINDOW=getattr(subprocess,"CREATE_NO_WINDOW",0)
PG_URL="https://sbp.enterprisedb.com/getfile.jsp?fileid=1260616"
PG_SHA="80379b2c04d51c30225532e0ae04509899141e9957ed096fe749d7fd9df8f82f"
class StartupError(Exception):pass

def run(args,**kwargs):
    return subprocess.run([str(a) for a in args],check=True,creationflags=NO_WINDOW,**kwargs)

def save(path,value):
    temporary=path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.replace(path)

def available_port(first):
    if not 1024<=first<=65515:raise StartupError("端口必须在1024至65515之间。")
    for port in range(first,first+20):
        with socket.socket() as probe:
            if os.name=="nt":probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try:probe.bind(("127.0.0.1",port));return port
            except OSError:continue
    raise StartupError(f"{first}起连续20个端口不可用，请指定其他端口。")

def instance_at(port,expected):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/",timeout=1) as response:value=json.load(response)
        return value.get("service")=="campus-lab-demo" and value.get("instance")==expected
    except (OSError,urllib.error.URLError,ValueError):return False

def download(archive):
    if archive.exists():
        with archive.open("rb") as source:digest=hashlib.file_digest(source,"sha256").hexdigest()
        if digest!=PG_SHA:raise StartupError("PostgreSQL压缩包校验失败，请仅删除.local/postgres-17.11.zip后重试。")
        return
    temporary=archive.with_suffix(".zip.part")
    print("[2/6] 下载官方PostgreSQL 17.11（约364MB），请保持网络连接。",flush=True)
    with urllib.request.urlopen(PG_URL,timeout=60) as response,temporary.open("wb") as target:
        total=int(response.headers.get("Content-Length",0));done=0;shown=-1;digest=hashlib.sha256()
        while chunk:=response.read(1024*1024):
            target.write(chunk);digest.update(chunk);done+=len(chunk)
            percent=done*100//total if total else 0
            if total and percent//10>shown:shown=percent//10;print(f"  下载进度：{min(percent,100)}%",flush=True)
    if digest.hexdigest()!=PG_SHA:raise StartupError("下载文件校验失败，未运行其中的程序；请重试下载。")
    temporary.replace(archive)

def install(args):
    if os.name!="nt":raise StartupError("自动安装适用于Windows；其他系统请按README配置PostgreSQL。")
    if sys.version_info[:2]!=(3,13) or struct.calcsize("P")!=8:raise StartupError("请安装Python 3.13的Windows 64位版本，再双击启动演示.cmd。")
    print("[1/6] 创建独立Python环境并安装锁定版本依赖。",flush=True)
    folder=ROOT/".venv";python=folder/"Scripts/python.exe"
    if not python.exists():venv.create(folder,with_pip=True)
    run([python,"-m","pip","install","-r",ROOT/"requirements.txt"],cwd=ROOT)
    archive=LOCAL/"postgres-17.11.zip";download(archive)
    print("[3/6] 解压并准备本项目专用数据库。",flush=True)
    base=LOCAL/"postgres";base.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            parts=Path(item.filename).parts
            if len(parts)>1 and parts[0]=="pgsql" and parts[1] in ("bin","lib","share"):
                if not (base/item.filename).resolve().is_relative_to(base.resolve()):raise StartupError("压缩包中出现越界路径。")
                package.extract(item,base)
    alias=ascii_alias(ROOT);pg=alias/".local/postgres/pgsql/bin";data=alias/".local/pgdata"
    check=subprocess.run([str(pg/"initdb.exe"),"--version"],capture_output=True,creationflags=NO_WINDOW)
    if check.returncode:raise StartupError("PostgreSQL组件无法启动。请安装微软Visual C++ 2015—2022 x64运行库；官方链接见老师运行指南。")
    file=LOCAL/"database.json"
    config=json.loads(file.read_text("utf-8")) if file.exists() else {"host":"127.0.0.1","port":available_port(args.db_port),"name":"campus_lab","user":"campus","password":secrets.token_urlsafe(32)}
    # Credentials are saved before initdb so interrupted setup never resets a database password.
    save(file,config)
    if not (data/"PG_VERSION").exists():
        if data.exists() and any(data.iterdir()):raise StartupError("初始化中断且数据库目录非空。请保留.local/pgdata并联系维护者，不要直接删除已有数据。")
        pw=alias/".local/init-password.tmp";pw.write_text(config["password"],encoding="utf-8")
        try:run([pg/"initdb.exe","-D",data,"-U",config["user"],"--pwfile="+str(pw),"--encoding=UTF8","--locale=C","--auth-host=scram-sha-256","--auth-local=scram-sha-256"],stdout=subprocess.DEVNULL)
        finally:pw.unlink(missing_ok=True)
    save(LOCAL/"runtime.json",{"portable":True,"pg_bin":str(pg),"pg_data":str(data),"python":str(python)})

def main():
    # English Windows redirects stdout using an ANSI code page by default.
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    os.environ["PYTHONUTF8"]="1";os.environ["PYTHONIOENCODING"]="utf-8"
    parser=argparse.ArgumentParser(description="校园实验室课程演示启动器")
    parser.add_argument("--setup",action="store_true");parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--smoke-test",action="store_true",help="启动并验证网页后退出，不清空业务数据")
    parser.add_argument("--port",type=int,default=8000);parser.add_argument("--db-port",type=int,default=55432)
    args=parser.parse_args();LOCAL.mkdir(exist_ok=True)
    if os.name=="nt":ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x8000)
    file=LOCAL/"runtime.json"
    if not file.exists():
        if not args.setup:raise StartupError("首次使用请双击启动演示.cmd，或运行python tools/start_local.py --setup。")
        install(args)
    runtime=json.loads(file.read_text("utf-8"))
    if runtime.get("portable"):
        alias=ascii_alias(ROOT);runtime.update({"pg_bin":str(alias/".local/postgres/pgsql/bin"),"pg_data":str(alias/".local/pgdata"),"python":str(ROOT/".venv/Scripts/python.exe")})
    config_file=LOCAL/"database.json";config=json.loads(config_file.read_text("utf-8"));pg=Path(runtime["pg_bin"])
    if not Path(runtime["python"]).exists():raise StartupError("Python虚拟环境缺失。请保留数据库并按老师运行指南修复环境。")
    env=os.environ.copy()
    for key in ("DJANGO_SECRET_KEY","MOCK_IDP_KEY","DJANGO_SETTINGS_MODULE"):env.pop(key,None)
    env.update({"PGHOST":config["host"],"PGPORT":str(config["port"]),"PGDATABASE":config["name"],"PGUSER":config["user"],"PGPASSWORD":config["password"],"PGCLIENTENCODING":"UTF8","DEBUG":"1","DEMO_MODE":"1","ALLOWED_HOSTS":"127.0.0.1,localhost,testserver","PYTHONUTF8":"1"})
    status=subprocess.run([str(pg/"pg_ctl.exe"),"-D",runtime["pg_data"],"status"],capture_output=True,creationflags=NO_WINDOW)
    if status.returncode:
        # Port availability is not evidence that the responding database belongs to this project.
        config["port"]=available_port(int(config["port"]));env["PGPORT"]=str(config["port"]);save(config_file,config)
        print("[4/6] 启动本项目数据库。",flush=True)
        run([pg/"pg_ctl.exe","-D",runtime["pg_data"],"-l",ascii_alias(ROOT)/".local/postgres.log","-o",f"-h 127.0.0.1 -p {config['port']} -c timezone=Asia/Shanghai","-w","start"],stdout=subprocess.DEVNULL)
    check=subprocess.run([str(pg/"psql.exe"),"-d","postgres","-tAc","SELECT 1 FROM pg_database WHERE datname='campus_lab'"],env=env,capture_output=True,text=True,creationflags=NO_WINDOW)
    if check.returncode:raise StartupError("本项目数据库连接失败，请查看.local/postgres.log；不要删除已有数据库。")
    if check.stdout.strip()!="1":run([pg/"createdb.exe",config["name"]],env=env)
    python=runtime["python"];print("[5/6] 同步表结构及模拟主数据（保留已有业务）。",flush=True)
    run([python,ROOT/"manage.py","migrate","--noinput"],cwd=ROOT,env=env)
    run([python,ROOT/"manage.py","seed_demo","--if-empty"],cwd=ROOT,env=env)
    keys=json.loads((LOCAL/"keys.json").read_text("utf-8"));instance=hashlib.sha256(keys["django"].encode()).hexdigest()[:16]
    previous=runtime.get("web_port")
    if previous and instance_at(previous,instance):
        url=f"http://127.0.0.1:{previous}";print(f"本项目已运行：{url}；没有重复启动或清空数据。",flush=True)
        if not args.no_browser and not args.smoke_test:webbrowser.open(url)
        return
    port=available_port(args.port);runtime["web_port"]=port;save(file,runtime)
    logs=[];children=[];url=f"http://127.0.0.1:{port}"
    try:
        print("[6/6] 启动网页和后台任务，检查实际HTTP响应。",flush=True)
        for name,command in [("web",["runserver",f"127.0.0.1:{port}","--noreload"]),("jobs",["run_jobs","--loop"])]:
            log=(LOCAL/(name+".log")).open("ab");logs.append(log)
            children.append(subprocess.Popen([python,str(ROOT/"manage.py")]+command,cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=NO_WINDOW))
        deadline=time.monotonic()+45
        while not instance_at(port,instance):
            if any(child.poll() is not None for child in children) or time.monotonic()>deadline:raise StartupError("网页或后台启动失败，请查看.local/web.log和.local/jobs.log。")
            time.sleep(.3)
        with urllib.request.urlopen(url+"/login/",timeout=5) as response:
            if "实验资源中心" not in response.read().decode("utf-8"):raise StartupError("网页响应内容未通过检查。")
        save(LOCAL/"startup-result.json",{"status":"passed","http_login":200,"url":url,"database_port":config["port"],"smoke_test":args.smoke_test,"python":sys.version.split()[0],"checked_at":time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        print(f"启动成功：{url}；演示密码：Demo@2026",flush=True)
        if args.smoke_test:print("PASS：安装/启动、数据库迁移、模拟主数据及真实HTTP响应检查通过。",flush=True);return
        if not args.no_browser:webbrowser.open(url)
        print("请保留本窗口。按Ctrl+C停止网页和后台任务。",flush=True)
        while all(child.poll() is None for child in children):time.sleep(1)
        raise StartupError("网页或后台意外退出，请查看.local中的日志。")
    except KeyboardInterrupt:print("正在停止本项目网页和后台。",flush=True)
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=10)
        for log in logs:log.close()
        print("网页和后台已停止。数据库单独停止请双击停止数据库.cmd。",flush=True)

if __name__=="__main__":
    try:main()
    except (StartupError,RuntimeError,OSError,subprocess.CalledProcessError,ValueError,zipfile.BadZipFile) as error:
        if isinstance(error,subprocess.CalledProcessError):print("启动步骤执行失败。请检查网络、Python版本及.local日志；排查方法见老师运行指南。",file=sys.stderr)
        else:print(str(error),file=sys.stderr)
        sys.exit(1)
