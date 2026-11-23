import pytest


@pytest.fixture(autouse=True)
def _celery_eager_for_tests(settings):
    """MVP 2 §4: the whole backend test suite runs Celery in eager mode, so a
    `transaction.on_commit` dispatch (extraction job publish) executes the
    task inline instead of requiring a live broker. `EAGER_PROPAGATES` makes
    an eager task's own exceptions surface in the calling test rather than
    being swallowed, per the brief's negative-test requirement.

    Dev/Compose leave both flags off by default (`config/settings/base.py`)
    so the real worker container keeps processing tasks out-of-process; only
    this test fixture (and the e2e CI job's env) turns eager mode on.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(autouse=True)
def _clear_cache():
    """Throttle counters (DRF scoped throttles, the Telegram relay limiter)
    live in the default cache (LocMem under config/settings/test.py). The
    anonymous refresh scope keys on REMOTE_ADDR, identical for every
    test client, so without a per-test reset their counts would leak across
    unrelated tests."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def web_client(db):
    """THE way a test reaches a web-channel view (Phase 4 branch 2): an
    APIClient authenticated as `user` with a `WebSessionPrincipal`, i.e.
    exactly what `DeviceJWTAuthentication` hands a view for a trusted
    device's session. Replaces the per-app `auth_client`/`client_for`
    helpers built on a bare `force_authenticate(user=...)`, which after the
    default-deny flip would silently be CHANNEL_UNKNOWN (low-trust) --
    `core/tests/test_web_client_fixture_guard.py` fails if one comes back.

    Call it as a factory: `web_client(user)`, or `web_client(user,
    device=device)` to bind the session to a real TrustedDevice row (views
    that act on "the current device"). Without `device` the principal names
    a random, row-less device id, so no test pays for a device row it does
    not need. Multi-user role tests keep working: the channel is the
    principal's, while owner-only surfaces still check `is_owner`. The real
    authenticator (device row, `did` claim, owner check) is covered end to
    end by accounts/tests/test_device_auth.py."""
    import uuid

    from rest_framework.test import APIClient

    from core.channels import WebSessionPrincipal

    def make(user, device=None):
        client = APIClient()
        device_id = str(device.pk) if device is not None else str(uuid.uuid4())
        client.force_authenticate(user=user, token=WebSessionPrincipal(device_id=device_id))
        return client

    return make


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin `django.utils.timezone.now` (and so `timezone.localdate()` /
    `localtime()`, which call it) to an aware instant: `frozen_now(at)`.

    Date-boundary regressions use it: `TIME_ZONE` is Europe/Istanbul
    (UTC+3), so from 21:00 UTC on the local calendar day is already the
    next one. A test that mixes a UTC date with a local one only fails in
    that window; freezing the clock at both a midday and a late-evening UTC
    instant makes the window part of every run. Only code that reads the
    clock through the `timezone` module is frozen (Postgres `now()` is
    not), which is every call site in this repo."""
    from django.utils import timezone

    def freeze(at):
        assert timezone.is_aware(at), "freeze at an aware instant"
        monkeypatch.setattr(timezone, "now", lambda: at)
        return at

    return freeze
