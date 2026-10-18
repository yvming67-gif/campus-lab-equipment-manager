"""Stop only the PostgreSQL cluster recorded in this project's private runtime."""
import json,subprocess
from pathlib import Path
from windows_paths import ascii_alias

def main():
    root=Path(__file__).resolve().parents[1];file=root/".local/runtime.json"
    if not file.exists():print("本项目尚未初始化，无需停止数据库。");return
    runtime=json.loads(file.read_text("utf-8"))
    if runtime.get("portable"):
        alias=ascii_alias(root);runtime.update({"pg_bin":str(alias/".local/postgres/pgsql/bin"),"pg_data":str(alias/".local/pgdata")})
    ctl=str(Path(runtime["pg_bin"])/"pg_ctl.exe");flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
    status=subprocess.run([ctl,"-D",runtime["pg_data"],"status"],capture_output=True,creationflags=flags)
    if status.returncode:print("本项目数据库已经停止。");return
    subprocess.run([ctl,"-D",runtime["pg_data"],"-m","fast","-w","stop"],check=True,creationflags=flags)
if __name__=="__main__":main()
