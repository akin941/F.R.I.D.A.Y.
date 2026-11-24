"""Settings modules (Phase 4 branch 1). Each case imports settings in a
fresh interpreter with a controlled environment -- settings are
module-level code, so an in-process reload would leak into the rest of the
suite."""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Variables a developer's shell / repo-root .env might carry that would
# change what these tests observe. base.py's read_env() only setdefaults,
# so blanking them here (empty = unset for every one of these string
# settings) keeps the repo-root .env from leaking in.
_BLANKED = [
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DATABASE_URL",
    "REDIS_URL",
    "REDIS_CACHE_URL",
    "DJANGO_TEST_DATABASE_NAME",
]
# Removed from the inherited environment instead: the settings module
# (entry points only `setdefault` it) and typed int/bool settings, which an
# empty string would not parse.
_REMOVED = ["DJANGO_SETTINGS_MODULE", "DJANGO_SECURE_HSTS_SECONDS", "DJANGO_SECURE_SSL_REDIRECT"]

PROD_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
    "DATABASE_URL": "postgres://friday:pw@db.internal:5432/friday",
    "DJANGO_ALLOWED_HOSTS": "web,friday.example",
    "REDIS_URL": "redis://:pw@redis:6379/0",
}


def _run(code, **env_overrides):
    env = {key: value for key, value in os.environ.items() if key not in _BLANKED + _REMOVED}
    env.update({key: "" for key in _BLANKED})
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


_DUMP = (
    "import json, django; django.setup(); from django.conf import settings as s; "
    "print(json.dumps({'debug': s.DEBUG, 'secret': s.SECRET_KEY, "
    "'cache': s.CACHES['default']['BACKEND'], "
    "'cache_location': s.CACHES['default'].get('LOCATION', ''), "
    "'test_db': s.DATABASES['default'].get('TEST', {}).get('NAME'), "
    "'silenced': s.SILENCED_SYSTEM_CHECKS}))"
)


def _dump(**env):
    result = _run(_DUMP, **env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_prod_settings_fail_hard_without_a_secret_key():
    env = {**PROD_ENV, "DJANGO_SECRET_KEY": ""}
    result = _run("import django; django.setup()", **env)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


@pytest.mark.parametrize("missing", ["DATABASE_URL", "DJANGO_ALLOWED_HOSTS", "REDIS_URL"])
def test_prod_settings_fail_hard_without_each_required_variable(missing):
    env = {**PROD_ENV, missing: ""}
    result = _run("import django; django.setup()", **env)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert missing in result.stderr


def test_prod_settings_refuse_the_dev_fallback_secret_even_with_debug_env():
    from config.settings.base import DEV_FALLBACK_SECRET_KEY

    env = {**PROD_ENV, "DJANGO_SECRET_KEY": DEV_FALLBACK_SECRET_KEY, "DJANGO_DEBUG": "true"}
    result = _run("import django; django.setup()", **env)
    assert result.returncode != 0
    assert "development fallback" in result.stderr


def test_prod_settings_pass_check_deploy():
    result = _run(
        "import sys; from django.core.management import execute_from_command_line; "
        "execute_from_command_line(['manage.py', 'check', '--deploy', '--fail-level', 'WARNING'])",
        **PROD_ENV,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_prod_settings_are_secure_and_use_redis_cache():
    result = _run(
        "import json, django; django.setup(); from django.conf import settings as s; "
        "print(json.dumps([s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, "
        "s.SECURE_CONTENT_TYPE_NOSNIFF, s.X_FRAME_OPTIONS, s.SECURE_REFERRER_POLICY, "
        "s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, s.CACHES['default']]))",
        **PROD_ENV,
    )
    assert result.returncode == 0, result.stderr
    debug, session, csrf, nosniff, xfo, referrer, redirect, hsts, cache = json.loads(
        result.stdout.strip().splitlines()[-1]
    )
    assert (debug, session, csrf, nosniff, xfo) == (False, True, True, True, "DENY")
    assert referrer == "same-origin"
    # Off at Django by default: its only client is the BFF over loopback HTTP.
    assert (redirect, hsts) == (False, 0)
    assert cache["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
    assert cache["LOCATION"] == "redis://:pw@redis:6379/1"


def test_hsts_and_ssl_redirect_are_env_driven_and_unsilence_their_checks():
    settings = _dump(
        **PROD_ENV, DJANGO_SECURE_HSTS_SECONDS="3600", DJANGO_SECURE_SSL_REDIRECT="true"
    )
    assert settings["silenced"] == []
    assert _dump(**PROD_ENV)["silenced"] == ["security.W004", "security.W008"]


def test_base_settings_refuse_the_fallback_secret_without_debug():
    result = _run("import django; django.setup()", DJANGO_SETTINGS_MODULE="config.settings.base")
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY is required" in result.stderr


def test_base_settings_allow_the_fallback_secret_only_with_debug():
    from config.settings.base import DEV_FALLBACK_SECRET_KEY

    settings = _dump(DJANGO_SETTINGS_MODULE="config.settings.base", DJANGO_DEBUG="true")
    assert settings["secret"] == DEV_FALLBACK_SECRET_KEY


def test_dev_settings_boot_without_any_secret():
    settings = _dump(DJANGO_SETTINGS_MODULE="config.settings.dev")
    assert settings["debug"] is True
    assert settings["cache"] == "django.core.cache.backends.locmem.LocMemCache"


def test_redis_cache_is_used_when_redis_url_is_set():
    settings = _dump(
        DJANGO_SETTINGS_MODULE="config.settings.dev", REDIS_URL="redis://:pw@redis:6379/0"
    )
    assert settings["cache"] == "django.core.cache.backends.redis.RedisCache"
    assert settings["cache_location"] == "redis://:pw@redis:6379/1"


def test_redis_cache_url_overrides_the_derived_location():
    settings = _dump(
        DJANGO_SETTINGS_MODULE="config.settings.dev",
        REDIS_URL="redis://:pw@redis:6379/0",
        REDIS_CACHE_URL="redis://:pw@cache:6379/5",
    )
    assert settings["cache_location"] == "redis://:pw@cache:6379/5"


def test_test_settings_always_use_locmem():
    settings = _dump(
        DJANGO_SETTINGS_MODULE="config.settings.test", REDIS_URL="redis://:pw@redis:6379/0"
    )
    assert settings["cache"] == "django.core.cache.backends.locmem.LocMemCache"


def test_test_database_name_comes_from_the_environment():
    settings = _dump(
        DJANGO_SETTINGS_MODULE="config.settings.dev", DJANGO_TEST_DATABASE_NAME="test_friday_wt"
    )
    assert settings["test_db"] == "test_friday_wt"
    assert _dump(DJANGO_SETTINGS_MODULE="config.settings.dev")["test_db"] is None


@pytest.mark.parametrize(
    "code",
    [
        "import config.wsgi",
        "import config.asgi",
        # Celery loads Django settings lazily, on first config access.
        "from config.celery import app; app.conf.broker_url",
    ],
    ids=["wsgi", "asgi", "celery"],
)
def test_server_entry_points_default_to_prod_settings(code):
    result = _run(code)
    assert result.returncode != 0
    assert "is required" in result.stderr


def test_server_entry_points_boot_with_a_complete_prod_env():
    result = _run(
        "import os; os.environ.pop('DJANGO_SETTINGS_MODULE', None);"
        "import config.wsgi; from django.conf import settings; print(settings.SETTINGS_MODULE)",
        **{k: v for k, v in PROD_ENV.items() if k != "DJANGO_SETTINGS_MODULE"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "config.settings.prod"


def test_manage_py_keeps_the_dev_default():
    result = _run(
        "import runpy, sys; sys.argv = ['manage.py', 'diffsettings', '--output', 'unified'];"
        "runpy.run_path('manage.py', run_name='__main__')"
    )
    assert result.returncode == 0, result.stderr
    assert "+ DEBUG = True" in result.stdout


# Phase 11 D3: the node voice credential's lifetime is capped at boot.
@pytest.mark.parametrize(("ttl", "ok"), [("3601", False), ("59", False), ("3600", True)])
def test_the_token_ttl_is_capped_at_boot(ttl, ok):
    result = _run(
        "import django; django.setup()",
        DJANGO_SETTINGS_MODULE="config.settings.dev",
        VOICE_LIVE_TOKEN_TTL_SECONDS=ttl,
    )
    assert (result.returncode == 0) is ok, result.stderr[-500:]
    if not ok:
        assert "VOICE_LIVE_TOKEN_TTL_SECONDS" in result.stderr
