"""每次HTTP请求或独立业务操作共享一个审计关联编号。"""
import uuid
import logging,time
from contextvars import ContextVar
from functools import wraps
correlation_id=ContextVar("business_request_id",default=None)
def correlated(func):
    @wraps(func)
    def call(*args,**kwargs):
        if correlation_id.get() is not None:return func(*args,**kwargs)
        token=correlation_id.set(uuid.uuid4())
        try:return func(*args,**kwargs)
        finally:correlation_id.reset(token)
    return call
class CorrelationMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        ident=uuid.uuid4();token=correlation_id.set(ident)
        started=time.monotonic()
        try:
            response=self.get_response(request);response["X-Request-ID"]=str(ident)
            logging.getLogger('campus.http').info('request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f',ident,request.method,request.path,response.status_code,(time.monotonic()-started)*1000)
            return response
        finally:correlation_id.reset(token)
