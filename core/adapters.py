"""Integration boundary. A disabled adapter is never a successful mock response."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def identity_adapter():
    path=getattr(settings,'CAMPUS_IDENTITY_ADAPTER','')
    if not path or path=='MOCK':raise ImproperlyConfigured('真实学校身份适配器未配置，不能退回模拟身份')
    return import_string(path)


def external_delivery(task):
    path=getattr(settings,'CAMPUS_MESSAGE_ADAPTER','')
    if not path:raise ImproperlyConfigured('真实消息渠道未配置，不能生成模拟送达')
    receipt=import_string(path)(idempotency_key=str(task.pk),recipient_id=task.notification.recipient_id,content=task.notification.content)
    if not isinstance(receipt,dict) or not receipt.get('accepted') or not receipt.get('receipt_id'):raise ValueError('通知渠道未返回有效接收凭据')
    return receipt
