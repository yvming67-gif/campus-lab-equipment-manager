"""Portable PostgreSQL lifecycle, loopback-only. Does not touch existing databases."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import hashlib
root=Path(__file__).resolve().parent.parent
local=root/'.local'
config=json.loads((local/'settings.json').read_text(encoding='utf-8'))
if sys.platform=='win32' and not str(root).isascii():
    # EDB initdb embeds its share directory into UTF8 SQL; non-ASCII ACP paths fail.
    alias=Path(tempfile.gettempdir())/('campus-lab-'+hashlib.sha256(str(root).encode()).hexdigest()[:12])
    if not alias.exists():
        subprocess.run(['cmd','/c','mklink','/J',str(alias),str(root)],check=True,capture_output=True)
    if alias.resolve()!=root.resolve(): raise RuntimeError('运行目录别名已被其他路径占用')
    root=alias; local=root/'.local'
binary=local/'postgresql'/'pgsql'/'bin'
data=local/'pgdata'
env=os.environ.copy()
env['PGPASSWORD']=config['PGPASSWORD']
env['PGCLIENTENCODING']='UTF8'
def run(exe,*args,check=True):
    return subprocess.run([str(binary/(exe+'.exe')),*map(str,args)],env=env,check=check)
p=argparse.ArgumentParser();p.add_argument('action',choices=['init','start','stop','status']);o=p.parse_args()
if o.action=='init':
    if not (data/'PG_VERSION').exists():
        password_file=local/'pg-password.tmp'
        password_file.write_text(config['PGPASSWORD'],encoding='utf-8')
        try: run('initdb','-D',data,'-U','campus','--encoding=UTF8','--locale=C','--auth=scram-sha-256',f'--pwfile={password_file}')
        finally: password_file.unlink(missing_ok=True)
        with (data/'postgresql.conf').open('a',encoding='utf-8') as f:
            f.write("\nlisten_addresses='127.0.0.1'\nport=55432\n")
    status=run('pg_ctl','-D',data,'status',check=False)
    if status.returncode: run('pg_ctl','-D',data,'-l',local/'postgres.log','-w','start')
    probe=subprocess.run([str(binary/'psql.exe'),'-h','127.0.0.1','-p','55432','-U','campus','-d','postgres','-tAc',"SELECT 1 FROM pg_database WHERE datname='campus_lab'"],env=env,capture_output=True,text=True,check=True)
    if probe.stdout.strip()!='1': run('createdb','-h','127.0.0.1','-p','55432','-U','campus','campus_lab')
elif o.action=='start': run('pg_ctl','-D',data,'-l',local/'postgres.log','-w','start')
elif o.action=='stop': run('pg_ctl','-D',data,'-m','fast','-w','stop')
else: run('pg_ctl','-D',data,'status')
