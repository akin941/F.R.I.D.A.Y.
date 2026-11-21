"""Test settings (pytest, backend/pyproject.toml): dev settings with a
process-local cache, whatever REDIS_URL the environment happens to carry --
throttle counters and the Telegram relay limiter must never reach a real
Redis from the test suite (root conftest.py clears this cache per test)."""

import base64
import secrets

from .dev import *  # noqa: F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Phase 9 branch 2: a per-process random node signing key, so pairing and
# invoke signing work in tests without any key in the repository. Tests of
# the missing/invalid-key paths override NODE_SIGNING_KEY themselves.
NODE_SIGNING_KEY = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
NODE_SIGNING_KEYS_RETIRED = []
