"""需求、源码定位、ERD及UML完整性检查，不访问真实校园系统。"""
import ast,json,re
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/"docs/specification.json").read_text("utf-8"));schema=json.loads((ROOT/"models/schema.json").read_text("utf-8"));mapping=json.loads((ROOT/"docs/implementation_map.json").read_text("utf-8"))
ids=lambda key:{x["id"] for x in d[key]}
uc=ids("cases");br=ids("rules");qr=ids("quality");ac=ids("acceptance");fr={c["fr"] for c in d["cases"]};spec={c["spec"] for c in d["cases"]};problems=ids("problems")
assert len(uc)==26 and len(fr)==26 and len(spec)==26 and len(br)==35 and len(qr)==11 and len(ac)==52
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
assert len(schema["models"])==31 and len(schema["relationships"])==52
names={m["table"] for m in schema["models"]}
for e in schema["relationships"]:assert e["from_table"] in names and e["to_table"] in names
assert (ROOT/"models/campus_lab.dbml").read_text("utf-8").count("Ref: ")==52
assert (ROOT/"models/campus_lab.mmd").read_text("utf-8").count("--")==52
fig=ROOT/"models/图形源文件/图4-1-系统总用例图.drawio";tree=ET.parse(fig);cells={c.attrib["id"]:c for c in tree.iter("mxCell")};assert uc<=set(cells)
for uid in uc:
    c=cells[uid];assert "ellipse" in c.attrib["style"];g=c.find("mxGeometry");x,y,w,h=[float(g.attrib[k]) for k in ["x","y","width","height"]];assert 85<=x and x+w<=423 and 4<=y and y+h<=664
for f in (ROOT/"core").rglob("*.py"):ast.parse(f.read_text("utf-8"))
for f in (ROOT/"models/图形源文件").glob("*.drawio"):ET.parse(f)
# 源码定位确实存在，不仅是编号匹配。
for key,ref in mapping.items():
    for path in re.findall(r"core/[A-Za-z0-9_/]+\.py",ref):assert (ROOT/path).exists(),(key,path)
print("PASS: 26 FR/UC/SPEC, 35 BR, 11 QR, 52 AC; full trace, 31 models/52 FK, editable UML and source locations")
