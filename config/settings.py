import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# JSON local settings are generated locally; never committed. Environment wins.
import json
local_path = BASE_DIR / '.local' / 'settings.json'
LOCAL = json.loads(local_path.read_text(encoding='utf-8')) if local_path.exists() else {}
def env(name, default=None):
    return os.environ.get(name, LOCAL.get(name, default))

SECRET_KEY = env('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError('缺少 SECRET_KEY，请先运行 python scripts/configure.py')
DEBUG = str(env('DEBUG', '1')) == '1'
ALLOWED_HOSTS = env('ALLOWED_HOSTS', '127.0.0.1,localhost,testserver').split(',')
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
                  'django.contrib.messages', 'django.contrib.staticfiles', 'core']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request',
              'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': env('PGDATABASE', 'campus_lab'),
            'USER': env('PGUSER', 'campus'), 'PASSWORD': env('PGPASSWORD'),
            'HOST': env('PGHOST', '127.0.0.1'), 'PORT': env('PGPORT', '55432'), 'CONN_MAX_AGE': 0,
            'OPTIONS': {'connect_timeout': 5}}}
AUTH_USER_MODEL = 'core.User'
AUTHENTICATION_BACKENDS = ['core.identity.MockCampusBackend']
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SIMULATED_MESSAGE_MODE = env('SIMULATED_MESSAGE_MODE', 'success')
SESSION_COOKIE_SECURE = str(env('COOKIE_SECURE', '0')) == '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
