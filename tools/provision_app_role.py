"""DBA-only, explicit target. Never run automatically during local-demo startup."""
import argparse,os
import psycopg
from psycopg import sql


def provision(database,role,password,host,port,user):
    if not password or len(password)<24:raise ValueError('通过CAMPUS_APP_PASSWORD提供至少24字符的独立密码')
    with psycopg.connect(dbname=database,host=host,port=port,user=user,password=os.environ.get('PGPASSWORD','')) as c:
        if c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',[role]).fetchone():raise ValueError('角色已存在，不自动重置权限或密码')
        c.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(role)))
        c.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        c.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
        c.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(role)))
        c.execute(sql.SQL('REVOKE UPDATE,DELETE,TRUNCATE ON core_auditevent FROM {}').format(sql.Identifier(role)))
        return {'role':role,'database':database,'superuser':False,'schema_owner':False,'audit_write':'INSERT only'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--role',required=True);p.add_argument('--host',default=os.environ.get('PGHOST','127.0.0.1'));p.add_argument('--port',type=int,default=int(os.environ.get('PGPORT','5432')));p.add_argument('--user',default=os.environ.get('PGUSER','postgres'));a=p.parse_args()
    print(provision(a.database,a.role,os.environ.get('CAMPUS_APP_PASSWORD',''),a.host,a.port,a.user))
