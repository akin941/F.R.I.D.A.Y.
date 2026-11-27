# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined
"""Phase 13b D8 (ADR-018 decision 5, branch 1b): two sliding one-minute
windows of one-second buckets in the Django cache (frozen clock). 10 per
entity then the 11th -> `rate_limited` + `ha.direct_rate_limited`; 30
across entities then the 31st; the window slides; a cache failure ->
`rate_store_unavailable`; env limits honoured; 0 disables. Follow-up 1 of
the brief review: `classify` only peeks, and one `count_direct_call` is the
single increment of each window per call -- an over-limit call spends
nothing."""

import pytest
from django.core.cache import cache

from audit.models import AuditLog
from connectors import home_direct
from connectors.tests.fake_home_assistant import ha  # noqa: F401
from connectors.tests.home_direct_helpers import (
    TV,
    bound_ha,  # noqa: F401
    call,
    classify,
    direct_tv,  # noqa: F401
    flagged,
)

pytestmark = pytest.mark.django_db

NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    return settings


@pytest.fixture
def clock(monkeypatch):
    now = {"t": NOW}
    monkeypatch.setattr(home_direct, "_now_seconds", lambda: now["t"])

    def advance(seconds):
        now["t"] += seconds

    return advance


def _count(workspace, entity_id=TV, times=1):
    return [home_direct.count_direct_call(workspace.pk, entity_id) for _ in range(times)]


def _rate_audits():
    return list(AuditLog.objects.filter(action="ha.direct_rate_limited").order_by("created_at"))


def test_the_eleventh_call_on_one_entity_is_rate_limited(direct_tv, owner, workspace, clock):
    for _ in range(10):
        assert classify(owner, workspace, origin="voice").direct is True
        assert home_direct.count_direct_call(workspace.pk, TV) is None
        clock(1)
    verdict = classify(owner, workspace, origin="voice")
    assert verdict.direct is False
    assert (verdict.reason, verdict.limit) == (home_direct.RATE_LIMITED, "entity")
    (audit,) = _rate_audits()
    assert audit.metadata == {
        "entity_id": TV,
        "service": "media_pause",
        "origin": "voice",
        "limit": "entity",
    }
    assert audit.workspace_id == workspace.pk
    # The executor's own count refuses too, and takes its increment back.
    assert home_direct.count_direct_call(workspace.pk, TV) == "entity"
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (10, 10)


def test_the_thirty_first_call_across_entities_is_rate_limited(bound_ha, owner, workspace, clock):
    entities = [f"media_player.tv_{i}" for i in range(4)]
    for entity_id in entities:
        flagged(workspace, entity_id)
    for entity_id in entities[:3]:
        assert _count(workspace, entity_id, times=10) == [None] * 10
    fourth = call("media_play", entity_id=entities[3])
    verdict = classify(owner, workspace, fourth)
    assert (verdict.reason, verdict.limit) == (home_direct.RATE_LIMITED, "global")
    assert _rate_audits()[0].metadata["limit"] == "global"
    assert home_direct.count_direct_call(workspace.pk, entities[3]) == "global"
    assert home_direct.peek_direct_calls(workspace.pk, entities[3]) == (30, 0)


def test_the_window_slides(direct_tv, owner, workspace, clock):
    _count(workspace, times=5)
    clock(30)
    _count(workspace, times=5)
    assert classify(owner, workspace).reason == home_direct.RATE_LIMITED
    clock(30)  # the first five are 60 s old: out of the window
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (5, 5)
    assert classify(owner, workspace).direct is True
    clock(31)
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (0, 0)
    assert classify(owner, workspace).direct is True


def test_sixty_one_seconds_later_direct_again(direct_tv, owner, workspace, clock):
    _count(workspace, times=10)
    assert classify(owner, workspace).reason == home_direct.RATE_LIMITED
    clock(61)
    assert classify(owner, workspace).direct is True


def test_classify_only_peeks_and_one_count_increments_each_window_once(
    direct_tv, owner, workspace, clock
):
    for origin in ("chat", "voice", "node"):
        assert classify(owner, workspace, origin=origin).direct is True
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (0, 0)
    assert home_direct.count_direct_call(workspace.pk, TV) is None
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (1, 1)
    # A call that falls back (not direct) spends nothing.
    assert classify(owner, workspace, call("turn_on")).direct is False
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (1, 1)


def test_the_entity_windows_are_separate(bound_ha, owner, workspace, clock):
    flagged(workspace)
    flagged(workspace, "media_player.yatak")
    _count(workspace, times=10)
    assert classify(owner, workspace).reason == home_direct.RATE_LIMITED
    other = call("media_pause", entity_id="media_player.yatak")
    assert classify(owner, workspace, other).direct is True


def test_a_cache_failure_is_never_direct(direct_tv, owner, workspace, monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "get_many", boom)
    assert classify(owner, workspace).reason == home_direct.RATE_STORE_UNAVAILABLE
    with pytest.raises(home_direct.RateStoreUnavailable):
        home_direct.count_direct_call(workspace.pk, TV)
    assert _rate_audits() == []


def test_an_increment_failure_is_unavailable_too(direct_tv, workspace, monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "add", boom)
    with pytest.raises(home_direct.RateStoreUnavailable):
        home_direct.count_direct_call(workspace.pk, TV)


def test_env_limits_are_honoured(direct_tv, owner, workspace, clock, ha_settings):
    ha_settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 2
    assert _count(workspace, times=3) == [None, None, "entity"]
    assert classify(owner, workspace).limit == "entity"
    ha_settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 100
    ha_settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 3
    assert _count(workspace, times=2) == [None, "global"]
    assert classify(owner, workspace).limit == "global"


@pytest.mark.parametrize(
    ("name", "limit"),
    [
        ("HA_DIRECT_RATE_GLOBAL_PER_MINUTE", "global"),
        ("HA_DIRECT_RATE_ENTITY_PER_MINUTE", "entity"),
    ],
)
def test_a_limit_of_zero_disables_the_class(direct_tv, owner, workspace, ha_settings, name, limit):
    setattr(ha_settings, name, 0)
    verdict = classify(owner, workspace)
    assert (verdict.reason, verdict.limit) == (home_direct.RATE_LIMITED, limit)
    assert home_direct.count_direct_call(workspace.pk, TV) == limit
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (0, 0)


def test_workspaces_count_separately(direct_tv, owner, workspace, clock):
    from accounts.services import register_user
    from workspaces.models import Workspace

    other = register_user(email="other@test.dev", password="Str0ngDevPass!42")
    other_ws = Workspace.objects.get(owner=other)
    for _ in range(10):
        home_direct.count_direct_call(other_ws.pk, TV)
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (0, 0)
    assert classify(owner, workspace).direct is True


def test_an_audit_can_be_skipped_by_a_re_check(direct_tv, owner, workspace, clock):
    _count(workspace, times=10)
    verdict = classify(owner, workspace, audit_rate_limit=False)
    assert verdict.reason == home_direct.RATE_LIMITED
    assert _rate_audits() == []
