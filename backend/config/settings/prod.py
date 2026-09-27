"""Production settings (Phase 4 branch 1) -- fail hard, never guess.

Every value an installation must choose is required here and raises
`ImproperlyConfigured` at import when missing or empty; nothing falls back
to a development default. `manage.py check --deploy` passes under these
settings (CI runs it with a dummy environment).

Transport security split: Django's only client is the Next.js BFF over
loopback/compose-network HTTP (frontend/src/lib/backend-fetch.ts), so a
Django-level SSL redirect would break every BFF call and HSTS on those
responses would never reach a browser. Both are therefore env-driven and
OFF by default here; the browser-facing HSTS, `frame-ancestors 'none'`,
nosniff and referrer policy are set by the frontend for production builds
(frontend/next.config.ts). Turn the Django ones on only when Django itself
is served directly over HTTPS.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import DEV_FALLBACK_SECRET_KEY, env, redis_cache_location


def _required(name):
    value = env(name, default="").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} is required in production settings.")
    return value


DEBUG = False

SECRET_KEY = _required("DJANGO_SECRET_KEY")
if SECRET_KEY == DEV_FALLBACK_SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must not be the development fallback.")

DATABASES = {"default": env.db_url_config(_required("DATABASE_URL"))}

ALLOWED_HOSTS = [host.strip() for host in _required("DJANGO_ALLOWED_HOSTS").split(",")]
ALLOWED_HOSTS = [host for host in ALLOWED_HOSTS if host]

REDIS_URL = _required("REDIS_URL")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
# Always Redis in production: throttles and the Telegram relay limiter must
# count across every web/worker process.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL", default="") or redis_cache_location(REDIS_URL),
    }
}

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# Off by default -- see the module docstring.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0

# `check --deploy` flags exactly the two deliberate choices above; silence
# them only while they are actually off, so turning one on re-enables its
# check.
SILENCED_SYSTEM_CHECKS = []
if not SECURE_HSTS_SECONDS:
    SILENCED_SYSTEM_CHECKS.append("security.W004")
if not SECURE_SSL_REDIRECT:
    SILENCED_SYSTEM_CHECKS.append("security.W008")
