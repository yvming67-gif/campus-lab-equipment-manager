from django import template
register=template.Library()
LABELS={"PENDING":"待处理","PROCESSING":"投递中","RETRY":"等待重试","SENT":"已发送","FAILED":"重试耗尽","APPROVAL_REQUEST":"待审核申请","AUTO_CONFIRM":"自动确认","APPROVAL_RESULT":"审核结果","CANCEL":"预约取消","CHECKOUT":"设备借出","RETURN":"设备归还","ROOM_START":"场地开始","ROOM_END":"场地结束","INCIDENT":"资源异常","INCIDENT_RESULT":"异常核实结果","MAINTENANCE_IMPACT":"维护影响","ACCEPTANCE":"维修验收","DUE_SOON":"即将到期","OVERDUE":"逾期提醒","EXPIRE":"预约失效","POLICY_IMPACT":"开放规则调整","REQUEST_INFO":"补充材料要求","PROVIDE_INFO":"补充说明"}
@register.filter
def business_label(value):return LABELS.get(value,"业务进度")
