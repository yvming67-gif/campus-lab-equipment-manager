import os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django
django.setup()
from django.apps import apps
from django.db import connection
from django.db.models import NOT_PROVIDED
out=ROOT/"models";out.mkdir(exist_ok=True)
schema=[];links=[]
for m in apps.get_app_config("core").get_models():
    fields=[]
    for f in m._meta.fields:
        related=f.remote_field.model if f.is_relation else None
        choices=[(str(a),str(b)) for a,b in f.choices] if f.choices else []
        default="" if f.default is NOT_PROVIDED else (str(f.default.__name__)+"()" if callable(f.default) else f.default)
        record={"name":f.name,"column":f.column,"label":str(f.verbose_name),"type":f.db_type(connection),"null":f.null,"blank":f.blank,"pk":f.primary_key,"unique":f.unique,"default":default,"choices":choices,"max_length":f.max_length,"fk":related._meta.db_table if related else None,"one_to_one":f.one_to_one,"on_delete":getattr(f.remote_field.on_delete,"__name__",None) if related else None}
        fields.append(record)
        if related:links.append({"from_table":m._meta.db_table,"from_field":f.column,"to_table":related._meta.db_table,"to_field":f.target_field.column,"null":f.null,"unique":f.unique,"name":f.name})
    schema.append({"model":m.__name__,"table":m._meta.db_table,"label":str(m._meta.verbose_name),"fields":fields,"constraints":[{"name":c.name,"definition":str(c)} for c in m._meta.constraints],"indexes":[{"name":i.name,"fields":i.fields} for i in m._meta.indexes]})
(out/"schema.json").write_text(json.dumps({"models":schema,"relationships":links},ensure_ascii=False,indent=2),encoding="utf-8")
dbml=["Project campus_lab { database_type: 'PostgreSQL' Note: '设备与场地独立预约；完整业务约束见数据设计说明与迁移文件。' }"]
for m in schema:
    dbml.append("Table "+m["table"]+" {")
    for f in m["fields"]:
        flags=[]
        if f["pk"]:flags.append("pk")
        if not f["null"]:flags.append("not null")
        if f["unique"] and not f["pk"]:flags.append("unique")
        flags.append("note: '"+(f["label"]+(("；枚举："+str(f["choices"])) if f["choices"] else "")).replace("'","\'")+"'")
        dbml.append('  '+f["column"]+' "'+f["type"]+'" ['+", ".join(flags)+']')
    dbml.append("  Note: '"+m["label"]+"；约束及条件唯一项以迁移文件为准。'\n}")
for e in links:dbml.append("Ref: "+e["from_table"]+"."+e["from_field"]+(" - " if e["unique"] else " > ")+e["to_table"]+"."+e["to_field"]+"")
(out/"campus_lab.dbml").write_text("\n".join(dbml),encoding="utf-8")
mermaid=["erDiagram"]
for m in schema:
    mermaid.append("    "+m["table"]+" {")
    for f in m["fields"]:mermaid.append("        "+("bigint" if f["fk"] else f["type"].split("(")[0].replace(" ","_"))+" "+f["column"]+(" PK" if f["pk"] else " FK" if f["fk"] else ""))
    mermaid.append("    }")
for e in links:mermaid.append("    "+e["to_table"]+(" |o" if e["null"] else " ||")+"--"+("o|" if e["unique"] else "o{")+" "+e["from_table"]+' : "'+e["name"]+'"')
(out/"campus_lab.mmd").write_text("\n".join(mermaid),encoding="utf-8")
print("Exported "+str(len(schema))+" models and "+str(len(links))+" physical FK relationships")
