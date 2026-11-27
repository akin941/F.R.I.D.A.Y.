# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13b closeout (brief branch 3, ADR-018): `check_home_direct` --
`ha_direct_media`, pass / warn / fail, read-only.

- **pass**: both kill switches off, at least one flagged entity, each one
  enabled, a `media_player` with a direct-eligible service, and the closed
  set's digest equal to ADR-018's recorded value;
- **warn**: the env kill switch (or a zero rate limit), the owner's kill
  switch, Home Assistant not configured, nothing flagged, a flagged entity
  disabled in the allowlist;
- **fail**: a flagged entity outside the class (only reachable by a write
  that goes around `HomeEntity.save()`), or the digest differs.

No Home Assistant request (the fake records every request), no model call,
no row written (the owner's settings row is read, never created)."""

from decimal import Decimal

import pytest

from ai_core.models import ModelUsage
from audit.models import AuditLog
from connectors import home_direct
from connectors.models import HomeDirectSettings, HomeEntity
from connectors.tests.conftest import committed, enable, firm, owner, workspace  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha  # noqa: F401
from connectors.tests.home_direct_helpers import TV, bound_ha, direct_tv, flagged  # noqa: F401
from ops import preflight
from ops.preflight import FAIL, PASS, WARN

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def direct_env(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6
    return settings


def _row():
    (row,) = preflight.check_home_direct()
    assert row.name == "ha_direct_media"
    return row


# --- pass ------------------------------------------------------------------------


def test_a_flagged_tv_with_both_switches_off_passes(direct_tv, ha):
    row = _row()
    assert row.status == PASS, row
    assert row.detail == (
        "state on, 1 flagged entity, limits 30/min global and 10/min per entity, "
        "volume max 60%; each a media_player with a direct service, closed set matches ADR-018"
    )
    # Read-only: no HA request, no model call, no settings row, no audit row.
    assert ha.requests == []
    assert not ModelUsage.objects.exists()
    assert not HomeDirectSettings.objects.exists()
    assert not AuditLog.objects.filter(action__startswith="ha.").exists()


def test_the_detail_carries_the_owner_volume_maximum_and_the_env_limits(
    direct_tv, workspace, settings
):
    HomeDirectSettings.objects.create(workspace=workspace, volume_max=Decimal("0.35"))
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 20
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 5
    row = _row()
    assert row.status == PASS
    assert "limits 20/min global and 5/min per entity, volume max 35%" in row.detail


def test_two_flagged_entities_are_counted(direct_tv, workspace):
    flagged(workspace, "media_player.yatak_odasi", label="Yatak odası TV")
    row = _row()
    assert row.status == PASS and "2 flagged entities" in row.detail


# --- warn ------------------------------------------------------------------------


def test_the_env_kill_switch_warns(direct_tv, settings):
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    row = _row()
    assert row.status == WARN
    assert row.detail.startswith("state off_env, 1 flagged entity")
    assert "every media call is a card" in row.detail


@pytest.mark.parametrize(
    "limit", ["HA_DIRECT_RATE_GLOBAL_PER_MINUTE", "HA_DIRECT_RATE_ENTITY_PER_MINUTE"]
)
def test_a_zero_rate_limit_reads_as_off_by_the_environment(direct_tv, settings, limit):
    setattr(settings, limit, 0)
    row = _row()
    assert row.status == WARN and row.detail.startswith("state off_env")


def test_the_owner_kill_switch_warns(direct_tv, workspace):
    HomeDirectSettings.objects.create(workspace=workspace, kill_switch=True, volume_max="0.60")
    row = _row()
    assert row.status == WARN
    assert row.detail.startswith("state off_owner")
    assert "the owner's kill switch is on" in row.detail


def test_home_assistant_not_configured_warns(workspace, ha):
    # Flagged, but no binding was ever enabled.
    flagged(workspace, label="TV")
    row = _row()
    assert row.status == WARN
    assert "Home Assistant not configured" in row.detail
    assert ha.requests == []


def test_no_owner_warns_as_not_configured(db):
    row = _row()
    assert row.status == WARN
    assert row.detail.startswith("state on, 0 flagged entities")
    assert "Home Assistant not configured" in row.detail


def test_nothing_flagged_warns_with_the_owner_step(bound_ha, workspace):
    allow(workspace, TV, ["media_pause"], label="TV")
    row = _row()
    assert row.status == WARN
    assert "no entity flagged direct" in row.detail
    assert "/connectors" in row.detail


def test_a_disabled_flagged_entity_is_not_direct_and_warns(direct_tv, workspace):
    other = flagged(workspace, "media_player.yatak_odasi", label="Yatak odası TV")
    HomeEntity.objects.filter(pk=other.pk).update(enabled=False)
    row = _row()
    assert row.status == WARN
    assert "1 flagged entity" in row.detail
    assert "flagged but disabled (not direct): media_player.yatak_odasi" in row.detail


# --- fail ------------------------------------------------------------------------


def test_a_flagged_light_fails(direct_tv, workspace):
    light = allow(workspace, "light.salon_tavan", ["turn_on"], label="lamba")
    # `HomeEntity.save()` refuses it; a direct write is what the row guards.
    HomeEntity.objects.filter(pk=light.pk).update(direct=True)
    row = _row()
    assert row.status == FAIL
    assert "light.salon_tavan=not_media_player" in row.detail


def test_a_flagged_tv_without_a_direct_service_fails(direct_tv, workspace):
    HomeEntity.objects.filter(pk=direct_tv.pk).update(services=["turn_on", "select_source"])
    row = _row()
    assert row.status == FAIL
    assert f"{TV}=no_direct_service" in row.detail


def test_a_flagged_entity_outside_a_personal_workspace_fails(direct_tv, firm):
    tv = allow(firm, "media_player.toplanti", ["media_pause"], label="Toplantı TV")
    HomeEntity.objects.filter(pk=tv.pk).update(direct=True)
    row = _row()
    assert row.status == FAIL
    assert "media_player.toplanti=not_personal" in row.detail


def test_a_failing_flag_wins_over_the_kill_switch(direct_tv, workspace, settings):
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    HomeEntity.objects.filter(pk=direct_tv.pk).update(services=["turn_on"])
    assert _row().status == FAIL


def test_a_widened_closed_set_fails(direct_tv, monkeypatch):
    monkeypatch.setattr(
        home_direct, "DIRECT_MEDIA_SERVICES", home_direct.DIRECT_MEDIA_SERVICES | {"turn_on"}
    )
    row = _row()
    assert row.status == FAIL
    assert "the direct media closed set changed" in row.detail
    assert "needs an ADR-018 amendment" in row.detail


def test_a_narrowed_closed_set_fails(direct_tv, monkeypatch):
    monkeypatch.setattr(
        home_direct, "DIRECT_MEDIA_SERVICES", home_direct.DIRECT_MEDIA_SERVICES - {"volume_set"}
    )
    assert _row().status == FAIL


def test_a_code_digest_moved_without_the_preflight_pin_fails(direct_tv, monkeypatch):
    """Changing the set and `ADR_018_DIGEST` together still fails: the
    preflight pin is a second, independent copy of ADR-018's value."""
    widened = home_direct.DIRECT_MEDIA_SERVICES | {"turn_on"}
    monkeypatch.setattr(home_direct, "DIRECT_MEDIA_SERVICES", widened)
    monkeypatch.setattr(home_direct, "ADR_018_DIGEST", home_direct.closed_set_digest(widened))
    assert _row().status == FAIL


def test_the_preflight_pin_is_the_adr_018_value():
    from connectors.tests.test_home_direct_classifier import ADR

    assert preflight.ADR_018_DIRECT_MEDIA_SHA256 == home_direct.ADR_018_DIGEST
    assert preflight.ADR_018_DIRECT_MEDIA_SHA256 in ADR.read_text(encoding="utf-8")


# --- wiring ----------------------------------------------------------------------


def test_the_row_is_registered_after_check_home_control(direct_tv):
    names = [name for name, _ in preflight.CHECKS]
    assert names.index("home_direct") == names.index("home_control") + 1
    assert ("home_direct", preflight.check_home_direct) in preflight.CHECKS
    results = preflight.run_preflight(checks=(("home_direct", preflight.check_home_direct),))
    assert [(r.name, r.status) for r in results] == [("ha_direct_media", PASS)]


def test_a_crashing_row_is_one_fail(monkeypatch):
    def boom():
        raise RuntimeError("table missing")

    monkeypatch.setattr(preflight, "_check_ha_direct_media", boom)
    (row,) = preflight.check_home_direct()
    assert (row.name, row.status, row.detail) == (
        "ha_direct_media",
        FAIL,
        "check crashed: RuntimeError",
    )
