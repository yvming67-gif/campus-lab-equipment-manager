import json,subprocess
from pathlib import Path
from windows_paths import ascii_alias
root=Path(__file__).resolve().parents[1];r=json.loads((root/".local/runtime.json").read_text("utf-8"))
if r.get("portable"):
    alias=ascii_alias(root);r.update({"pg_bin":str(alias/".local/postgres/pgsql/bin"),"pg_data":str(alias/".local/pgdata")})
subprocess.run([str(Path(r["pg_bin"])/"pg_ctl.exe"),"-D",r["pg_data"],"-m","fast","-w","stop"],check=True)
