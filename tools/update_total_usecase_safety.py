#!/usr/bin/env python3
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'models/图形源文件/图4-1-系统总用例图.drawio'
svgp=source.with_suffix('.svg')
preview=ROOT/'docs/逻辑整改/图形预览/图4-1-系统总用例图.png'
tree=ET.parse(source);root=tree.getroot()
model=root.find('.//mxGraphModel')
model.set('dx','507');model.set('dy','840');model.set('pageHeight','840')
cells={c.get('id'):c for c in root.iter('mxCell') if c.get('id')}
boundary=cells['n0'].find('mxGeometry');boundary.set('height','835')
if 'n42' not in cells:
    def add_vertex(i,value,style,x,y,w,h):
        c=ET.SubElement(root.find('.//root'),'mxCell',{'id':i,'parent':'1','vertex':'1','value':value,'style':style})
        ET.SubElement(c,'mxGeometry',{'x':str(x),'y':str(y),'width':str(w),'height':str(h),'as':'geometry'})
    def add_edge(i,sourceid,targetid):
        c=ET.SubElement(root.find('.//root'),'mxCell',{'id':i,'parent':'1','edge':'1','source':sourceid,'target':targetid,
           'style':'edgeStyle=none;rounded=0;endArrow=none;dashed=0;strokeColor=#4b716b;exitX=1;exitY=0.5;entryX=0;entryY=0.5;'})
        ET.SubElement(c,'mxGeometry',{'relative':'1','as':'geometry'})
    add_vertex('n42','安全信息','rounded=0;strokeColor=#b7c3c0;fillColor=#eef3ef;whiteSpace=wrap;html=1;fontFamily=Microsoft YaHei;fontSize=9.5;fontColor=#24384b;',89,710,330,18)
    add_vertex('UC-28','UC-28<br>编制并提交场地安全信息','ellipse;strokeColor=#356f69;fillColor=#eef3ef;whiteSpace=wrap;html=1;fontFamily=Microsoft YaHei;fontSize=8.2;fontColor=#2a3436;',104,738,138,32)
    add_vertex('UC-29','UC-29<br>复核并发布场地安全信息','ellipse;strokeColor=#356f69;fillColor=#eef3ef;whiteSpace=wrap;html=1;fontFamily=Microsoft YaHei;fontSize=7.8;fontColor=#2a3436;',104,778,138,32)
    add_vertex('n43','实验室管理员','shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;fontFamily=Microsoft YaHei;fontSize=8;fontColor=#2a3436;',12,744,32,42)
    add_edge('assoc-safety-author','n43','UC-28');add_edge('assoc-safety-reviewer','n43','UC-29')
    # A separate point route keeps the second association outside the system boundary and away from use-case nodes.
    edge=next(c for c in root.iter('mxCell') if c.get('id')=='assoc-safety-reviewer')
    st=edge.get('style','')+'edgeStyle=orthogonalEdgeStyle;rounded=0;jettySize=auto;orthogonalLoop=1;'
    edge.set('style',st)
    g=edge.find('mxGeometry')
    ET.SubElement(g,'Array',{'as':'points'})
    pt=ET.SubElement(g.find('Array'),'mxPoint',{'x':'58','y':'794'})
ET.indent(root,space='  ')
ET.register_namespace('', '')
tree.write(source,encoding='utf-8',xml_declaration=True)
# Extend existing SVG and preserve its original rendered nodes and association paths.
svg=svgp.read_text('utf-8')
svg=svg.replace('height="720" viewBox="0 0 507 720"','height="840" viewBox="0 0 507 840"').replace('<rect x="85" y="4" width="338" height="715"','<rect x="85" y="4" width="338" height="835"')
if 'UC-28' not in svg:
    extra='''<rect x="89" y="710" width="330" height="18" fill="#eef3ef" stroke="#b7c3c0"/><text x="254" y="722.325" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="9.5" fill="#24384b">安全信息</text>
<ellipse cx="173" cy="754" rx="69" ry="16" fill="#eef3ef" stroke="#356f69"/><text x="173" y="751.15" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8.2" fill="#2a3436">UC-28</text><text x="173" y="763.15" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8.2" fill="#2a3436">编制并提交场地安全信息</text>
<ellipse cx="173" cy="794" rx="69" ry="16" fill="#eef3ef" stroke="#356f69"/><text x="173" y="791.15" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7.8" fill="#2a3436">UC-29</text><text x="173" y="803.15" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7.8" fill="#2a3436">复核并发布场地安全信息</text>
<g stroke="#536872" stroke-width="1" fill="none"><circle cx="28" cy="748" r="3"/><path d="M28 751v8m-6-4h12m-6 4-5 6m5-6 5 6"/></g><text x="28" y="779" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7.6" fill="#2a3436">实验室管理员</text>
<path d="M44 756H104M44 762H58V794H104" fill="none" stroke="#4b716b" stroke-width="0.8"/>'''
    svg=svg.replace('</svg>',extra+'</svg>')
svgp.write_text(svg,'utf-8')
# Make a crisp 3x PNG preview from the already rendered base and extend its canvas.
im=Image.open(preview).convert('RGB')
S=3;new=Image.new('RGB',(507*S,840*S),'white');new.paste(im,(0,0))
d=ImageDraw.Draw(new);font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',24)
def pxbox(x,y,w,h,fill='white',outline=None,width=1):
    d.rectangle((x*S,y*S,(x+w)*S,(y+h)*S),fill=fill,outline=outline,width=width*S if outline else 1)
# erase old boundary bottom edge, then redraw the longer system boundary.
d.rectangle((84*S,716*S,424*S,722*S),fill='white')
d.line((85*S,4*S,85*S,839*S),fill='#24384b',width=3);d.line((423*S,4*S,423*S,839*S),fill='#24384b',width=3);d.line((85*S,839*S,423*S,839*S),fill='#24384b',width=3)
pxbox(89,710,330,18,'#eef3ef','#b7c3c0')
d.text((254*S,719*S),'安全信息',font=font,fill='#24384b',anchor='mm')
for cy,label,num,fs in [(754,'编制并提交场地安全信息','UC-28',22),(794,'复核并发布场地安全信息','UC-29',21)]:
    d.ellipse(((104)*S,(cy-16)*S,(242)*S,(cy+16)*S),fill='#eef3ef',outline='#356f69',width=3)
    d.text((173*S,(cy-5)*S),num,font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',24),fill='#2a3436',anchor='mm')
    d.text((173*S,(cy+7)*S),label,font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',fs),fill='#2a3436',anchor='mm')
# actor figure and associations (same actor role; author/reviewer are distinct accounts in sequence flow)
d.ellipse((25*S,745*S,31*S,751*S),outline='#536872',width=3);d.line((28*S,751*S,28*S,759*S),fill='#536872',width=3);d.line((22*S,755*S,34*S,755*S),fill='#536872',width=3);d.line((28*S,759*S,23*S,765*S),fill='#536872',width=3);d.line((28*S,759*S,33*S,765*S),fill='#536872',width=3)
d.text((28*S,779*S),'实验室管理员',font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',22),fill='#2a3436',anchor='mm')
d.line((44*S,756*S,104*S,756*S),fill='#4b716b',width=2);d.line((44*S,762*S,58*S,762*S,58*S,794*S,104*S,794*S),fill='#4b716b',width=2)
new.save(preview,optimize=True)
print('updated total use-case UML and preview')
