"""Portable DB + configured-storage archive; never overwrites a source object."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql
from .models import Evidence


def file_digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def pg_environment(db, inherited=None):
    env=dict(os.environ if inherited is None else inherited)
    env.update(PGHOST=str(db["HOST"]),PGPORT=str(db["PORT"]),PGUSER=db["USER"],PGPASSWORD=db["PASSWORD"],PGCONNECT_TIMEOUT="10")
    variables={"sslmode":"PGSSLMODE", "channel_binding":"PGCHANNELBINDING", "sslrootcert":"PGSSLROOTCERT"}
    for key,variable in variables.items():
        if key in db.get("OPTIONS",{}):env[variable]=str(db["OPTIONS"][key])
    return env


def archive_database(pg_bin, output, *, storage=None, maximum_bytes=100*1024**2):
    """Export one PostgreSQL snapshot and matching immutable evidence objects.

    The caller runs on a trusted maintenance computer. Django may use S3;
    nothing assumes MEDIA_ROOT exists. A verified archive is not a restore test.
    """
    if connection.in_atomic_block:
        raise ValueError("备份必须在独立维护连接执行，不能嵌入现有业务事务")
    if not 0 < maximum_bytes <= 2*1024**3:
        raise ValueError("备份容量限制须大于0且不超过2GB")
    target=Path(output).resolve();pg_bin=Path(pg_bin).resolve()
    if target.exists():
        raise ValueError("目标归档已存在，拒绝覆盖")
    if target.is_relative_to(settings.BASE_DIR) and not target.is_relative_to(settings.LOCAL):
        raise ValueError("备份必须保存在项目外或本地忽略目录，不能进入公开源码")
    binary=pg_bin/("pg_dump.exe" if os.name=="nt" else "pg_dump")
    if not binary.is_file():raise ValueError("请提供已安装的PostgreSQL客户端目录")
    target.parent.mkdir(parents=True,exist_ok=True)
    storage=storage if storage is not None else default_storage
    db=settings.DATABASES["default"]
    env=pg_environment(db)
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        temp=Path(directory);dump=temp/"database.dump";archive=temp/"archive.zip"
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("SELECT pg_export_snapshot()")
                snapshot=cursor.fetchone()[0]
                attachments=list(Evidence.objects.order_by("pk").values("id","file","sha256"))
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'django_session' ORDER BY tablename")
                tables=[row[0] for row in cursor.fetchall()];counts={}
                for table in tables:
                    cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("public",table)))
                    counts[table]=cursor.fetchone()[0]
            # Binary stdout avoids Windows native file-name encoding problems.
            with dump.open("wb") as stream:
                result=subprocess.run([str(binary),"-Fc","--no-owner","--no-privileges",
                                       "--exclude-table-data=django_session","--snapshot="+snapshot,"-d",db["NAME"]],
                                      env=env,stdout=stream,stderr=subprocess.PIPE,timeout=180,
                                      creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if result.returncode:raise RuntimeError("数据库快照导出失败；未发布归档，源数据未修改")
        total=dump.stat().st_size
        if total>maximum_bytes:raise ValueError("数据库归档超过所选容量限制")
        manifest=dict(database_sha256=file_digest(dump),attachments=attachments,session_data="excluded",
                      created=timezone.now().isoformat(),table_counts=counts,snapshot_method="pg_export_snapshot",
                      storage_scope="configured-storage; attachment bytes checked against snapshot metadata")
        copied={}
        with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as package:
            package.write(dump,"database.dump")
            for item in attachments:
                name=item["file"];path=PurePosixPath(name)
                if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                    raise ValueError("附件路径无效，拒绝归档")
                if name in copied:
                    if copied[name]!=item["sha256"]:raise ValueError("同名附件摘要冲突")
                    continue
                digest=hashlib.sha256()
                with storage.open(name,"rb") as source,package.open("media/"+name,"w") as dest:
                    while block:=source.read(1024**2):
                        total+=len(block)
                        if total>maximum_bytes:raise ValueError("联合归档超过所选容量限制")
                        digest.update(block);dest.write(block)
                if digest.hexdigest()!=item["sha256"]:raise ValueError("附件摘要与数据库快照不符；未发布归档")
                copied[name]=item["sha256"]
            package.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        # Publish only after all objects and checksums were collected. Exclusive
        # creation protects existing archives even if another process races us.
        published=False
        try:
            destination=target.open("xb");published=True
            with destination,archive.open("rb") as source:shutil.copyfileobj(source,destination)
        except Exception:
            if published:target.unlink(missing_ok=True)
            raise
    return dict(archive=str(target),archive_sha256=file_digest(target),attachments=len(attachments),
                snapshot_method="pg_export_snapshot",table_counts=counts,session_data="excluded",
                restore_verified=False)
