"""Consistent pg_dump backup and safe verification in a fresh throwaway database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from datetime import datetime,timezone
import psycopg
from psycopg import sql
root=Path(__file__).resolve().parent.parent
local=root/'.local'
cfg=json.loads((local/'settings.json').read_text(encoding='utf-8')) if (local/'settings.json').exists() else {}
def setting(k,default=None):return os.environ.get(k,cfg.get(k,default))
params=dict(host=setting('PGHOST','127.0.0.1'),port=setting('PGPORT','55432'),user=setting('PGUSER','campus'),password=setting('PGPASSWORD'),dbname=setting('PGDATABASE','campus_lab'))
bin_dir=local/'postgresql'/'pgsql'/'bin'
def executable(name):
    candidate=bin_dir/(name+('.exe' if sys.platform=='win32' else ''))
    return str(candidate) if candidate.exists() else shutil.which(name) or name
env=os.environ.copy();env['PGPASSWORD']=params['password'];env['PGCLIENTENCODING']='UTF8'
def cli(name,database,*args):
    subprocess.run([executable(name),'-h',params['host'],'-p',str(params['port']),'-U',params['user'],'-d',database,*map(str,args)],env=env,check=True)
def signatures(conn):
    result={}
    tables=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    for (table,) in tables:
        rows=conn.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table))).fetchall()
        result[table]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(row[0] for row in rows).encode()).hexdigest()}
    return result
p=argparse.ArgumentParser();p.add_argument('action',choices=['create','verify']);p.add_argument('--file');o=p.parse_args()
backups=local/'backups';backups.mkdir(parents=True,exist_ok=True)
if o.action=='create':
    path=Path(o.file) if o.file else backups/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.dump')
    if path.exists(): raise RuntimeError('备份目标已存在，请指定新文件名')
    with psycopg.connect(**params) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        snapshot=conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
        cli('pg_dump',params['dbname'],'-Fc','--snapshot',snapshot,'-f',path)
        checks=signatures(conn)
    path.with_suffix('.manifest.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'已生成一致性备份：{path.name}（含逐表数量和数据摘要）')
else:
    path=Path(o.file) if o.file else max(backups.glob('*.dump'),key=lambda p:p.stat().st_mtime)
    expected=json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    temporary='campus_restore_verify_'+secrets.token_hex(6)
    created=False
    started=datetime.now(timezone.utc)
    try:
        with psycopg.connect(**{**params,'dbname':'postgres'},autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(temporary)));created=True
        cli('pg_restore',temporary,'--no-owner','--no-acl','--exit-on-error',path)
        with psycopg.connect(**{**params,'dbname':temporary}) as restored: actual=signatures(restored)
        if actual!=expected: raise RuntimeError('恢复校验失败：逐表数据摘要不同')
        report={'verified_at':datetime.now(timezone.utc).isoformat(),'tables':len(actual),
            'rows':sum(t['count'] for t in actual.values()),'seconds':(datetime.now(timezone.utc)-started).total_seconds(),
            'result':'PASS','method':'fresh PostgreSQL database; all table counts and SHA256 row digests match'}
        out=root/'artifacts';out.mkdir(exist_ok=True)
        (out/'restore-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        # Only ever remove the newly created, randomly named verification DB.
        if created and temporary.startswith('campus_restore_verify_'):
            with psycopg.connect(**{**params,'dbname':'postgres'},autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(temporary)))
