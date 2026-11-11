#!/usr/bin/env python3
import json, html, textwrap, xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
spec=json.loads((ROOT/'docs/specification.json').read_text('utf-8'))
flow=next(x for x in spec['business_flows'] if x['id']=='TO-SF')
W,H,S=1120,1260,2
out=ROOT/'models/图形源文件'
out.mkdir(parents=True,exist_ok=True)
puml=out/'图3-10-引入系统后的资源安全信息支持流程.puml'
svgp=out/'图3-10-引入系统后的资源安全信息支持流程.svg'
drawp=out/'图3-10-引入系统后的资源安全信息支持流程.drawio'
pngs=[ROOT/'docs/课程规范完善/图形预览/图3-10-引入系统后的资源安全信息支持流程.png',ROOT/'docs/逻辑整改/图形预览/图3-10-引入系统后的资源安全信息支持流程.png']
labels=['实验室管理员\n编制人','资源安全信息\n系统','实验室管理员\n复核人','场地使用人','安全规程版本／阅读回执／\n逻辑审计记录']
xs=[110,330,550,770,990]
rows=[
 ('TO-SF01',0,1,'选择一个授权管理单元或具体资源范围，提交草稿及正式来源',145),
 ('TO-SF02',1,4,'保存草稿并返回版本号；草稿仍仅管理人员可见',205),
 ('TO-SF02a',0,1,'编制人明确请求提交该草稿复核',265),
 ('TO-SF02b',1,4,'重新校验唯一范围、LabGrant、必填字段与日期，追加待审核状态和审计事实',325),
 ('TO-SF03',1,2,'仅向同LabGrant范围且非创建者管理员提供待审核项',385),
 ('TO-SF04',2,1,'核对管理单元/资源范围和正式来源后提交决定；系统不验证来源真伪',455),
 ('TO-SF05',1,4,'通过：只在所选范围发布新版本；保留旧版本与确认记录',545),
 ('TO-SF06',1,4,'退回：记录具体意见，当前已发布版本不变',625),
 ('TO-SF07',1,4,'保存退回状态、审核责任和意见；保留原历史',675),
 ('TO-SF07a',1,0,'向编制人发送发布或退回结果通知；通知失败不回滚审核决定',765),
 ('TO-SF07b',1,3,'仅通知受该范围影响的未来已确认预约申请人，并带预约编号；不自动取消',880),
 ('TO-SF08',0,1,'根据退回意见复制形成新草稿并递增版本；保留原退回事实',995),
 ('TO-SF09',3,1,'查看适用于当前资源的管理单元通用及资源专属版本；按版本确认（如要求）',1060),
 ('TO-SF09a',1,4,'重新校验版本仍有效且要求确认；记录用户、版本与时间并追加审计；重复确认返回原回执',1110),
 ('TO-SF09b',1,3,'向使用人返回确认结果、规程版本及确认时间',1150),
]
fontpath=Path(r'C:\Windows\Fonts\msyh.ttc')
# Vector source
parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
'<defs><marker id="arr" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L9,3.5 L0,7 z" fill="#315b55"/></marker></defs>',
'<rect width="1120" height="1260" fill="#fffdf9"/>',
'<text x="560" y="30" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="19" font-weight="700" fill="#24384b">引入系统后的资源安全信息支持流程</text>']
for i,(x,label) in enumerate(zip(xs,labels)):
    parts.append(f'<rect x="{x-82}" y="52" width="164" height="54" rx="8" fill="#edf4f1" stroke="#5b857b" stroke-width="1.5"/>')
    for j,line in enumerate(label.split('\n')):
        parts.append(f'<text x="{x}" y="{75+j*19}" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="#24384b">{html.escape(line)}</text>')
    parts.append(f'<line x1="{x}" y1="106" x2="{x}" y2="1180" stroke="#8a9995" stroke-width="1.2" stroke-dasharray="5 5"/>')
# UML alt fragment around the human review branch
parts += ['<rect x="430" y="485" width="650" height="240" fill="none" stroke="#b36b42" stroke-width="1.6"/>',
'<rect x="430" y="485" width="650" height="28" fill="#f6eee8" stroke="#b36b42" stroke-width="1"/>',
'<text x="440" y="504" font-family="Microsoft YaHei,sans-serif" font-size="12" font-weight="700" fill="#8c4d2f">alt 复核通过 / 退回修订</text>',
'<line x1="430" y1="590" x2="1080" y2="590" stroke="#b36b42" stroke-width="1" stroke-dasharray="6 4"/>',
'<text x="440" y="530" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#8c4d2f">[通过]</text>',
'<text x="440" y="610" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#8c4d2f">[退回修订]</text>',
 '<rect x="430" y="810" width="650" height="100" fill="none" stroke="#547c72" stroke-width="1.4"/>',
'<rect x="430" y="810" width="650" height="26" fill="#edf4f1" stroke="#547c72" stroke-width="1"/>',
'<text x="440" y="828" font-family="Microsoft YaHei,sans-serif" font-size="11" font-weight="700" fill="#315b55">opt 已发布、要求确认且存在未来已确认预约</text>',
'<rect x="110" y="925" width="440" height="100" fill="none" stroke="#547c72" stroke-width="1.2" stroke-dasharray="5 4"/>',
'<text x="120" y="943" font-family="Microsoft YaHei,sans-serif" font-size="10" font-weight="700" fill="#315b55">opt 复核退回</text>']
for ident,a,b,label,y in rows:
    x1,x2=xs[a],xs[b]
    start=min(x1,x2)+7 if x2>x1 else max(x1,x2)-7
    end=max(x1,x2)-8 if x2>x1 else min(x1,x2)+8
    direction=1 if x2>x1 else -1
    parts.append(f'<line x1="{start}" y1="{y}" x2="{end}" y2="{y}" stroke="#315b55" stroke-width="1.7" marker-end="url(#arr)"/>')
    mid=(start+end)/2
    wrapped=textwrap.wrap(f'{ident} {label}',width=30)
    parts.append(f'<rect x="{mid-138}" y="{y-25-len(wrapped)*12}" width="276" height="{len(wrapped)*13+5}" rx="4" fill="#fffdf9"/>')
    for j,line_text in enumerate(wrapped):
        parts.append(f'<text x="{mid}" y="{y-12-len(wrapped)*12+j*13}" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#24384b">{html.escape(line_text)}</text>')
parts += ['<text x="560" y="1170" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#6b7470">每个版本只关联一个管理单元或具体资源；资源专属内容不向关联设备继承；正式来源和校内岗位待组织确认</text>','</svg>']
svg=''.join(parts)
svgp.write_text(svg,encoding='utf-8')
# Draw.io editable model
mx=ET.Element('mxfile',{'host':'app.diagrams.net'})
diagram=ET.SubElement(mx,'diagram',{'name':'图3-10-引入系统后的资源安全信息支持流程'})
model=ET.SubElement(diagram,'mxGraphModel',{'dx':str(W),'dy':str(H),'pageWidth':str(W),'pageHeight':str(H),'grid':'1','guides':'1','tooltips':'1','connect':'1','arrows':'1','fold':'1','page':'1','pageScale':'1','math':'0','shadow':'0'})
root=ET.SubElement(model,'root');ET.SubElement(root,'mxCell',{'id':'0'});ET.SubElement(root,'mxCell',{'id':'1','parent':'0'})
def cell(i,value,style,x,y,w,h,vertex=True):
    c=ET.SubElement(root,'mxCell',{'id':i,'value':value,'style':style,'vertex':'1' if vertex else '0','parent':'1'})
    ET.SubElement(c,'mxGeometry',{'x':str(x),'y':str(y),'width':str(w),'height':str(h),'as':'geometry'})
cell('title','图3-10 引入系统后的资源安全信息支持流程','text;html=1;align=center;verticalAlign=middle;fontFamily=Microsoft YaHei;fontSize=18;fontStyle=1;fontColor=#24384b;strokeColor=none;fillColor=none;',300,12,520,28)
for i,(x,label) in enumerate(zip(xs,labels)):
    cid=f'l{i}'
    cell(cid,html.escape(label).replace('\n','<br>'),'rounded=1;whiteSpace=wrap;html=1;fontFamily=Microsoft YaHei;fontSize=12;strokeColor=#5b857b;fillColor=#edf4f1;fontColor=#24384b;',x-82,52,164,54)
    cell(f'life{i}','',f'shape=line;strokeColor=#8a9995;dashed=1;html=1;',x,106,1,1034)
# alt frame and branch divider
cell('alt','alt 复核通过 / 退回修订','rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;fontFamily=Microsoft YaHei;fontSize=12;fontStyle=1;strokeColor=#b36b42;fillColor=none;fontColor=#8c4d2f;',430,485,650,240)
cell('branch','','shape=line;strokeColor=#b36b42;dashed=1;html=1;',430,590,650,1)
cell('opt_notice','opt 已发布、要求确认且存在未来已确认预约','rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;fontFamily=Microsoft YaHei;fontSize=11;fontStyle=1;strokeColor=#547c72;fillColor=none;fontColor=#315b55;',430,810,650,100)
cell('opt_revision','opt 复核退回','rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacingTop=4;spacingLeft=8;fontFamily=Microsoft YaHei;fontSize=10;fontStyle=1;strokeColor=#547c72;fillColor=none;fontColor=#315b55;',110,925,440,100)
for ident,a,b,label,y in rows:
    x1,x2=xs[a],xs[b]
    src=f'l{a}';dst=f'l{b}'
    edge=ET.SubElement(root,'mxCell',{'id':ident,'value':html.escape(label),'style':'edgeStyle=none;rounded=0;html=1;endArrow=classic;endFill=1;strokeColor=#315b55;fontFamily=Microsoft YaHei;fontSize=11;fontColor=#24384b;labelBackgroundColor=#fffdf9;','edge':'1','parent':'1','source':src,'target':dst})
    geo=ET.SubElement(edge,'mxGeometry',{'relative':'1','as':'geometry'})
    ET.SubElement(geo,'mxPoint',{'x':str(x1),'y':str(y),'as':'sourcePoint'})
    ET.SubElement(geo,'mxPoint',{'x':str(x2),'y':str(y),'as':'targetPoint'})
cell('note','每个版本只关联一个管理单元或具体资源；资源专属内容不向关联设备继承；正式来源和校内岗位待组织确认','text;html=1;align=center;verticalAlign=middle;fontFamily=Microsoft YaHei;fontSize=11;fontColor=#6b7470;strokeColor=none;fillColor=none;',120,1215,880,22)
ET.indent(mx,space=' ');drawp.write_text(ET.tostring(mx,encoding='unicode',xml_declaration=True),encoding='utf-8')
# PlantUML source
lines=['@startuml','title 图3-10 引入系统后的资源安全信息支持流程','skinparam defaultFontName Microsoft YaHei','skinparam sequenceArrowColor #315b55','skinparam participantBackgroundColor #edf4f1','participant "实验室管理员\\n编制人" as Author','participant "资源安全信息系统" as System','participant "实验室管理员\\n复核人" as Reviewer','actor "场地使用人" as User','database "安全规程版本／阅读回执／\\n逻辑审计记录" as Record',
'Author -> System: TO-SF01 选择唯一管理单元/资源范围并登记来源材料','System -> Record: TO-SF02 保存草稿并返回版本号','Author -> System: TO-SF02a 请求提交复核','System -> Record: TO-SF02b 重新校验并追加待审核状态与审计','System -> Reviewer: TO-SF03 向同范围且非创建者管理员提供待审核项','Reviewer -> System: TO-SF04 人工核对来源、范围并填写意见','alt 复核通过','System -> Record: TO-SF05 发布版本并保留旧版本和确认历史','else 退回修订','System -> Record: TO-SF06 记录具体退回意见，现行版本不变','System -> Record: TO-SF07 追加退回状态、责任与历史','end','System -> Author: TO-SF07a 通知编制人处理结果；失败不回滚决定','opt 已发布、要求阅读确认且有未来已确认预约','System -> User: TO-SF07b 仅通知受该范围影响的未来已确认预约申请人，并带预约编号；不自动取消','end','opt 复核退回','Author -> System: TO-SF08 复制退回版本形成新草稿并递增版本','end','User -> System: TO-SF09 查看适用于当前资源的管理单元通用及资源专属版本；按版本确认（如要求）','System -> Record: TO-SF09a 重新校验版本仍有效且要求确认；记录用户、版本、时间与审计；重复确认返回原回执','System --> User: TO-SF09b 返回确认结果、规程版本和确认时间','note over Author,Reviewer: 每个版本只关联一个管理单元或具体资源；资源专属内容不向关联设备继承；正式来源和校内岗位待组织确认','@enduml']
puml.write_text('\n'.join(lines)+'\n',encoding='utf-8')
# High-resolution PNG using the same layout
im=Image.new('RGB',(W*S,H*S),(255,253,249));d=ImageDraw.Draw(im)
def ft(sz):return ImageFont.truetype(str(fontpath),sz*S)
def txt_center(text,x,y,font,fill,anchor='mm'):
    d.multiline_text((x*S,y*S),text,font=font,fill=fill,anchor=anchor,align='center',spacing=2*S)
def line(x1,y1,x2,y2,fill,width=1,dash=False):
    if dash:
        import math
        length=math.hypot(x2-x1,y2-y1);steps=max(1,int(length/8))
        for i in range(0,steps,2):
            a=i/steps;b=min(i+1,steps)/steps
            d.line((int((x1+(x2-x1)*a)*S),int((y1+(y2-y1)*a)*S),int((x1+(x2-x1)*b)*S),int((y1+(y2-y1)*b)*S)),fill=fill,width=width*S)
    else:d.line((x1*S,y1*S,x2*S,y2*S),fill=fill,width=width*S)
d.text((W*S//2,30*S),'引入系统后的资源安全信息支持流程',font=ft(18),fill='#24384b',anchor='mm')
for i,(x,label) in enumerate(zip(xs,labels)):
    d.rounded_rectangle(((x-82)*S,52*S,(x+82)*S,106*S),radius=8*S,fill='#edf4f1',outline='#5b857b',width=2*S)
    txt_center(label,x,79,ft(12),'#24384b')
    line(x,106,x,1180,'#8a9995',1,True)
# alt frame
d.rectangle((433*S,485*S,1078*S,725*S),outline='#b36b42',width=2*S)
d.rectangle((433*S,485*S,1078*S,513*S),fill='#f6eee8',outline='#b36b42',width=2*S)
d.text((443*S,499*S),'alt 复核通过 / 退回修订',font=ft(11),fill='#8c4d2f',anchor='lm')
line(433,590,1078,590,'#b36b42',1,True)
d.text((442*S,530*S),'[通过]',font=ft(10),fill='#8c4d2f',anchor='mm')
d.text((442*S,610*S),'[退回修订]',font=ft(10),fill='#8c4d2f',anchor='mm')
for x1,y1,x2,y2,label in [(433,810,1078,910,'opt 已发布、要求确认且存在未来已确认预约'),(110,925,550,1025,'opt 复核退回')]:
    d.rectangle((x1*S,y1*S,x2*S,y2*S),outline='#547c72',width=2*S)
    d.rectangle((x1*S,y1*S,x2*S,(y1+26)*S),fill='#edf4f1',outline='#547c72',width=2*S)
    d.text(((x1+10)*S,(y1+13)*S),label,font=ft(10),fill='#315b55',anchor='lm')
for ident,a,b,label,y in rows:
    x1,x2=xs[a],xs[b];start=min(x1,x2)+8 if x2>x1 else max(x1,x2)-8;end=max(x1,x2)-9 if x2>x1 else min(x1,x2)+9
    line(start,y,end,y,'#315b55',2)
    # arrow head
    direction=1 if end>start else -1
    d.polygon([(end*S,y*S),((end-8*direction)*S,(y-4)*S),((end-8*direction)*S,(y+4)*S)],fill='#315b55')
    mid=(start+end)/2
    wrapped='\n'.join(textwrap.wrap(f'{ident} {label}',width=32))
    line_count=wrapped.count('\n')+1
    center_y=y-12-(line_count-1)*6
    box_width=min(600,max(180,max(len(t) for t in wrapped.splitlines())*12+18))
    box_height=line_count*14+8
    d.rounded_rectangle((int(mid*S-box_width*S/2),int(center_y*S-box_height*S/2),int(mid*S+box_width*S/2),int(center_y*S+box_height*S/2)),radius=3*S,fill='#fffdf9')
    txt_center(wrapped,mid,center_y,ft(10),'#24384b')
d.text((560*S,1220*S),'每个版本只关联一个管理单元或具体资源；房间专属内容不向关联设备继承；正式来源与校内岗位待组织确认',font=ft(10),fill='#6b7470',anchor='mm')
for dst in pngs:
    dst.parent.mkdir(parents=True,exist_ok=True);im.save(dst,optimize=True)
print('generated',svgp,drawp,puml,*pngs)
