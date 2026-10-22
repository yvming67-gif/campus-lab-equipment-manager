"""Validate the separate cloud-demo profile without exposing secret values."""
import re
from urllib.parse import parse_qs, unquote, urlsplit


def required(env, name, minimum=1):
    value = env.get(name, "").strip()
    if len(value) < minimum:
        raise ValueError(f"Cloud configuration requires {name}")
    return value


def hostname(value):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value) or ".." in value:
        raise ValueError("Cloud hostname must contain a hostname only")
    return value.lower()


def database(env):
    try:
        url = urlsplit(required(env, "DATABASE_URL"))
        if url.scheme not in {"postgres", "postgresql"} or not url.hostname or not url.username or not url.password or url.fragment:
            raise ValueError
        name = unquote(url.path.removeprefix("/"))
        if not name or "/" in name or "\x00" in name:
            raise ValueError
        options = parse_qs(url.query, strict_parsing=True)
        if any(len(v) != 1 for v in options.values()):
            raise ValueError
        if set(options) - {"sslmode", "channel_binding", "application_name"}:
            raise ValueError
        sslmode = options.get("sslmode", ["require"])[0]
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise ValueError
        port = str(url.port or 5432)
        opts = {k: v[0] for k, v in options.items()}
        opts.update(sslmode=sslmode, connect_timeout=10)
        return {"ENGINE": "django.db.backends.postgresql", "NAME": name, "USER": unquote(url.username), "PASSWORD": unquote(url.password), "HOST": url.hostname, "PORT": port, "CONN_MAX_AGE": 0, "DISABLE_SERVER_SIDE_CURSORS": True, "OPTIONS": opts}
    except (ValueError, TypeError):
        raise ValueError("DATABASE_URL must be a valid PostgreSQL URL with credentials and TLS enabled") from None


def storage_options(env):
    endpoint = required(env, "AWS_ENDPOINT_URL_S3")
    url = urlsplit(endpoint)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("AWS_ENDPOINT_URL_S3 must be an HTTPS S3 endpoint without credentials or a path")
    bucket = required(env, "CAMPUS_STORAGE_BUCKET")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("CAMPUS_STORAGE_BUCKET must be a valid bucket name")
    return dict(endpoint_url=endpoint.rstrip("/"), access_key=required(env, "AWS_ACCESS_KEY_ID"), secret_key=required(env, "AWS_SECRET_ACCESS_KEY"), region_name=required(env, "AWS_REGION"), bucket_name=bucket, default_acl=None, custom_domain=None, querystring_auth=True, querystring_expire=60, file_overwrite=False, location="", object_parameters={"CacheControl": "private, no-store"})
