import os, json, secrets
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL = BASE_DIR / ".local"
LOCAL.mkdir(exist_ok=True)
key_file = LOCAL / "keys.json"
if not key_file.exists():
    key_file.write_text(json.dumps({"django": secrets.token_urlsafe(48), "idp": secrets.token_urlsafe(48)}), encoding="utf-8")
keys = json.loads(key_file.read_text(encoding="utf-8"))
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", keys["django"])
MOCK_IDP_KEY = os.environ.get("MOCK_IDP_KEY", keys["idp"])
DEBUG = os.environ.get("DEBUG", "0") == "1"
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "django.contrib.postgres", "core"]
MIDDLEWARE = ["core.request_context.CorrelationMiddleware","django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "core.context.navigation"]}}]
WSGI_APPLICATION = "config.wsgi.application"
db_config_file = LOCAL / "database.json"
use_private_db = db_config_file.exists() and os.environ.get("CAMPUS_USE_EXTERNAL_DATABASE") != "1"
db_config = json.loads(db_config_file.read_text(encoding="utf-8")) if use_private_db else {}
def database_value(env_name, field, default):
    return str(db_config[field]) if use_private_db else os.environ.get(env_name, default)
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": database_value("PGDATABASE", "name", "campus_lab"), "USER": database_value("PGUSER", "user", "campus"), "PASSWORD": database_value("PGPASSWORD", "password", ""), "HOST": database_value("PGHOST", "host", "127.0.0.1"), "PORT": database_value("PGPORT", "port", "55432"), "CONN_MAX_AGE": 0, "OPTIONS": {"connect_timeout": 10}}}
AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = LOCAL / "static"
MEDIA_ROOT = LOCAL / "evidence"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
