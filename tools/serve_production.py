"""Single-process Windows/Linux application server behind a trusted HTTPS proxy."""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE']='config.production'
import django
django.setup()
from django.db import connection
from django.core.management import call_command
from django.conf import settings
from django.utils.module_loading import import_string


def check_role():
    with connection.cursor() as c:
        c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')
        if any(c.fetchone()):raise RuntimeError('生产运行账号不能是超级用户或角色/数据库创建者')
        c.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user")
        if c.fetchone()[0]:raise RuntimeError('生产运行账号不能拥有业务表；请使用独立迁移账号')


def main():
    check_role();call_command('check',deploy=True)
    adapter=import_string(os.environ['CAMPUS_IDENTITY_ADAPTER'])
    if not callable(adapter):raise RuntimeError('正式身份适配器必须是可调用实现')
    from waitress import serve
    from config.wsgi import application
    serve(application,host='127.0.0.1',port=int(os.environ.get('CAMPUS_WEB_PORT','8080')),threads=8)

if __name__=='__main__':main()
