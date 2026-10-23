"""Shared business-facing labels; stored event/channel codes remain unchanged."""
NOTIFICATION_LABELS = {
    "PENDING": "待处理", "PROCESSING": "投递中", "RETRY": "等待重试",
    "SENT": "已发送", "FAILED": "重试耗尽", "APPROVAL_REQUEST": "待审核申请",
    "AUTO_CONFIRM": "自动确认", "APPROVAL_RESULT": "审核结果", "CANCEL": "预约取消",
    "CHECKOUT": "设备借出", "RETURN": "设备归还", "ROOM_START": "场地开始",
    "ROOM_END": "场地结束", "INCIDENT": "资源异常", "INCIDENT_RESULT": "异常核实结果",
    "MAINTENANCE_IMPACT": "维护影响", "ACCEPTANCE": "维修验收", "DUE_SOON": "即将到期",
    "OVERDUE": "逾期提醒", "EXPIRE": "预约失效", "POLICY_IMPACT": "开放规则调整",
    "REQUEST_INFO": "补充材料要求", "PROVIDE_INFO": "补充说明",
    "ARRANGEMENT_IMPACT": "使用安排变化", "COORDINATE": "预约协调",
    "INCIDENT_RESPONSE": "异常确认与异议", "REASSIGN": "维修任务转派",
    "RECALL": "使用召回", "RECHECK": "预约复核", "WORK_CREATED": "维修任务创建",
    "WORK_PROGRESS": "维修处理进展", "APPROVAL_ESCALATION": "审批待办升级",
}
EVENT_LABELS = {
    "LOGIN": "身份登录", "SUBMIT": "提交申请", "CREATE": "提交申请",
    "AUTO_CONFIRM": "自动确认", "APPROVE": "审核通过", "REJECT": "审核驳回",
    "CANCEL": "取消预约", "CHECKOUT": "设备借出", "RETURN": "设备归还",
    "ROOM_START": "场地入场", "ROOM_END": "场地结束", "EXPIRE": "预约失效",
    "REQUEST_INFO": "要求补充材料", "PROVIDE_INFO": "补充申请说明",
    "COORDINATE": "协调预约", "IMPACT_REVIEW": "暂停交接待复核", "RECHECK": "复核预约",
    "REVIEW_ENDED": "结束待复核状态", "RECALL": "召回使用", "CALENDAR_RECALL": "开放变化召回",
    "MAINTENANCE_CANCEL": "维护协调取消", "RESOURCE_HOLD_CANCEL": "服务限制协调取消",
    "SAFETY_CONTACT": "安全事项联系", "REPORT_INCIDENT": "报告异常", "VERIFY_INCIDENT": "核实异常",
    "INCIDENT_RESPONSE": "追加确认、异议或复核", "INCIDENT_RESOLVED": "异常处置完成",
    "CREATE_WORK": "创建维修工单", "REASSIGN": "重新指派维修", "WORK_UPDATE": "更新维修进展",
    "WORK_ACCEPT": "验收维修结果", "WORK_REJECT": "维修验收未通过",
    "EVIDENCE_READ": "查看异常证据", "EVIDENCE_UPLOAD": "提交异常证据",
    "MANUAL_CONTACT": "登记人工联系", "AUTHORIZATION_CHANGE": "调整授权",
    "CALENDAR_EXCEPTION": "调整单日开放", "LAB_CREATE": "新增实验室", "MANUAL_HOLD": "调整服务限制",
    "OPENING_CHANGE": "调整周开放日历", "POLICY_CHANGE": "调整规则参数",
    "SERVICE_STATUS": "调整资源服务状态", "RESOURCE_CREATE": "建立资源档案",
    "RESOURCE_UPDATE": "调整资源档案",
}
WORK_STATE_LABELS = {"": "创建前", "OPEN": "待处理", "IN_PROGRESS": "处理中",
                     "AWAITING_ACCEPTANCE": "待验收", "CLOSED": "已关闭"}
CHANNEL_LABELS = {"IN_APP": "站内", "MOCK_EXTERNAL": "模拟外部", "EXTERNAL": "正式外部"}


def notification_label(value):
    return NOTIFICATION_LABELS.get(value, "业务进度")


def event_label(value):
    return EVENT_LABELS.get(value, "其他业务事件")


def work_state_label(value):
    return WORK_STATE_LABELS.get(value, "未知维修状态")


def channel_label(value):
    return CHANNEL_LABELS.get(value, "未知投递渠道")
