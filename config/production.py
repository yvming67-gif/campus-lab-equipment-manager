"""A single-node deployment profile; actual identity/provider acceptance is separate."""
import os
from django.core.exceptions import ImproperlyConfigured
from .settings import *

required=['DJANGO_SECRET_KEY','ALLOWED_HOSTS','CAMPUS_USE_EXTERNAL_DATABASE','PGDATABASE','PGUSER','PGPASSWORD','CAMPUS_IDENTITY_ADAPTER']
missing=[key for key in required if not os.environ.get(key)]
if missing:raise ImproperlyConfigured('生产配置缺少：'+', '.join(missing))
if os.environ['CAMPUS_USE_EXTERNAL_DATABASE']!='1':raise ImproperlyConfigured('生产配置必须显式使用外部数据库')
if os.environ['CAMPUS_IDENTITY_ADAPTER']=='MOCK':raise ImproperlyConfigured('生产环境不能使用模拟身份')
DEBUG=False
DEMO_MODE=False
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False
STATIC_ROOT=LOCAL/'static'
CSRF_TRUSTED_ORIGINS=[x.strip() for x in os.environ.get('CSRF_TRUSTED_ORIGINS','').split(',') if x.strip()]
if any(not value.startswith('https://') for value in CSRF_TRUSTED_ORIGINS):raise ImproperlyConfigured('正式CSRF来源必须使用HTTPS')
# Only configure proxy headers at a trusted reverse proxy; never trust arbitrary forwarded headers.
if os.environ.get('CAMPUS_TRUST_HTTPS_PROXY')=='1':SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
