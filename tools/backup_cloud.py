"""Manual maintenance command; no automatic schedule or cloud credentials in CLI."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description="导出PostgreSQL与配置存储中的证据；只读源库，恢复另行执行")
    parser.add_argument("--pg-bin",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--maximum-mb",type=int,default=100)
    args=parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.cloud_settings")
    import django
    django.setup()
    from core.backup import archive_database
    result=archive_database(args.pg_bin,args.output,maximum_bytes=args.maximum_mb*1024**2)
    result.pop("table_counts")
    print(json.dumps(result,ensure_ascii=True))


if __name__=="__main__":
    try:main()
    except Exception as error:
        # Provider exceptions can contain connection information; report only type.
        print("Backup failed; error type="+type(error).__name__+". Existing archives and source data were not overwritten.",file=sys.stderr)
        raise SystemExit(1)
