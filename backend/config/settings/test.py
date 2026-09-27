"""Test settings (pytest, backend/pyproject.toml): dev settings with a
process-local cache, whatever REDIS_URL the environment happens to carry --
throttle counters and the Telegram relay limiter must never reach a real
Redis from the test suite (root conftest.py clears this cache per test)."""

from .dev import *  # noqa: F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
