"""需求、源码定位、ERD及UML完整性检查，不访问真实校园系统。"""
import ast,json,re
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/"docs/specification.json").read_text("utf-8"));schema=json.loads((ROOT/"models/schema.json").read_text("utf-8"));mapping=json.loads((ROOT/"docs/implementation_map.json").read_text("utf-8"))
ids=lambda key:{x["id"] for x in d[key]}
uc=ids("cases");br=ids("rules");qr=ids("quality");ac=ids("acceptance");fr={c["fr"] for c in d["cases"]};spec={c["spec"] for c in d["cases"]};problems=ids("problems")
assert len(uc)==26 and len(fr)==26 and len(spec)==26 and len(br)==47 and len(qr)==11 and len(ac)==64
assert set(mapping)==uc
for case in d["cases"]:
    assert set(case["rules"])<=br and set(case["quality"])<=qr
    assert case["ac_normal"] in ac and case["ac_exception"] in ac
    assert case["steps"] and case["alternatives"] and case["errors"]
    steps={int(re.match(r"(\d+)",x).group(1)) for x in case["steps"]}
    for path in case["alternatives"]+case["errors"]:assert int(re.match(r"(\d+)",path).group(1)) in steps
for row in d["trace"]:assert row["problem"] in problems and row["fr"] in fr and row["uc"] in uc and row["spec"] in spec and set(row["rules"])<=br and set(row["ac"])<=ac
assert {r["problem"] for r in d["trace"]}==problems
assert {r["fr"] for r in d["trace"]}==fr
assert {b for c in d["cases"] for b in c["rules"]}==br
# Compare exported entities with actual model declarations, not a fixed legacy count.
model_tree=ast.parse((ROOT/'core/models.py').read_text('utf-8'))
declared={n.name for n in model_tree.body if isinstance(n,ast.ClassDef) and any((isinstance(b,ast.Attribute) and b.attr=='Model') or (isinstance(b,ast.Name) and b.id=='AbstractUser') for b in n.bases)}
assert {m['model'] for m in schema['models']}==declared
assert len(declared)==len(schema['models'])
assert len({m['table'] for m in schema['models']})==len(declared)
assert all(m['fields'] and len({f['column'] for f in m['fields']})==len(m['fields']) for m in schema['models'])
names={m["table"] for m in schema["models"]}
for e in schema["relationships"]:assert e["from_table"] in names and e["to_table"] in names
assert (ROOT/"models/campus_lab.dbml").read_text("utf-8").count("Ref: ")==len(schema["relationships"])
assert (ROOT/"models/campus_lab.mmd").read_text("utf-8").count("--")==len(schema["relationships"])
fig=ROOT/"models/图形源文件/图4-1-系统总用例图.drawio";tree=ET.parse(fig);cells={c.attrib["id"]:c for c in tree.iter("mxCell")};assert uc<=set(cells)
for uid in uc:
    c=cells[uid];assert "ellipse" in c.attrib["style"];g=c.find("mxGeometry");x,y,w,h=[float(g.attrib[k]) for k in ["x","y","width","height"]];assert 85<=x and x+w<=423 and 4<=y and y+h<=664
for f in (ROOT/"core").rglob("*.py"):ast.parse(f.read_text("utf-8"))
for f in (ROOT/"models/图形源文件").glob("*.drawio"):ET.parse(f)
# 源码定位确实存在，不仅是编号匹配。
for key,ref in mapping.items():
    for path in re.findall(r"core/[A-Za-z0-9_/]+\.py",ref):assert (ROOT/path).exists(),(key,path)
print("PASS: 26 FR/UC/SPEC, 47 BR, 11 QR, 64 AC; full trace, exported models/FK, editable UML and source locations")

# Full-field ERD is verified against the exported application model, not a manual table list.
import hashlib
manifest=json.loads((ROOT/"models/ERD/manifest.json").read_text("utf-8"))
assert manifest["schema_sha256"]==hashlib.sha256(json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()
actual={m["model"]:m for m in schema["models"]}
assert manifest["entity_count"]==len(actual)==len(schema["models"])
assert manifest["field_count"]==sum(len(m["fields"]) for m in actual.values())==sum(len(m["fields"]) for m in schema["models"])
assert manifest["relationship_count"]==len(schema["relationships"])==len(schema["relationships"])
assert {e["model"] for e in manifest["entities"]}==set(actual)
for e in manifest["entities"]:
    m=actual[e["model"]];assert e["table"]==m["table"]
    assert len(e["fields"])==len(m["fields"]) and set(e["fields"])=={f["column"] for f in m["fields"]}
from_table={m["table"]:m["model"] for m in actual.values()}
expected={(from_table[r["from_table"]],r["from_field"],from_table[r["to_table"]],r["to_field"],r["null"],r["unique"]) for r in schema["relationships"]}
assert {(r["child"],r["from_field"],r["parent"],r["to_field"],r["null"],r["unique"]) for r in manifest["relationships"]}==expected
assert {r["id"] for r in manifest["relationships"]}=={f"R{i:02d}" for i in range(1,len(schema["relationships"])+1)}
svg=ET.parse(ROOT/"models/图形源文件/ERD-00-完整实体关系总图.svg")
svgids={n.attrib.get("id") for n in svg.iter() if n.attrib.get("id")}
assert sum(i.startswith("entity-") for i in svgids)==len(schema["models"])
assert sum(i.startswith("field-") for i in svgids)==sum(len(m["fields"]) for m in schema["models"])
assert sum(i.startswith("edge-") for i in svgids)==len(schema["relationships"])
full=ET.parse(ROOT/"models/图形源文件/ERD-00-完整实体关系总图.drawio")
fullcells={c.attrib["id"]:c for c in full.iter("mxCell")}
assert fullcells["shared"].attrib.get("visible")=="0"
for r in manifest["relationships"]:
    edge=fullcells["edge-"+r["id"]]
    child=actual[r["child"]]["table"];parent=actual[r["parent"]]["table"]
    assert edge.attrib["source"]=="field-"+child+"-"+r["from_field"]
    assert edge.attrib["target"]=="field-"+parent+"-"+r["to_field"]
    for a,b in zip(r["points"],r["points"][1:]):
        assert a[0]==b[0] or a[1]==b[1]
        for entity in manifest["entities"]:
            x,y,w,h=entity["rect"]
            cross=(a[1]==b[1] and y+.2<a[1]<y+h-.2 and max(min(a[0],b[0]),x+.2)<min(max(a[0],b[0]),x+w-.2)) or (a[0]==b[0] and x+.2<a[0]<x+w-.2 and max(min(a[1],b[1]),y+.2)<min(max(a[1],b[1]),y+h-.2))
            assert not cross,(r["id"],entity["model"])
assert sorted(n for d in manifest["domains"] for n in d["entities"])==sorted(actual)
for d in manifest["domains"]:
    paths=list((ROOT/"models/图形源文件").glob(f"图D-*-*ERD.svg"))
    matches=[f for f in paths if d["code"]+" · " in f.read_text("utf-8")]
    assert len(matches)==1,d["code"]
    domain_svg=ET.parse(matches[0]);groups={n.attrib.get("id") for n in domain_svg.iter()}
    for name in d["entities"]:
        for field in actual[name]["fields"]:assert "field-"+actual[name]["table"]+"-"+field["column"] in groups
print("PASS: vector ERD has all exported entities / fields / field-attached FK; six complete domains; no card-crossing connectors")
