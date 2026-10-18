"""ASCII access to EDB files, including Windows profiles with Chinese TEMP paths."""
import ctypes,hashlib,os,subprocess,tempfile
from pathlib import Path

def _short_ascii(path):
    if os.name!="nt":return None
    try:
        size=ctypes.windll.kernel32.GetShortPathNameW(str(path),None,0)
        if not size:return None
        buffer=ctypes.create_unicode_buffer(size+1)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer)):return None
        value=Path(buffer.value)
        if str(value).isascii() and value.samefile(path):return value
    except OSError:pass
    return None

def _alias_name(root):return "campus-lab-v3-"+hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]

def ascii_alias(root):
    root=Path(root).resolve()
    if os.name!="nt" or str(root).isascii():return root
    short=_short_ascii(root)
    if short:return short
    temporary=Path(tempfile.gettempdir())
    candidates=[temporary,_short_ascii(temporary)]
    public=os.environ.get("PUBLIC")
    if public:candidates.append(Path(public)/"Documents/CampusLabRuntime")
    system=os.environ.get("SystemRoot")
    if system:candidates.append(Path(system)/"Temp/CampusLabRuntime")
    seen=set()
    for base in candidates:
        if base is None or not str(base).isascii() or str(base).casefold() in seen:continue
        seen.add(str(base).casefold());alias=base/_alias_name(root)
        try:
            base.mkdir(parents=True,exist_ok=True)
            if not os.path.lexists(alias):
                subprocess.run(["cmd","/d","/c","mklink","/J",str(alias),str(root)],check=True,capture_output=True)
            if alias.resolve()!=root:raise RuntimeError("本项目目录联接已指向其他位置，已停止使用；请按运行指南排查。")
            return alias
        except (OSError,subprocess.CalledProcessError):continue
    raise RuntimeError("未找到可写的英文运行目录。请按运行指南设置本次终端TEMP后重试，已有数据无需删除。")
