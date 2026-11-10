"""Render the current, code-derived ERD and data design reference PDF."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "models" / "schema.json"
MANIFEST_PATH = ROOT / "models" / "ERD" / "manifest.json"
INDEX_PATH = ROOT / "models" / "ERD" / "关系索引.csv"
DESTINATION = ROOT / "docs" / "ERD与数据设计说明.pdf"
TEMP_DIR = ROOT / "tmp" / "pdfs"

INK = colors.HexColor("#24384B")
TEAL = colors.HexColor("#356F69")
GOLD = colors.HexColor("#A87934")
BODY = colors.HexColor("#202829")
MUTED = colors.HexColor("#68757D")
GRID = colors.HexColor("#D9DFDE")
PALE = colors.HexColor("#F1F5F3")
PAPER = colors.HexColor("#FBFAF6")

FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")
if not FONT_PATH.exists():
    raise FileNotFoundError("需要 Windows 微软雅黑字体 C:/Windows/Fonts/msyh.ttc")
pdfmetrics.registerFont(TTFont("CampusCN", str(FONT_PATH), subfontIndex=0))

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT = RIGHT = 15 * mm
TOP = 19 * mm
BOTTOM = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT - RIGHT

styles = getSampleStyleSheet()
cover_kicker = ParagraphStyle(
    "CoverKicker", fontName="CampusCN", fontSize=10, leading=15,
    textColor=TEAL, alignment=TA_CENTER, spaceAfter=12,
)
cover_title = ParagraphStyle(
    "CoverTitle", fontName="CampusCN", fontSize=24, leading=36,
    textColor=INK, alignment=TA_CENTER, spaceAfter=16,
)
cover_subtitle = ParagraphStyle(
    "CoverSubtitle", fontName="CampusCN", fontSize=12, leading=20,
    textColor=MUTED, alignment=TA_CENTER, spaceAfter=8,
)
h1 = ParagraphStyle(
    "SectionHeading", fontName="CampusCN", fontSize=15, leading=22,
    textColor=INK, spaceBefore=9, spaceAfter=9, keepWithNext=True,
)
h2 = ParagraphStyle(
    "DomainHeading", fontName="CampusCN", fontSize=12, leading=18,
    textColor=TEAL, spaceBefore=8, spaceAfter=6, keepWithNext=True,
)
body = ParagraphStyle(
    "BodyCN", fontName="CampusCN", fontSize=9, leading=15,
    textColor=BODY, spaceAfter=6,
)
small = ParagraphStyle(
    "SmallCN", fontName="CampusCN", fontSize=7.5, leading=10.5,
    textColor=BODY, spaceAfter=1,
)
small_muted = ParagraphStyle(
    "SmallMutedCN", parent=small, textColor=MUTED,
)
table_head = ParagraphStyle(
    "TableHeadCN", fontName="CampusCN", fontSize=7.4, leading=10,
    textColor=colors.white, alignment=TA_LEFT,
)
caption = ParagraphStyle(
    "CaptionCN", fontName="CampusCN", fontSize=8, leading=11,
    textColor=MUTED, alignment=TA_CENTER, spaceBefore=3, spaceAfter=7,
)
callout = ParagraphStyle(
    "CalloutCN", fontName="CampusCN", fontSize=9, leading=15,
    textColor=INK, backColor=PAPER, borderColor=GRID, borderWidth=0.5,
    borderPadding=8, spaceBefore=4, spaceAfter=8,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def para(value: object, style=small) -> Paragraph:
    text = escape(str(value)).replace("\n", "<br/>")
    return Paragraph(text or "-", style)


def table(headers, rows, widths, *, repeat_rows=1, font_size=None):
    data = [[para(value, table_head) for value in headers]]
    data.extend([[para(value) for value in row] for row in rows])
    result = LongTable(
        data, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT",
        splitByRow=1,
    )
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return result


def on_page(canvas, document):
    canvas.saveState()
    if document.page > 1:
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(LEFT, PAGE_HEIGHT - 12 * mm, PAGE_WIDTH - RIGHT, PAGE_HEIGHT - 12 * mm)
        canvas.setFont("CampusCN", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(LEFT, PAGE_HEIGHT - 9 * mm, "系统分析与设计 / ERD与数据设计说明")
    canvas.setStrokeColor(GRID)
    canvas.line(LEFT, 12 * mm, PAGE_WIDTH - RIGHT, 12 * mm)
    canvas.setFont("CampusCN", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(LEFT, 8 * mm, "宋明俞 · 1602240221 · 信管1402")
    canvas.drawRightString(PAGE_WIDTH - RIGHT, 8 * mm, f"{document.page}")
    canvas.restoreState()


def field_constraints(field: dict) -> str:
    flags = []
    if field.get("pk"):
        flags.append("主键")
    if field.get("fk"):
        flags.append("外键")
    if field.get("one_to_one"):
        flags.append("一对一")
    if field.get("unique") and not field.get("pk"):
        flags.append("唯一")
    flags.append("可空" if field.get("null") else "非空")
    return " / ".join(flags)


def choices_text(field: dict) -> str:
    choices = field.get("choices") or []
    values = []
    for item in choices[:8]:
        if isinstance(item, (list, tuple)) and len(item) > 1:
            values.append(f"{item[0]}={item[1]}")
        else:
            values.append(str(item))
    if len(choices) > 8:
        values.append("…")
    return "; ".join(values)


def physical_constraints(model: dict) -> list[list[str]]:
    rows = []
    for item in model.get("constraints", []):
        rows.append([
            item.get("name", ""), item.get("type", ""),
            json.dumps(item.get("definition", item), ensure_ascii=False, sort_keys=True),
        ])
    for item in model.get("indexes", []):
        rows.append([
            item.get("name", ""), "索引",
            json.dumps(item, ensure_ascii=False, sort_keys=True),
        ])
    return rows


def build_story(schema: dict, manifest: dict, relation_rows: list[dict]) -> list:
    models = {model["model"]: model for model in schema["models"]}
    domain_titles = {
        "A": "身份与权限", "B": "资源与开放", "C": "预约与规则",
        "D": "现场使用", "E": "异常与维修", "F": "审计与通知",
        "G": "场地安全信息",
    }
    story = [Spacer(1, 29 * mm)]
    story += [
        para("校园实验教学中心 / 课程数据模型", cover_kicker),
        para("校园实验室设备预约与借用管理系统", cover_title),
        para("ERD 与数据设计说明", cover_subtitle),
        Spacer(1, 12 * mm),
        para("领域模型 · 关系结构 · 字段字典 · 数据约束", cover_subtitle),
        Spacer(1, 27 * mm),
        para("姓名：宋明俞　　学号：1602240221", cover_subtitle),
        para("班级：信管1402　　课程：系统分析与设计", cover_subtitle),
        para("模型版本 2.4　　2026年11月9日", cover_kicker),
        PageBreak(),
    ]

    story.append(para("一、范围与版本依据", h1))
    story.append(para(
        f"本说明以项目代码导出的物理模型为准，包含 {manifest['entity_count']} 个领域实体、"
        f"{manifest['field_count']} 个字段和 {manifest['relationship_count']} 条物理外键。"
        "模型描述课程演示系统的数据库结构，不证明目标学校已采用相同组织、制度、资源数量或安全规程。"
    ))
    story.append(para(
        "模型范围包括身份与管理范围、实验室管理单元、独立场地与设备、开放日历、预约与审批、"
        "设备和场地实际使用、异常与维修、审计与通知，以及场地安全规程版本与阅读确认。"
        "Django会话及框架内部授权组、迁移记录不列入业务实体。AuditEvent的entity_type与entity_id"
        "是逻辑引用，不能当作物理外键。"
    ))
    story.append(para(
        "课程新装种子数据为模拟示例：3个管理单元、6间场地、9项设备。它们不代表任何高校的真实资产。"
        "项目没有获授权的目标场地实拍照片；安全规程在取得责任岗位批准材料前保持待确认。", callout,
    ))
    domain_rows = []
    for domain in manifest["domains"]:
        code = domain["code"]
        domain_rows.append([
            f"{code} / {domain_titles.get(code, code)}",
            "、".join(domain["entities"]),
        ])
    story.append(para("表1　数据域与实体", caption))
    story.append(table(["数据域", "实体"], domain_rows, [115, CONTENT_WIDTH - 115]))

    story.append(para("二、核心数据职责与一致性", h1))
    responsibility = [
        ["实验室与空间", "Lab是管理单元；一个Lab可关联多项Resource。Resource.kind=ROOM时由RoomDetail保存位置、容量、类别和设施。房间可独立预约。"],
        ["设备与房间依赖", "EquipmentDetail与Resource一对一；可移动设备独立预约。固定设备可关联房间，候选时段和实际场地使用需同时满足关联限制。"],
        ["计划与事实", "Reservation记录预约计划；ApprovalDecision与ReservationReview分开记录初始决定和后续复核；Loan、ReturnRecord、RoomUse记录真实交接和使用事实。"],
        ["异常与维修", "Incident、IncidentResponse、Evidence保留报告、核实和证据；WorkOrder与WorkOrderEvent记录维修分派、执行和验收。维修完成不自动恢复资源状态。"],
        ["安全信息", "SafetyProcedure保存来源、版本、适用对象、生效与复核信息；SafetyAcknowledgement把用户确认关联到具体版本。阅读确认不等于培训、考核、门禁或现场检查。"],
        ["可用性与状态", "资源服务状态、实物状态、预约生命周期、指定时间可用性、资格、安全规程与通知投递分别表达，不能压成一个可用/不可用字段。"],
        ["责任与审计", "业务状态变化保留操作者、时间和前后状态；不同业务角色指向User但责任含义不同。历史凭据采用追加记录，不以新状态覆盖旧事实。"],
    ]
    story.append(table(["设计主题", "数据职责与规则边界"], responsibility, [105, CONTENT_WIDTH - 105]))

    for domain in manifest["domains"]:
        code = domain["code"]
        title = domain_titles.get(code, code)
        story.append(PageBreak())
        story.append(para(f"三、字段字典 / {code}：{title}", h1))
        story.append(para(
            "下列表格直接由当前schema.json与ERD关系索引生成。字段中文含义来自模型标签；"
            "主外键、可空和唯一标志描述存储结构。选择业务流程时仍须以需求规格和状态字典为准。"
        ))
        for entity in domain["entities"]:
            model = models[entity]
            story.append(para(
                f"{model.get('label', entity)} / {entity} / {model['table']}", h2,
            ))
            field_rows = []
            for field in model["fields"]:
                target = field.get("fk") or field.get("one_to_one") or ""
                meaning = field.get("label", field.get("name", ""))
                if target:
                    meaning += f"；目标 {target}"
                    if field.get("on_delete"):
                        meaning += f"；删除策略 {field['on_delete']}"
                if field.get("choices"):
                    meaning += f"；选项 {choices_text(field)}"
                field_rows.append([
                    f"{field.get('column', field.get('name', ''))} / {field.get('name', '')}",
                    field.get("type", ""), field_constraints(field), meaning,
                ])
            story.append(table(
                ["列名 / 字段名", "数据库类型", "键与可空", "业务标签、关联或枚举"],
                field_rows,
                [115, 83, 77, CONTENT_WIDTH - 275],
            ))
            constraints = physical_constraints(model)
            if constraints:
                story.append(para("表级约束与索引", caption))
                story.append(table(
                    ["名称", "类型", "定义"], constraints,
                    [100, 62, CONTENT_WIDTH - 162],
                ))

    story.append(PageBreak())
    story.append(para("四、物理关系索引", h1))
    story.append(para(
        "每条记录对应数据库中的实际外键。R编号与关系索引CSV和ERD一致。"
        "FK可空性描述子记录是否必须指定父记录；FK唯一性决定一个父对象最多关联一个或多个子对象。"
    ))
    story.append(table(
        ["编号", "子表字段", "父表字段", "子端可空", "子端唯一", "跨域共享"],
        [[
            row["id"], f"{row['child']}.{row['from_field']}",
            f"{row['parent']}.{row['to_field']}",
            "是" if row["null"] else "否",
            "是" if row["unique"] else "否",
            "是" if row["shared"] else "否",
        ] for row in relation_rows],
        [42, 132, 132, 58, 58, CONTENT_WIDTH - 422],
    ))

    story.append(PageBreak())
    story.append(para("五、数据约束与部署边界", h1))
    constraints_text = [
        "同一设备或场地的有效预约采用半开区间并发约束；待审核申请不占资源时间，获批时重新校验。",
        "场地预约人数和实际入场人数均由服务端与房间容量比较；房间容量必须由有权岗位维护。",
        "每日或每周开放时间当前仅建模一个连续窗口；如目标学校存在多段开放窗口，需先确认后再扩展模型。",
        "安全规程按版本保存来源、审核、生效和复核日期；历史版本及阅读记录不得被覆盖。",
        "证据附件通过受控存储和对象级授权读取；文件摘要用于校验完整性，不公开存储地址。",
        "通知业务记录、渠道投递记录和每次投递尝试分开保存；外部通道失败不得撤销已成功保存的业务。",
        "运行备份与恢复RPO/RTO仍是演示运行条件；不在本说明中将未完成的生产恢复演练标为通过。",
    ]
    for index, item in enumerate(constraints_text, 1):
        story.append(para(f"{index}. {item}"))
    story.append(para("版本来源与可复核材料", h2))
    sources = [
        "models/schema.json：由当前Django模型和迁移导出的字段、约束及索引。",
        "models/ERD/manifest.json与models/ERD/关系索引.csv：完整实体、关系编号、字段和图形布局。",
        "docs/specification.json：功能需求、业务规则、质量需求和验收场景。",
        "docs/逻辑整改/字段与状态字典.json、状态事件与规则字典.md：业务含义、可见范围及状态迁移。",
        "docs/案例研究与设计决定.md：公开案例事实、项目推论和适用边界；案例不能证明目标学校制度。",
    ]
    for item in sources:
        story.append(para("• " + item, small))

    story.append(PageBreak())
    story.append(para("附录、分域ERD图册", h1))
    story.append(para(
        "以下页面是与当前schema同版本生成的分域图像预览。用于打印和快速定位；"
        "完整可编辑矢量源、A4矢量图册和可搜索离线图谱见models目录。"
    ))
    for index, domain in enumerate(manifest["domains"], 1):
        story.append(PageBreak())
        image_path = ROOT / "models" / "ERD" / f"分域预览-{index}.png"
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        story.append(para(f"{domain['code']} / {domain_titles.get(domain['code'], domain['code'])}", h2))
        diagram = Image(str(image_path), width=CONTENT_WIDTH, height=235 * mm, kind="proportional")
        diagram.hAlign = "CENTER"
        story.append(diagram)
        story.append(para(
            "实体字段、外键目标与关系编号由当前模型导出；如需检索单个字段，请使用交互图谱或字段字典。",
            caption,
        ))
    return story


def main():
    schema = read_json(SCHEMA_PATH)
    manifest = read_json(MANIFEST_PATH)
    if len(schema["models"]) != manifest["entity_count"]:
        raise ValueError("schema与ERD实体数量不一致")
    if sum(len(model["fields"]) for model in schema["models"]) != manifest["field_count"]:
        raise ValueError("schema与ERD字段数量不一致")
    if len(manifest["relationships"]) != manifest["relationship_count"]:
        raise ValueError("ERD关系索引数量不一致")
    with INDEX_PATH.open(encoding="utf-8-sig", newline="") as stream:
        index_rows = list(csv.DictReader(stream))
    if len(index_rows) != manifest["relationship_count"]:
        raise ValueError("CSV关系索引数量不一致")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="data-design-", suffix=".pdf", dir=TEMP_DIR)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        document = SimpleDocTemplate(
            str(temp_path), pagesize=A4, leftMargin=LEFT, rightMargin=RIGHT,
            topMargin=TOP, bottomMargin=BOTTOM,
            title="Campus Lab Equipment Reservation and Loan Management System - ERD and Data Design",
            author="Song Mingyu",
            subject="Model 2.4; 38 entities; 327 fields; 68 physical foreign keys",
        )
        document.build(build_story(schema, manifest, manifest["relationships"]), onFirstPage=on_page, onLaterPages=on_page)
        from pypdf import PdfReader
        result = PdfReader(str(temp_path))
        if len(result.pages) < 35:
            raise ValueError(f"输出页数异常：{len(result.pages)}")
        text_content = "\n".join((page.extract_text() or "") for page in result.pages)
        for required in ("SafetyProcedure", "SafetyAcknowledgement", "R68", "模型版本 2.4"):
            if required not in text_content:
                raise ValueError(f"最终PDF缺少必需内容：{required}")
        os.replace(temp_path, DESTINATION)
        print(f"Rendered {len(result.pages)} pages: {manifest['entity_count']} entities / {manifest['field_count']} fields / {manifest['relationship_count']} relationships")
    finally:
        if temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    main()
