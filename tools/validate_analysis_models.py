"""校验分析模型契约和引用，不将静态校验当作实际业务验收。"""
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs/课程规范完善'
def load(p):return json.loads(p.read_text('utf-8'))
s=load(ROOT/'docs/specification.json');cases={c['id']:c for c in s['cases']}
sources={r['id'] for r in s['sources']};fields={r['name'] for r in s['fields']}
receipts={r['name']:r for r in s['receipt_dictionary']}
for c in cases.values():
    anchors={re.match(r'(\d+[a-z]?)',t).group(1) for t in c['steps']+c['alternatives']+c['errors']}
    assert str(c['input_step']) in anchors and str(c['receipt_step']) in anchors,c['id']
    assert set(c['description_sources'])<=sources and c['receipt_group'] in receipts,c['id']
    assert set(c['field_ids'])<=fields,c['id']
    for r in c['rule_step_links']:
        assert r['rule'] in c['rules'] and set(r['steps'])<=anchors,(c['id'],r)
ops=load(DOC/'分析操作目录.json');messages=load(DOC/'内部协作消息.json');identifiers=[]
classes=load(DOC/'分析类操作目录.json')
entity_ids={'用户':'User','资源':'Resource','预约':'Reservation','设备借出':'Loan','归还凭据':'ReturnRecord','异常':'Incident','维修工单':'WorkOrder','审计':'AuditEvent'}
for seq in messages:
    c=cases[seq['uc']];anchors={re.match(r'(\d+[a-z]?)',t).group(1) for t in c['steps']+c['alternatives']+c['errors']}
    depth=0
    for row in seq['rows']:
        if row['type']=='frame':depth+=1
        elif row['type']=='end':depth-=1;assert depth>=0
        elif row['type']=='call':
            identifiers.append(row['id']);receiver=seq['lanes'][row['b']][0]
            assert row['op'] in ops[receiver],(row['id'],receiver,row['op'])
            if seq['lanes'][row['b']][1]=='entity':
                declared={op.split('(')[0] for c in classes if c['class_id']==entity_ids[receiver] for op in c['operations']}
                assert row['op'] in declared,(row['id'],'类图缺少接收操作',receiver,row['op'])
            assert row['step'] in anchors,(row['id'],row['step'])
    assert depth==0,seq['id']
assert len(identifiers)==len(set(identifiers))
for diagram in load(DOC/'分析模型图形目录.json'):
    source=ROOT/diagram['source'];assert source.is_file()
    for ext in ['.drawio','.svg','.puml']:assert source.with_suffix(ext).is_file(),(source,ext)
    assert (ROOT/diagram['preview']).is_file()
traces=load(ROOT/'docs/逻辑整改/完整需求追踪矩阵.json')
for t in traces:
    if t['uc'] in cases:assert t['rules']==cases[t['uc']]['rules'],t['id']
    else:assert t['id']=='TRACE-OPS-01' and 'DC-01' in t['rules'],t['id']
reverse=load(ROOT/'docs/逻辑整改/业务规则反向追踪.json')
for row in reverse:
    assert set(row['use_cases'])=={c['id'] for c in cases.values() if row['id'] in c['rules']},row['id']
    assert set(row['acceptance'])=={a['id'] for a in s['acceptance'] if row['id'] in a['rules']},row['id']
schema=load(ROOT/'models/schema.json');models={m['model']:m for m in schema['models']}
for model,expected in [('Reservation',{'PENDING','CONFIRMED','REJECTED','CANCELLED','IN_USE','COMPLETED','EXPIRED'}),('WorkOrder',{'OPEN','IN_PROGRESS','AWAITING_ACCEPTANCE','CLOSED'})]:
    values={k for k,v in next(f for f in models[model]['fields'] if f['name']=='status')['choices']}
    assert values==expected,(model,values)
print(f'分析契约通过：{len(cases)}规约、{len(messages)}内部协作、{len(identifiers)}消息、9组可编辑模型；静态检查不代替语义及运行验收。')
