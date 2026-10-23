"""Restore an existing archive without importing Django or contacting the source DB."""
import argparse,hashlib,json,os,re,subprocess,tempfile,zipfile
from pathlib import Path,PurePosixPath


def inspect_archive(archive):
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('备份包含重复文件名')
        if sum(i.file_size for i in z.infolist())>2*1024**3:raise ValueError('备份超过2GB恢复工具限制，请使用经审批的专用恢复流程')
        for name in names:
            path=PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:raise ValueError('备份含越界路径')
            if name not in ['manifest.json','database.dump'] and not name.startswith('media/'):raise ValueError('备份含未声明文件')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('session_data')!='excluded':raise ValueError('备份未声明排除会话')
        if hashlib.sha256(z.read('database.dump')).hexdigest()!=manifest['database_sha256']:raise ValueError('数据库摘要不符')
        expected={'manifest.json','database.dump'}
        for item in manifest['attachments']:
            name='media/'+item['file'];expected.add(name)
            if hashlib.sha256(z.read(name)).hexdigest()!=item['sha256']:raise ValueError('附件摘要不符')
        if set(names)!=expected:raise ValueError('附件清单与归档不一致')
        return manifest


def restore(archive,pg_bin,target_db,media,host,port,user):
    import psycopg
    from psycopg import sql
    archive=Path(archive).resolve();media=Path(media).resolve();pg_bin=Path(pg_bin).resolve()
    if not re.fullmatch('[a-z][a-z0-9_]{0,62}',target_db):raise ValueError('目标库名格式错误')
    if media.exists() and (not media.is_dir() or any(media.iterdir())):raise ValueError('目标媒体目录必须不存在或为空；不覆盖现有附件')
    manifest=inspect_archive(archive)
    kwargs=dict(host=host,port=port,user=user,password=os.environ.get('PGPASSWORD',''))
    with psycopg.connect(dbname='postgres',autocommit=True,**kwargs) as connection:
        if connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',[target_db]).fetchone():raise ValueError('目标数据库已存在，已拒绝覆盖')
        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target_db)))
    env=os.environ.copy();env.update(dict(PGHOST=str(host),PGPORT=str(port),PGUSER=user))
    with tempfile.TemporaryDirectory() as temp,zipfile.ZipFile(archive) as z:
        dump=Path(temp)/'database.dump';dump.write_bytes(z.read('database.dump'))
        process=subprocess.run([str(pg_bin/'pg_restore.exe') if os.name=='nt' else str(pg_bin/'pg_restore'),'--single-transaction','--exit-on-error','--no-owner','--no-privileges','-d',target_db,str(dump)],env=env,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode:raise RuntimeError('目标库恢复失败；源数据未修改，请检查新目标库后再重试，不自动删除它')
        with psycopg.connect(dbname=target_db,**kwargs) as connection:
            if connection.execute('SELECT COUNT(*) FROM django_session').fetchone()[0]:raise ValueError('恢复库含有效会话，拒绝验收')
            for table,expected_count in manifest.get('table_counts',{}).items():
                actual=connection.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier('public',table))).fetchone()[0]
                if actual!=expected_count:raise ValueError('恢复库表数量与归档快照不符')
        media.mkdir(parents=True,exist_ok=True)
        for item in manifest['attachments']:
            target=(media/item['file']).resolve()
            if not target.is_relative_to(media):raise ValueError('附件目标越界')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read('media/'+item['file']))
        receipt={'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'target_database':target_db,'attachments':len(manifest['attachments']),'sessions':0,'verified':True,'table_counts_verified':bool(manifest.get('table_counts'))}
        (media/'restore-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
        return receipt


def main():
    p=argparse.ArgumentParser(description='已有备份离线恢复：只连接目标实例，不需要源库')
    p.add_argument('--archive',required=True);p.add_argument('--pg-bin',required=True);p.add_argument('--target-db',required=True);p.add_argument('--media-dir',required=True)
    p.add_argument('--host',default=os.environ.get('PGHOST','127.0.0.1'));p.add_argument('--port',type=int,default=int(os.environ.get('PGPORT','5432')));p.add_argument('--user',default=os.environ.get('PGUSER','campus_restore'))
    args=p.parse_args()
    print(json.dumps(restore(args.archive,args.pg_bin,args.target_db,args.media_dir,args.host,args.port,args.user),ensure_ascii=True))

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,zipfile.BadZipFile,KeyError) as error:raise SystemExit(str(error))
