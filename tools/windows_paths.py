"""Keep EDB initdb SQL paths ASCII without moving the project or database."""
import hashlib,os,subprocess,tempfile
from pathlib import Path

def ascii_alias(root):
    root=Path(root).resolve()
    if os.name!="nt" or str(root).isascii():return root
    base=Path(tempfile.gettempdir())
    if not str(base).isascii():raise RuntimeError("PostgreSQL setup needs an ASCII TEMP directory on this Windows account.")
    alias=base/("campus-lab-v2-"+hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16])
    if not os.path.lexists(alias):
        subprocess.run(["cmd","/d","/c","mklink","/J",str(alias),str(root)],check=True,capture_output=True)
    if alias.resolve()!=root:raise RuntimeError("The local PostgreSQL directory alias belongs to another folder.")
    return alias
