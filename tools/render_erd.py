"""Render the complete actual domain schema to vector ERD, editable draw.io and A4 atlas.
Optional authoring dependency: pip install -r requirements-erd.txt
Business runtime and database schema are not modified.
"""
import csv,hashlib,heapq,html,json,math,os,re
from pathlib import Path
from xml.etree.ElementTree import Element,SubElement,ElementTree
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4,A0,landscape
ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/"models";DEST=MODEL/"ERD";SOURCE=MODEL/"图形源文件"
INK="#263136";MUTED="#69737a";LINE="#bdc5c8";BLUE="#287a9a";GOLD="#ad842b";PAPER="#f5f5f2"
DOMAINS=[
 ("A","身份与权限","共享身份、岗位和管理范围",["User","Role","UserRole","LabGrant","QualificationType","UserQualification"],"#e8edf2","#526c83",[["User","Role","LabGrant"],["UserRole","QualificationType","UserQualification"]]),
 ("B","资源与开放","资源主档、类型明细和日历",["Lab","Resource","EquipmentDetail","RoomDetail","Accessory","OpeningPeriod"],"#e5eee7","#52765f",[["Lab","OpeningPeriod","Accessory"],["Resource","EquipmentDetail","RoomDetail"]]),
 ("C","预约与规则","申请、最终决定和规则版本",["Reservation","ApprovalDecision","ReviewNote","Policy","PolicyRevision"],"#f3eedc","#8d7948",[["Reservation","ApprovalDecision"],["Policy","PolicyRevision","ReviewNote"]]),
 ("D","现场使用","设备交接与场地实际使用",["Loan","ReturnRecord","HandoverItem","RoomUse"],"#e6eef5","#50778c",[["Loan","ReturnRecord"],["RoomUse","HandoverItem"]]),
 ("E","异常与维修","证据、工单和独立验收",["Incident","Evidence","WorkOrder","WorkOrderEvent"],"#f3e8e1","#936c56",[["Incident","Evidence"],["WorkOrder","WorkOrderEvent"]]),
 ("F","审计与通知","状态区间和可追踪投递",["AuditEvent","ServicePeriod","Notification","Delivery","DeliveryAttempt","MockReceipt"],"#ece9f2","#776785",[["AuditEvent","ServicePeriod","MockReceipt"],["Notification","Delivery","DeliveryAttempt"]])]
BY_DOMAIN={d[0]:d for d in DOMAINS}
SCHEMA=json.loads((MODEL/"schema.json").read_text("utf-8"));BY={m["model"]:m for m in SCHEMA["models"]};TABLE={m["table"]:m for m in SCHEMA["models"]}
CODES={n:d[0]+str(i+1).zfill(2) for d in DOMAINS for i,n in enumerate(d[3])}
HOME={n:d[0] for d in DOMAINS for n in d[3]}
REL=[]
for i,e in enumerate(SCHEMA["relationships"],1):
 r=dict(e);r.update(id=f"R{i:02d}",child=TABLE[e["from_table"]]["model"],parent=TABLE[e["to_table"]]["model"]);REL.append(r)
REL_BY={(r["child"],r["from_field"]):r for r in REL}

def fonts():
    windows=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"
    cjk=Path(os.environ.get("CAMPUS_CJK_FONT",str(windows/"msyh.ttc")))
    if not cjk.exists():raise SystemExit("Set CAMPUS_CJK_FONT to an embeddable Chinese TTF/TTC before rendering.")
    pdfmetrics.registerFont(TTFont("ERDCJK",str(cjk),subfontIndex=0))
    for name,file in [("ERDMono","consola.ttf"),("ERDMonoBold","consolab.ttf"),("ERDSans","arial.ttf"),("ERDBold","arialbd.ttf")]:
        candidate=windows/file
        if candidate.exists():pdfmetrics.registerFont(TTFont(name,str(candidate)))
        else:pdfmetrics.registerFont(TTFont(name,str(cjk),subfontIndex=0))

def field_order(model):
    return sorted(model["fields"],key=lambda f:0 if f["pk"] else 1 if f["fk"] else 2 if f["unique"] else 3)

def short_type(t):
    return t.replace("timestamp with time zone","timestamptz").replace("character varying","varchar").replace("double precision","float8")

def color(v):return colors.HexColor(v)

class Card:
    def __init__(self,name,x,y,w,small=False):
        self.name=name;self.model=BY[name];self.x=x;self.y=y;self.w=w;self.small=small
        self.header=41 if small else 58;self.row=12 if small else 17;self.ref=8.5 if small else 11
        self.pad=5 if small else 8;self.footer=13 if small else 17
        self.rows=[];offset=self.header+self.pad
        for f in field_order(self.model):
            height=self.row+(self.ref if f["fk"] else 0)
            self.rows.append(dict(field=f,y=offset,h=height));offset+=height
        self.h=offset+self.footer
    @property
    def rect(self):return (self.x,self.y,self.w,self.h)
    def port(self,column,side):
        row=next(r for r in self.rows if r["field"]["column"]==column)
        return (self.x if side=="L" else self.x+self.w,self.y+row["y"]+self.row*.52,-1 if side=="L" else 1,0)

class Scene:
    def __init__(self,width,height,title,small=False):
        self.width=width;self.height=height;self.title=title;self.small=small;self.cards={};self.groups=[];self.edges=[];self.extra=[]
    def group(self,key,x,y,width,columns,gap=None):
        d=BY_DOMAIN[key];small=self.small;pad=10 if small else 35;cw=(width-2*pad-(55 if small else 80))/2
        header=38 if small else 62;gap=(18 if small else 26) if gap is None else gap
        bottom=y
        for col,names in enumerate(columns):
            yy=y+header
            for name in names:
                card=Card(name,x+pad+col*(cw+(55 if small else 80)),yy,cw,small);self.cards[name]=card;yy+=card.h+gap
            bottom=max(bottom,yy-gap)
        self.groups.append(dict(key=key,x=x,y=y,w=width,h=bottom-y+(20 if small else 28),color=d[4],accent=d[5],title=d[1],subtitle=d[2]))
    def route(self,include_shared=True):
        by=self.cards;links=[r for r in REL if r["child"] in by and r["parent"] in by]
        for r in links:
            r=dict(r)
            local=HOME[r["child"]]==HOME[r["parent"]]
            core=(r["child"],r["from_field"]) in {("Reservation","resource_id"),("Reservation","applicant_id"),("Loan","reservation_id"),("RoomUse","reservation_id"),("Incident","resource_id"),("Incident","reservation_id"),("ServicePeriod","resource_id"),("HandoverItem","accessory_id"),("EquipmentDetail","qualification_id"),("LabGrant","lab_id")}
            r["shared"]=not (local or core)
            if include_shared or not r["shared"]:self.edges.append(r)
        router=Router(self)
        self.edges.sort(key=lambda r:(r["shared"],abs(by[r["child"]].x-by[r["parent"]].x)+abs(by[r["child"]].y-by[r["parent"]].y)))
        for edge in self.edges:
            edge.update(router.connect(edge));router.reserve(edge)
        self.validate()
    def validate(self):
        for edge in self.edges:
            pts=edge["points"]
            for a,b in zip(pts,pts[1:]):
                assert abs(a[0]-b[0])<.01 or abs(a[1]-b[1])<.01,(edge["id"],"diagonal")
                for card in self.cards.values():
                    x,y,w,h=card.rect
                    hit=(abs(a[1]-b[1])<.01 and y+.2<a[1]<y+h-.2 and max(min(a[0],b[0]),x+.2)<min(max(a[0],b[0]),x+w-.2)) or (abs(a[0]-b[0])<.01 and x+.2<a[0]<x+w-.2 and max(min(a[1],b[1]),y+.2)<min(max(a[1],b[1]),y+h-.2))
                    assert not hit,(edge["id"],"connector crosses",card.name,a,b)

class Router:
    def __init__(self,scene):
        self.scene=scene;self.step=4 if scene.small else 8;self.used={};self.blocked=set();s=self.step
        rectangles=[c.rect for c in scene.cards.values()]
        rectangles += [(g["x"]+12,g["y"]+3,g["w"]-24,(26 if scene.small else 44)) for g in scene.groups]
        pad=3 if scene.small else 7
        for x,y,w,h in rectangles:
            for xx in range(math.floor((x-pad)/s),math.ceil((x+w+pad)/s)+1):
                for yy in range(math.floor((y-pad)/s),math.ceil((y+h+pad)/s)+1):self.blocked.add((xx,yy))
    def connect(self,r):
        a=self.scene.cards[r["child"]];b=self.scene.cards[r["parent"]];pk=next(f["column"] for f in b.model["fields"] if f["pk"])
        choices=[]
        for sa in ["L","R"]:
            for sb in ["L","R"]:
                pa=a.port(r["from_field"],sa);pb=b.port(pk,sb)
                distance=abs(pa[0]-pb[0])+abs(pa[1]-pb[1]);choices.append((distance,pa,pb))
        choices.sort(key=lambda item:item[0]);s=self.step;best=None
        for _,pa,pb in choices:
            reach=16 if self.scene.small else 28
            start=(round((pa[0]+pa[2]*reach)/s),round(pa[1]/s));end=(round((pb[0]+pb[2]*reach)/s),round(pb[1]/s))
            if start in self.blocked or end in self.blocked:continue
            queue=[(0,0,start[0],start[1],-1)];cost={(start[0],start[1],-1):0};previous={};finish=None
            while queue:
                _,g,x,y,di=heapq.heappop(queue);state=(x,y,di)
                if cost.get(state)!=g:continue
                if (x,y)==end:finish=state;break
                for nd,(dx,dy) in enumerate([(1,0),(0,1),(-1,0),(0,-1)]):
                    xx,yy=x+dx,y+dy
                    if (xx,yy) in self.blocked or xx<2 or yy<15 or xx*s>self.scene.width-8 or yy*s>self.scene.height-20:continue
                    # Dedicated orthogonal lanes; shared references may follow a muted bus.
                    penalty=self.used.get((xx,yy),0)*(3 if r["shared"] else 9)
                    ng=g+1+(5 if di not in [-1,nd] else 0)+penalty;ns=(xx,yy,nd)
                    if ng<cost.get(ns,1e30):cost[ns]=ng;previous[ns]=state;heapq.heappush(queue,(ng+abs(xx-end[0])+abs(yy-end[1]),ng,xx,yy,nd))
            if finish:
                points=[];cur=finish
                while True:
                    points.append((cur[0]*s,cur[1]*s))
                    if cur not in previous:break
                    cur=previous[cur]
                points.reverse();points=[pa[:2],(points[0][0],pa[1])]+points+[(points[-1][0],pb[1]),pb[:2]]
                clean=[]
                for point in points:
                    if clean and point==clean[-1]:continue
                    while len(clean)>1 and ((clean[-2][0]==clean[-1][0]==point[0]) or (clean[-2][1]==clean[-1][1]==point[1])):clean.pop()
                    clean.append(point)
                best=dict(points=clean,child_port=pa,parent_port=pb);break
        if best is None:raise RuntimeError("No safe connector route: "+r["id"])
        return best
    def reserve(self,r):
        for a,b in zip(r["points"],r["points"][1:]):
            n=max(1,math.ceil((abs(a[0]-b[0])+abs(a[1]-b[1]))/self.step))
            for i in range(n+1):
                p=(round((a[0]+(b[0]-a[0])*i/n)/self.step),round((a[1]+(b[1]-a[1])*i/n)/self.step));self.used[p]=self.used.get(p,0)+1


class PDFPaint:
    def __init__(self,c,height):self.c=c;self.height=height
    def group(self,*args,**kwargs):pass
    def end(self):pass
    def rect(self,x,y,w,h,fill="none",stroke="none",radius=0,width=.7,dash=False):
        c=self.c;c.setLineWidth(width);c.setDash(4,3) if dash else c.setDash()
        if fill!="none":c.setFillColor(color(fill))
        if stroke!="none":c.setStrokeColor(color(stroke))
        if radius:c.roundRect(x,self.height-y-h,w,h,radius,fill=fill!="none",stroke=stroke!="none")
        else:c.rect(x,self.height-y-h,w,h,fill=fill!="none",stroke=stroke!="none")
        c.setDash()
    def text(self,x,y,text,size=10,fill=INK,font="ERDSans",align="left"):
        c=self.c;c.setFont(font,size);c.setFillColor(color(fill));fn=c.drawString if align=="left" else c.drawRightString if align=="right" else c.drawCentredString;fn(x,self.height-y,text)
    def line(self,points,stroke=LINE,width=1,dash=False):
        c=self.c;c.setStrokeColor(color(stroke));c.setLineWidth(width);c.setDash(4,3) if dash else c.setDash();path=c.beginPath();path.moveTo(points[0][0],self.height-points[0][1])
        for x,y in points[1:]:path.lineTo(x,self.height-y)
        c.drawPath(path);c.setDash()
    def circle(self,x,y,r,stroke=LINE,fill="#ffffff",width=.8):
        c=self.c;c.setStrokeColor(color(stroke));c.setFillColor(color(fill));c.setLineWidth(width);c.circle(x,self.height-y,r,fill=1,stroke=1)

class SVGPaint:
    def __init__(self,scene):
        self.root=Element("svg",xmlns="http://www.w3.org/2000/svg",width=str(scene.width),height=str(scene.height),viewBox=f"0 0 {scene.width} {scene.height}",role="img")
        SubElement(self.root,"title").text=scene.title
        self.current=self.root;self.stack=[]
    def group(self,ident,cls="",**data):
        self.stack.append(self.current);attrs={"id":ident,"class":cls}
        attrs.update({"data-"+k:str(v) for k,v in data.items()});self.current=SubElement(self.current,"g",attrs)
    def end(self):self.current=self.stack.pop()
    def rect(self,x,y,w,h,fill="none",stroke="none",radius=0,width=.7,dash=False):
        attrs={"x":str(x),"y":str(y),"width":str(w),"height":str(h),"rx":str(radius),"fill":fill,"stroke":stroke,"stroke-width":str(width)}
        if dash:attrs["stroke-dasharray"]="4 3"
        return SubElement(self.current,"rect",attrs)
    def text(self,x,y,text,size=10,fill=INK,font="ERDSans",align="left"):
        family="Consolas,monospace" if "Mono" in font else "Microsoft YaHei,Noto Sans CJK SC,sans-serif" if font=="ERDCJK" else "Arial,Microsoft YaHei,sans-serif"
        attrs={"x":str(x),"y":str(y),"font-size":str(size),"fill":fill,"font-family":family,"text-anchor":{"left":"start","right":"end","center":"middle"}[align]}
        if "Bold" in font:attrs["font-weight"]="700"
        SubElement(self.current,"text",attrs).text=text
    def line(self,points,stroke=LINE,width=1,dash=False):
        attrs={"points":" ".join(f"{x},{y}" for x,y in points),"fill":"none","stroke":stroke,"stroke-width":str(width),"stroke-linejoin":"round"}
        if dash:attrs["stroke-dasharray"]="4 3"
        SubElement(self.current,"polyline",attrs)
    def circle(self,x,y,r,stroke=LINE,fill="#ffffff",width=.8):SubElement(self.current,"circle",cx=str(x),cy=str(y),r=str(r),fill=fill,stroke=stroke,**{"stroke-width":str(width)})

def key(p,x,y,scale=1):
    p.circle(x+2*scale,y-3*scale,2.2*scale,GOLD,"#fcf4d9",.8*scale)
    p.line([(x+3.5*scale,y-1.5*scale),(x+9*scale,y+4*scale),(x+11*scale,y+2*scale)],GOLD,1.3*scale)
    p.line([(x+7*scale,y+2*scale),(x+9*scale,y)],GOLD,1.2*scale)

def cardinal(p,port,many,optional,small=False):
    x,y,dx,dy=port;unit=.52 if small else .78
    def at(a,b=0):return (x+dx*a*unit-dy*b*unit,y+dy*a*unit+dx*b*unit)
    if many:
        for off in [-4,0,4]:p.line([at(1,off),at(9)],"#607987",.7 if small else 1)
    else:p.line([at(7,-4),at(7,4)],"#607987",.7 if small else 1)
    if optional:
        cx,cy=at(16);p.circle(cx,cy,2.7*unit,"#607987",PAPER,.65 if small else .9)
    else:p.line([at(15,-4),at(15,4)],"#607987",.7 if small else 1)

def edge_paint(scene,p,r):
    p.group("edge-"+r["id"],"relation shared-reference" if r["shared"] else "relation business-reference",relationship=r["id"])
    p.line(r["points"],"#b0b9bf" if r["shared"] else "#6b818c",.55 if scene.small else 1.05,r["shared"])
    cardinal(p,r["child_port"],not r["unique"],True,scene.small);cardinal(p,r["parent_port"],False,r["null"],scene.small);p.end()

def card_paint(scene,p,card):
    x,y,w,h=card.rect;small=card.small;d=BY_DOMAIN[HOME[card.name]];model=card.model
    p.group("entity-"+model["table"],"entity",model=card.name,table=model["table"],domain=HOME[card.name],code=CODES[card.name])
    p.rect(x,y,w,h,"#ffffff","#78888e",2 if small else 3,.6 if small else .9)
    p.rect(x,y,w,2 if small else 3,d[5])
    p.text(x+9,y+(12 if small else 18),card.name,9.6 if small else 15.3,INK,"ERDMonoBold")
    p.text(x+w-8,y+(12 if small else 18),CODES[card.name],7.0 if small else 10.4,d[5],"ERDBold","right")
    p.text(x+9,y+(25 if small else 35),model["label"],8 if small else 11.8,d[5],"ERDCJK")
    p.text(x+9,y+(35 if small else 49),model["table"],6.7 if small else 8.8,MUTED,"ERDMono")
    p.line([(x,y+card.header),(x+w,y+card.header)],"#c4cccf",.5)
    for row in card.rows:
        f=row["field"];yy=y+row["y"];fs=8.6 if small else 10.8;baseline=yy+card.row*.73
        p.group("field-"+model["table"]+"-"+f["column"],"field",column=f["column"],pk=str(bool(f["pk"])).lower(),fk=str(bool(f["fk"])).lower())
        if f["pk"]:p.rect(x+.5,yy-1,w-1,row["h"],"#fcf8e9")
        elif f["fk"]:p.rect(x+.5,yy-1,w-1,row["h"],"#f4f9fb")
        if f["pk"]:key(p,x+7,baseline-2,.65 if small else .85)
        elif f["fk"]:p.text(x+6,baseline,"›",fs+1,BLUE,"ERDSans")
        p.text(x+(19 if small else 23),baseline,f["column"],fs,INK,"ERDMonoBold" if f["pk"] else "ERDMono")
        tag=short_type(f["type"])+( " ?" if f["null"] else "")+(" UQ" if f["unique"] and not f["pk"] else "")
        p.text(x+w-8,baseline,tag,6.8 if small else 8.0,MUTED,"ERDMono","right")
        # Native column names/types are retained; FK target and stable relation ID are explicit.
        if f["fk"]:
            r=REL_BY[(card.name,f["column"])];target=r["parent"]+"."+r["to_field"]
            p.text(x+(19 if small else 23),baseline+card.ref*.95,r["id"]+" → "+target,6.7 if small else 8.4,BLUE,"ERDCJK")
            p.text(x+w-8,baseline+card.ref*.95,CODES[r["parent"]],6.2 if small else 7.6,BLUE,"ERDSans","right")
        p.end()
    p.line([(x,y+h-card.footer),(x+w,y+h-card.footer)],"#d4dadb",.4)
    p.text(x+9,y+h-(4 if small else 6),f"{len(model['fields'])} fields",6.6 if small else 8.1,MUTED,"ERDMono")
    p.text(x+w-8,y+h-(4 if small else 6),"主键 / 外键 / 完整字段",6.1 if small else 8.0,MUTED,"ERDCJK","right")
    p.end()

def paint(scene,p,shared=False):
    p.rect(0,0,scene.width,scene.height,PAPER)
    for g in scene.groups:
        p.group("domain-"+g["key"],"domain")
        p.rect(g["x"],g["y"],g["w"],g["h"],g["color"],"#b8c3c6",13 if scene.small else 30,.6 if scene.small else .9,g["key"]=="A")
        p.text(g["x"]+12 if scene.small else g["x"]+25,g["y"]+(19 if scene.small else 30),g["key"]+"  "+g["title"],11 if scene.small else 20,g["accent"],"ERDCJK")
        p.text(g["x"]+g["w"]-(12 if scene.small else 25),g["y"]+(19 if scene.small else 30),str(len(BY_DOMAIN[g["key"]][3]))+" 实体",8 if scene.small else 12,g["accent"],"ERDCJK","right")
        if not scene.small:p.text(g["x"]+25,g["y"]+48,g["subtitle"],10.8,MUTED,"ERDCJK")
        p.end()
    if shared:
        p.group("shared-references","shared-layer")
        for r in scene.edges:
            if r["shared"]:edge_paint(scene,p,r)
        p.end()
    p.group("business-references","business-layer")
    for r in scene.edges:
        if not r["shared"]:edge_paint(scene,p,r)
    p.end()
    for card in scene.cards.values():card_paint(scene,p,card)
    if not scene.small:
        p.text(65,58,"校园实验室设备预约与借用管理系统",31,INK,"ERDCJK")
        p.text(65,93,"完整实体关系图谱",21,INK,"ERDCJK")
        p.text(65,121,"31个实体  /  245个字段  /  52条物理外键  /  PostgreSQL",12,MUTED,"ERDCJK")
        key(p,1640,56,1.15);p.text(1660,59,"主键",12,INK,"ERDCJK")
        p.text(1745,59,"›  R01 → Parent.id",12,BLUE,"ERDCJK");p.text(1970,59,"外键与目标实体",12,INK,"ERDCJK")
        p.text(2190,59,"? 可空  ·  UQ 单列唯一",12,MUTED,"ERDCJK")
        p.text(1640,86,"实线：域内及主业务外键；共享责任主体引用以字段目标完整展开。",11,MUTED,"ERDCJK")
        p.text(1640,111,"可编辑源文件可切换“共享引用”图层，检查全部52条连接。",11,MUTED,"ERDCJK")
        p.line([(65,143),(2735,143)],"#aeb9bd",1)
        p.text(65,2036,"阅读顺序：资源 → 预约 → 现场使用 → 异常维修；身份、规则与审计为共用支撑。",12,INK,"ERDCJK")
        p.text(65,2061,"实体代码用于跨域定位；R01—R52对应完整关系索引。实体、字段及外键由实际模型导出。",10.8,MUTED,"ERDCJK")
        p.text(2735,2061,"2026-10-19  /  ERD 1.0",10.5,MUTED,"ERDSans","right")
        p.text(1790,1960,"关键约束",12,INK,"ERDCJK")
        p.text(1790,1980,"待审核不占资源；确认与使用中的重叠区间互斥。",10.5,MUTED,"ERDCJK")
        p.text(1790,1998,"设备与场地分别记录实际使用；审计逻辑引用不伪装为外键。",10.5,MUTED,"ERDCJK")
    else:
        external=sorted({r["parent"] for r in REL if r["child"] in scene.cards and r["parent"] not in scene.cards},key=lambda n:CODES[n])
        p.text(10,scene.height-33,"域外引用："+" · ".join(CODES[n]+" "+n for n in external[:3]),7.1,BLUE,"ERDCJK")
        if len(external)>3:p.text(10,scene.height-22,"　　　　　"+" · ".join(CODES[n]+" "+n for n in external[3:]),7.1,BLUE,"ERDCJK")
        p.text(10,scene.height-8,"金色钥匙：PK　蓝色目标：FK　?：可空　UQ：单列唯一；圆圈表示0，竖线表示1，乌鸦脚表示多。",7.1,MUTED,"ERDCJK")

def make_scenes():
    master=Scene(2800,2080,"校园实验室设备预约与借用管理系统 · 完整ERD")
    positions={"A":(70,170),"B":(990,170),"C":(1910,170),"F":(70,1100),"E":(990,1120),"D":(1910,1120)}
    for d in DOMAINS:
        x,y=positions[d[0]];master.group(d[0],x,y,820,d[6])
    master.route()
    domains=[]
    for d in DOMAINS:
        s=Scene(507,650,d[0]+" · "+d[1],True);s.group(d[0],0,0,507,d[6]);s.height=s.groups[0]["h"]+44;s.route();domains.append(s)
    return master,domains

def drawio(scene,path):
    root=Element("mxfile",host="app.diagrams.net",type="device",version="26.0.0")
    diagram=SubElement(root,"diagram",id="campus-erd",name=scene.title)
    model=SubElement(diagram,"mxGraphModel",dx=str(scene.width),dy=str(scene.height),grid="1",gridSize="8",guides="1",tooltips="1",connect="1",arrows="1",fold="1",page="1",pageScale="1",pageWidth=str(scene.width),pageHeight=str(scene.height),math="0",shadow="0")
    cells=SubElement(model,"root");SubElement(cells,"mxCell",id="0")
    for ident,name,visible in [("domains","业务域","1"),("relations","业务外键","1"),("shared","共享引用（可切换；所有外键目标亦在字段行标注）","0"),("entities","完整字段实体","1"),("notes","图例与说明","1")]:SubElement(cells,"mxCell",id=ident,value=name,parent="0",visible=visible)
    def vertex(ident,value,x,y,w,h,style,parent="entities",**attrs):
        c=SubElement(cells,"mxCell",id=ident,value=value,style=style,vertex="1",parent=parent,**attrs);SubElement(c,"mxGeometry",x=str(x),y=str(y),width=str(w),height=str(h),**{"as":"geometry"});return c
    for g in scene.groups:
        vertex("domain-"+g["key"],g["key"]+"　"+g["title"],g["x"],g["y"],g["w"],g["h"],f"rounded=1;arcSize=5;whiteSpace=wrap;html=1;fillColor={g['color']};strokeColor=#b8c3c6;align=left;verticalAlign=top;spacingTop=9;spacingLeft=14;fontFamily=Microsoft YaHei;fontSize={'11' if scene.small else '20'};fontColor={g['accent']};dashed={'1' if g['key']=='A' else '0'};", "domains")
    for card in scene.cards.values():
        x,y,w,h=card.rect;m=card.model;ident="entity-"+m["table"];d=BY_DOMAIN[HOME[card.name]]
        vertex(ident,"",x,y,w,h,"rounded=0;fillColor=#ffffff;strokeColor=#78888e;strokeWidth=0.8;container=1;collapsible=0;recursiveResize=0;")
        value=f"<b>{html.escape(card.name)}</b>　{CODES[card.name]}<br/><font color='{d[5]}'>{html.escape(m['label'])}</font><br/><font color='#69737a' face='Consolas'>{html.escape(m['table'])}</font>"
        vertex(ident+"-title",value,0,0,w,card.header,"text;html=1;whiteSpace=wrap;fillColor=none;strokeColor=none;align=left;verticalAlign=middle;spacingLeft=9;fontSize="+("9" if scene.small else "13")+";fontFamily=Microsoft YaHei;",ident)
        for row in card.rows:
            f=row["field"];flags="PK " if f["pk"] else "FK " if f["fk"] else ""
            dtype=short_type(f["type"])+(" ?" if f["null"] else "")+(" UQ" if f["unique"] and not f["pk"] else "")
            value=f"<font face='Consolas'>{flags}{html.escape(f['column'])}</font> <font color='#69737a'>{html.escape(dtype)}</font>"
            if f["fk"]:
                r=REL_BY[(card.name,f["column"])];value+=f"<br/><font color='{BLUE}'>{r['id']} → {r['parent']}.{r['to_field']} · {CODES[r['parent']]}</font>"
            vertex("field-"+m["table"]+"-"+f["column"],value,1,row["y"]-1,w-2,row["h"],f"text;html=1;whiteSpace=wrap;fillColor={'#fcf8e9' if f['pk'] else '#f4f9fb' if f['fk'] else '#ffffff'};strokeColor=none;align=left;verticalAlign=middle;spacingLeft=7;fontSize={'8' if scene.small else '10'};fontFamily=Microsoft YaHei;",ident)
    for r in scene.edges:
        child=scene.cards[r["child"]];parent=scene.cards[r["parent"]];pk=r["to_field"]
        row=next(x for x in child.rows if x["field"]["column"]==r["from_field"])
        y=(child.row*.52+1)/row["h"]
        ex=0 if r["child_port"][2]<0 else 1;en=0 if r["parent_port"][2]<0 else 1
        source="field-"+child.model["table"]+"-"+r["from_field"];target="field-"+parent.model["table"]+"-"+pk
        start="ERzeroToOne" if r["unique"] else "ERzeroToMany";end="ERzeroToOne" if r["null"] else "ERone"
        style=f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeWidth={'0.5' if scene.small else '1'};strokeColor={'#b0b9bf' if r['shared'] else '#6b818c'};startArrow={start};endArrow={end};startFill=0;endFill=0;exitX={ex};exitY={y};exitDx=0;exitDy=0;entryX={en};entryY=0.52;entryDx=0;entryDy=0;dashed={int(r['shared'])};"
        c=SubElement(cells,"mxCell",id="edge-"+r["id"],value="",style=style,edge="1",parent="shared" if r["shared"] else "relations",source=source,target=target)
        geo=SubElement(c,"mxGeometry",relative="1",**{"as":"geometry"});pts=SubElement(geo,"Array",**{"as":"points"})
        for xx,yy in r["points"][1:-1]:SubElement(pts,"mxPoint",x=str(xx),y=str(yy))
    if not scene.small:
        vertex("title",scene.title+"<br/><font style='font-size:13px'>31 entities / 245 fields / 52 physical foreign keys</font>",65,30,1400,100,"text;html=1;align=left;fontSize=30;fontFamily=Microsoft YaHei;","notes")
        vertex("legend","金色PK / 蓝色FK目标 / ?可空 / UQ单列唯一<br/>共享责任引用在字段行完整标注，可切换共享引用图层。<br/>R01—R52见关系索引；审计entity_type+entity_id为逻辑引用。",1640,30,1080,100,"text;html=1;align=left;fontSize=16;fontFamily=Microsoft YaHei;","notes")
    path.parent.mkdir(parents=True,exist_ok=True);ElementTree(root).write(path,encoding="utf-8",xml_declaration=True)

def export_scene(scene,stem):
    p=SVGPaint(scene);paint(scene,p,True)
    group=p.root.find(".//g[@id='shared-references']")
    if group is not None:group.set("style","display:none")
    ElementTree(p.root).write(SOURCE/(stem+".svg"),encoding="utf-8",xml_declaration=True)
    drawio(scene,SOURCE/(stem+".drawio"))

DOMAIN_NOTES={
 "A":["角色决定可执行的操作，LabGrant另行限制实验室范围；二者共同形成权限。","授予者、被授权者分别引用User，不能合并为同一责任字段。"],
 "B":["Resource统一标识设备与场地；两类明细分别保存实物和空间特性。","资源状态不等于目标时段可用性，可用性还依赖日历、资格与预约承诺。"],
 "C":["待审核不占用时段；自动确认与人工批准均在事务中重新检查冲突。","最终决定最多一条，补充过程可多条；PolicyRevision保留参数变更版本。"],
 "D":["预约记录计划承诺；Loan、ReturnRecord、RoomUse分别记录实际使用事实。","配件交接保存名称、数量及状态快照，避免主档变化改写既有历史。"],
 "E":["异常与维修分开建模；同一异常最多一张开放工单，不合并不同故障。","维修提交结果与管理员独立验收分开记录，全部阻断解除后方可恢复。"],
 "F":["通知、渠道投递与每次尝试分层；失败重试不能回滚已保存业务。","AuditEvent的对象编号为逻辑引用；MockReceipt仅属于模拟消息适配器。"]}

def export_pdfs(master,domains):
    path=DEST/"完整ERD总图.pdf";size=landscape(A0);c=canvas.Canvas(str(path),pagesize=size,pageCompression=1)
    c.setTitle("校园实验室设备预约与借用管理系统：完整实体关系图谱");c.setAuthor("Campus Lab Equipment Manager")
    scale=min((size[0]-40)/master.width,(size[1]-40)/master.height);c.translate((size[0]-master.width*scale)/2,(size[1]-master.height*scale)/2);c.scale(scale,scale);paint(master,PDFPaint(c,master.height));c.showPage();c.save()
    c=canvas.Canvas(str(DEST/"分域ERD图册.pdf"),pagesize=A4,pageCompression=1);c.setTitle("校园实验室设备预约与借用管理系统：分域ERD图册");c.setAuthor("Campus Lab Equipment Manager")
    for i,s in enumerate(domains,1):
        w,h=A4;c.setFillColor(color(INK));c.setFont("ERDCJK",15);c.drawString(44,h-43,"实体关系图册 / "+s.title)
        c.setFillColor(color(MUTED));c.setFont("ERDCJK",8);c.drawString(44,h-64,"完整字段、主外键与目标实体；编号与完整ERD总图一致。")
        c.saveState();c.translate(44,h-87-s.height);paint(s,PDFPaint(c,s.height));c.restoreState()
        note_y=h-101-s.height
        if note_y>90:
            c.setFont("ERDCJK",9);c.setFillColor(color(BY_DOMAIN[s.groups[0]["key"]][5]));c.drawString(44,note_y,"建模要点")
            c.setFont("ERDCJK",8)
            for j,line in enumerate(DOMAIN_NOTES[s.groups[0]["key"]]):c.drawString(44,note_y-17-j*14,line)
        c.setFont("ERDCJK",8);c.setFillColor(color(MUTED));c.drawString(44,45,"Campus Lab Equipment Manager　·　PostgreSQL");c.drawRightString(w-44,45,f"{i} / {len(domains)}");c.showPage()
    c.save()

def export_index(master,domains):
    with (DEST/"关系索引.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.writer(f);writer.writerow(["关系编号","子实体代码","引用表","引用字段","父实体代码","目标表","目标字段","子记录对应父","父记录对应子","删除策略"])
        for r in REL:writer.writerow([r["id"],CODES[r["child"]],r["from_table"],r["from_field"],CODES[r["parent"]],r["to_table"],r["to_field"],"0..1" if r["null"] else "1","0..1" if r["unique"] else "0..N","PROTECT (ORM) / NO ACTION (SQL)"])
    info={"schema_hash_format":"canonical-json-sorted-compact-utf8","schema_sha256":hashlib.sha256(json.dumps(SCHEMA,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest(),"entity_count":len(master.cards),"field_count":sum(len(c.rows) for c in master.cards.values()),"relationship_count":len(master.edges),"format":"complete-field-vector-v1","shared_reference_default":"hidden; all targets and IDs remain inline","entities":[{"model":c.name,"code":CODES[c.name],"table":c.model["table"],"fields":[r["field"]["column"] for r in c.rows],"rect":c.rect} for c in master.cards.values()],"relationships":[{"id":r["id"],"child":r["child"],"from_field":r["from_field"],"parent":r["parent"],"to_field":r["to_field"],"null":r["null"],"unique":r["unique"],"shared":r["shared"],"points":r["points"]} for r in master.edges],"domains":[{"code":s.groups[0]["key"],"entities":list(s.cards)} for s in domains]}
    (DEST/"manifest.json").write_text(json.dumps(info,ensure_ascii=False,indent=2),"utf-8")

def export_viewer():
    svg=(SOURCE/"ERD-00-完整实体关系总图.svg").read_text("utf-8").split("?>",1)[-1]
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>校园实验室 · ERD图谱</title><style>
*{box-sizing:border-box}body{margin:0;background:#e8e9e6;color:#263136;font:14px "Microsoft YaHei",sans-serif}header{position:sticky;top:0;z-index:3;background:#fafaf6;border-bottom:1px solid #bdc5c8;padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}button,input{font:inherit;padding:6px 10px;border:1px solid #bdc5c8;border-radius:4px;background:white}input{width:245px}label{white-space:nowrap}.status{color:#69737a;font-size:12px}#viewport{overflow:auto;height:calc(100vh - 66px);padding:20px}#stage{width:2800px;transform-origin:top left;box-shadow:0 2px 20px #0001}.dim{opacity:.16}.hit>rect:first-child{stroke:#b26e36;stroke-width:4}#stage svg{display:block;width:100%;height:auto}
</style><header><strong>校园实验室 · 完整ERD</strong><button id="out">−</button><span id="zoom">100%</span><button id="in">＋</button><button id="fit">适合窗口</button><input id="search" aria-label="搜索实体或字段" placeholder="搜索实体、中文名或字段"><label><input type="checkbox" id="shared" style="width:auto">显示共享引用</label><span class="status" id="status">31实体 · 245字段 · 52物理外键 · 可离线查看</span></header><div id="viewport"><div id="stage">'''+svg+'''</div></div><script>
const stage=document.querySelector('#stage'),viewport=document.querySelector('#viewport');let scale=1;function setScale(s){scale=Math.max(.2,Math.min(2.5,s));stage.style.width=(2800*scale)+'px';document.querySelector('#zoom').textContent=Math.round(scale*100)+'%'}document.querySelector('#in').onclick=()=>setScale(scale*1.2);document.querySelector('#out').onclick=()=>setScale(scale/1.2);document.querySelector('#fit').onclick=()=>setScale((viewport.clientWidth-45)/2800);document.querySelector('#shared').onchange=e=>document.querySelector('#shared-references').style.display=e.target.checked?'':'none';document.querySelector('#search').oninput=e=>{const q=e.target.value.trim().toLowerCase();let found=0;document.querySelectorAll('.entity').forEach(n=>{let hit=q&&n.textContent.toLowerCase().includes(q);n.classList.toggle('dim',!!q&&!hit);n.classList.toggle('hit',!!hit);if(hit)found++});document.querySelector('#status').textContent=q?'匹配 '+found+' 个实体':'31实体 · 245字段 · 52物理外键 · 可离线查看'};document.querySelector('#fit').click();
</script></html>'''
    (DEST/"交互图谱.html").write_text(page,"utf-8")

def navigation_paint(p):
    p.rect(0,0,507,470,PAPER)
    for i,d in enumerate(DOMAINS):
        x=9+(i%2)*253;y=12+(i//2)*149;w=236;hh=136
        p.rect(x,y,w,hh,d[4],"#b8c3c6",9,.6)
        p.text(x+12,y+22,d[0]+"  "+d[1],11,d[5],"ERDCJK")
        for j,name in enumerate(d[3]):p.text(x+12,y+43+j*13,CODES[name]+"  "+name,8.4,INK,"ERDMono")
    p.text(10,465,"A—F为定位代码；本图是领域目录，实体关系及完整字段见后续分域图与大幅总图。",7,MUTED,"ERDCJK")

def navigation_export():
    scene=Scene(507,470,"业务数据域导航",True);p=SVGPaint(scene);navigation_paint(p)
    ElementTree(p.root).write(SOURCE/"图D-1-业务数据域导航.svg",encoding="utf-8",xml_declaration=True)
    root=Element("mxfile",host="app.diagrams.net",type="device");dia=SubElement(root,"diagram",name="业务数据域导航");model=SubElement(dia,"mxGraphModel",page="1",pageWidth="507",pageHeight="470");cells=SubElement(model,"root");SubElement(cells,"mxCell",id="0");SubElement(cells,"mxCell",id="1",parent="0")
    for i,d in enumerate(DOMAINS):
        value="<b>"+d[0]+" "+d[1]+"</b><br/>"+"<br/>".join(CODES[n]+" "+n for n in d[3]);c=SubElement(cells,"mxCell",id=d[0],value=value,vertex="1",parent="1",style="rounded=1;html=1;align=left;spacing=12;fontFamily=Microsoft YaHei;fontSize=9;fillColor="+d[4]+";strokeColor=#b8c3c6;");SubElement(c,"mxGeometry",x=str(9+(i%2)*253),y=str(12+(i//2)*149),width="236",height="136",**{"as":"geometry"})
    ElementTree(root).write(SOURCE/"图D-1-业务数据域导航.drawio",encoding="utf-8",xml_declaration=True)

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--preview",action="store_true",help="Also render PNG previews when PyMuPDF is installed.");args=parser.parse_args()
    fonts();DEST.mkdir(parents=True,exist_ok=True);SOURCE.mkdir(parents=True,exist_ok=True)
    master,domains=make_scenes();print("Routed complete schema: 31 entities / 245 fields / 52 relationships",flush=True)
    export_scene(master,"ERD-00-完整实体关系总图")
    for i,s in enumerate(domains,2):export_scene(s,f"图D-{i}-{s.groups[0]['title']}ERD")
    navigation_export();export_pdfs(master,domains);export_index(master,domains);export_viewer()
    if args.preview:
        import fitz
        for filename in ["完整ERD总图","分域ERD图册"]:
            doc=fitz.open(DEST/(filename+".pdf"))
            if filename=="完整ERD总图":doc[0].get_pixmap(matrix=fitz.Matrix(2800/doc[0].rect.width,2800/doc[0].rect.width)).save(DEST/"完整ERD预览.png")
            else:
                for i,page in enumerate(doc,1):page.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(DEST/f"分域预览-{i}.png")
    print("PDF / SVG / draw.io / relationship index / offline viewer written.",flush=True)

if __name__=="__main__":main()
