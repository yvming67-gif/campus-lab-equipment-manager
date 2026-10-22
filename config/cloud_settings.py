"""Cloud demonstration profile; the existing local profile remains independent."""
from .settings import *  # noqa: F403
from .cloud_environment import database, hostname, required, storage_options
from botocore.config import Config

SECRET_KEY = required(os.environ, "DJANGO_SECRET_KEY", 32)
MOCK_IDP_KEY = required(os.environ, "MOCK_IDP_KEY", 32)
if os.environ.get("DEMO_MODE") != "1":
    raise ValueError("This cloud profile is for simulated demonstrations; set DEMO_MODE=1")
DEMO_MODE = True
DEBUG = False
cloud_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    cloud_hosts.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
if not cloud_hosts:
    raise ValueError("Cloud configuration requires an explicit hostname")
ALLOWED_HOSTS = sorted({hostname(h) for h in cloud_hosts})
CSRF_TRUSTED_ORIGINS = ["https://" + h for h in ALLOWED_HOSTS]
DATABASES = {"default": database(os.environ)}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# Internal platform probes must execute the database check, not return a healthy
# 301 before reaching it. This endpoint contains no account or secret data.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_HSTS_SECONDS = 3600
MIDDLEWARE.insert(MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1, "whitenoise.middleware.WhiteNoiseMiddleware")
options = storage_options(os.environ)
options["client_config"] = Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"}, request_checksum_calculation="when_required", response_checksum_validation="when_required")
STORAGES = {
    "default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": options},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"safe": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "safe"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"campus": {"handlers": ["console"], "level": "INFO", "propagate": False}, "django.server": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
