"""Explicitly scoped backup maintenance. Preview is always the default."""
import argparse,json,hashlib,shutil,time
from pathlib import Path
from restore_archive import inspect_archive


def maintenance(folder,days=30,keep=7,replica=None,apply=False,now=None):
    folder=Path(folder).resolve();now=now or time.time()
    if days<1 or keep<1:raise ValueError('保留天数和最少份数必须为正数')
    candidates=sorted(folder.glob('backup-*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)
    valid=[];invalid=[]
    for archive in candidates:
        try:inspect_archive(archive);valid.append(archive)
        except Exception:invalid.append(archive.name)
    deletions=[p for p in valid[keep:] if p.stat().st_mtime<now-days*86400]
    result={'preview':not apply,'valid':len(valid),'invalid':invalid,'delete':[p.name for p in deletions],'replica':[]}
    if replica:
        replica=Path(replica).resolve()
        if replica==folder or replica.is_relative_to(folder) or folder.is_relative_to(replica):raise ValueError('副本目录必须独立，不能是源目录或其父子目录')
        if apply:replica.mkdir(parents=True,exist_ok=True)
        for archive in valid:
            destination=replica/archive.name
            if apply:
                if destination.exists() and hashlib.sha256(destination.read_bytes()).digest()!=hashlib.sha256(archive.read_bytes()).digest():raise ValueError('已有副本内容不同，拒绝覆盖')
                if not destination.exists():shutil.copy2(archive,destination)
                inspect_archive(destination)
                if hashlib.sha256(destination.read_bytes()).digest()!=hashlib.sha256(archive.read_bytes()).digest():raise ValueError('副本摘要不符')
            result['replica'].append(archive.name)
    if apply and deletions:
        if not replica:raise ValueError('清理前必须指定并验证独立副本目录')
        for archive in deletions:
            if archive.is_symlink() or archive.parent.resolve()!=folder:raise ValueError('删除目标不在指定备份目录')
            archive.unlink()
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--replica');p.add_argument('--days',type=int,default=30);p.add_argument('--keep',type=int,default=7);p.add_argument('--apply',action='store_true');a=p.parse_args()
    print(json.dumps(maintenance(a.folder,a.days,a.keep,a.replica,a.apply),ensure_ascii=True))
