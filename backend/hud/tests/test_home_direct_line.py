"""The HUD's `home_direct` line (Phase 13b brief D11, branch 1d, ADR-018):
`health.home_direct` in `/hud/events` is `{state, flagged, calls_last_hour}`
with `state` one of `on` / `off_owner` / `off_env` / `not_configured`, read
from the database only (never a Home Assistant request, never a row
written), workspace-scoped, never another owner's entities or calls, and a
delta when a direct call lands between two ticks. The line is read through
`tools.home_ports.home_direct_status`; an unregistered, raising or
malformed reader is `not_configured`."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.services import register_user
from audit.models import AuditLog
from connectors import home_direct
from connectors.bridges import home_assistant as bridge
from connectors.manifest import get_manifest
from connectors.models import ConnectorBinding, HomeDirectCall, HomeDirectSettings, HomeEntity
from core.speech import phrase_key
from hud.services import health_section, home_direct_section
from hud.tests.conftest import PASSWORD
from tools import home_ports
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

TV = "media_player.salon_tv"
TV_SERVICES = ["turn_on", "turn_off", "media_play", "media_pause", "volume_set"]
LINE_KEYS = {"state", "flagged", "calls_last_hour"}


@pytest.fixture(autouse=True)
def direct_env(settings, monkeypatch):
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6

    # The HUD line never talks to Home Assistant: any transport use fails.
    def no_transport():
        raise AssertionError("the HUD line made a Home Assistant request")

    monkeypatch.setattr(bridge, "_http_transport", no_transport)
    return settings


def bind(workspace, *, enabled=True, status="ok"):
    manifest = get_manifest("home_assistant")
    return ConnectorBinding.objects.create(
        workspace=workspace,
        connector=manifest.name,
        enabled=enabled,
        last_health={
            "manifest_version": manifest.version,
            "tools": {policy.name: status for policy in manifest.tools},
        },
    )


def entity(workspace, entity_id=TV, *, services=None, direct=True, enabled=True, label="TV"):
    return HomeEntity.objects.create(
        workspace=workspace,
        entity_id=entity_id,
        label=label,
        key=phrase_key(label),
        services=list(TV_SERVICES if services is None else services),
        enabled=enabled,
        direct=direct,
    )


def direct_call(workspace, owner, *, ago=timedelta(minutes=5), **fields):
    values = {
        "entity_id": TV,
        "service": "media_pause",
        "origin": HomeDirectCall.Origin.VOICE,
        "requested_by": owner,
        "status": HomeDirectCall.Status.SUCCEEDED,
    }
    values.update(fields)
    row = HomeDirectCall.objects.create(workspace=workspace, **values)
    HomeDirectCall.objects.filter(pk=row.pk).update(created_at=timezone.now() - ago)
    return row


def _line(stream, workspace_id, **kwargs):
    frames = stream(workspace_id, **kwargs)
    return frames[1]["health"]["home_direct"]


@pytest.fixture
def direct_tv(workspace):
    bind(workspace)
    return entity(workspace)


# --- the four states ------------------------------------------------------------


def test_not_configured_without_home_assistant(stream, workspace):
    assert _line(stream, workspace.id) == {
        "state": "not_configured",
        "flagged": 0,
        "calls_last_hour": 0,
    }


def test_on_with_a_flagged_entity_and_a_working_binding(stream, owner, workspace, direct_tv):
    direct_call(workspace, owner)
    direct_call(workspace, owner, ago=timedelta(minutes=59), status=HomeDirectCall.Status.FAILED)
    direct_call(workspace, owner, ago=timedelta(minutes=61))
    direct_call(workspace, owner, ago=timedelta(days=1))
    line = _line(stream, workspace.id)
    assert set(line) == LINE_KEYS
    assert line == {"state": "on", "flagged": 1, "calls_last_hour": 2}


def test_off_owner(stream, owner, workspace, direct_tv):
    HomeDirectSettings.objects.create(workspace=workspace, kill_switch=True, volume_max="0.60")
    direct_call(workspace, owner)
    assert _line(stream, workspace.id) == {"state": "off_owner", "flagged": 1, "calls_last_hour": 1}


def test_off_env(stream, workspace, direct_tv, direct_env):
    direct_env.HA_DIRECT_MEDIA_KILL_SWITCH = True
    assert _line(stream, workspace.id)["state"] == "off_env"


def test_env_wins_over_the_owner_switch(stream, workspace, direct_tv, direct_env):
    HomeDirectSettings.objects.create(workspace=workspace, kill_switch=True, volume_max="0.60")
    direct_env.HA_DIRECT_MEDIA_KILL_SWITCH = True
    assert _line(stream, workspace.id)["state"] == "off_env"


@pytest.mark.parametrize(
    "limit", ["HA_DIRECT_RATE_GLOBAL_PER_MINUTE", "HA_DIRECT_RATE_ENTITY_PER_MINUTE"]
)
def test_a_zero_rate_limit_is_off_env(stream, workspace, direct_tv, direct_env, limit):
    # `.env.example`: a limit of 0 disables the class.
    setattr(direct_env, limit, 0)
    assert _line(stream, workspace.id)["state"] == "off_env"


def test_not_configured_when_nothing_is_flagged(stream, workspace):
    bind(workspace)
    entity(workspace, direct=False)
    assert _line(stream, workspace.id) == {
        "state": "not_configured",
        "flagged": 0,
        "calls_last_hour": 0,
    }


def test_a_disabled_flagged_entity_is_not_counted(stream, workspace):
    bind(workspace)
    entity(workspace, enabled=False)
    assert _line(stream, workspace.id)["flagged"] == 0


@pytest.mark.parametrize(
    "binding", [{"enabled": False}, {"status": "pin_mismatch"}, {"status": "unchecked"}]
)
def test_not_configured_when_home_assistant_is_not_usable(stream, owner, workspace, binding):
    bind(workspace, **binding)
    entity(workspace)
    direct_call(workspace, owner)
    # The counts still show what exists; the class cannot run.
    assert _line(stream, workspace.id) == {
        "state": "not_configured",
        "flagged": 1,
        "calls_last_hour": 1,
    }


def test_two_flagged_entities(stream, workspace, direct_tv):
    entity(workspace, "media_player.yatak_tv", label="Yatak TV")
    assert _line(stream, workspace.id)["flagged"] == 2


# --- reads only, scoped -----------------------------------------------------------


def test_the_line_writes_nothing(stream, owner, workspace, direct_tv):
    direct_call(workspace, owner)
    audit_before = AuditLog.objects.count()
    stream(workspace.id)
    assert not HomeDirectSettings.objects.exists()
    assert HomeDirectCall.objects.count() == 1
    assert AuditLog.objects.count() == audit_before


def test_a_firm_stream_shows_not_configured(stream, owner, workspace, direct_tv):
    direct_call(workspace, owner)
    firm = Workspace.objects.create(name="Firma", owner=owner, type="company")
    WorkspaceMember.objects.create(workspace=firm, user=owner, role="owner")
    assert _line(stream, firm.id) == {"state": "not_configured", "flagged": 0, "calls_last_hour": 0}


def test_another_user_never_sees_my_entities_or_calls(stream, owner, workspace, direct_tv):
    direct_call(workspace, owner)
    # One installation owner per install: another user's personal
    # workspace is never a direct-class workspace.
    other = register_user(email="other@test.dev", password=PASSWORD)
    other_ws = Workspace.objects.get(owner=other, type=Workspace.Type.PERSONAL)
    line = home_direct_section(other_ws, other)
    assert line == {"state": "not_configured", "flagged": 0, "calls_last_hour": 0}
    # ...and his own flagged TV and calls do not reach my line.
    bind(other_ws)
    entity(other_ws)
    direct_call(other_ws, other)
    direct_call(other_ws, other)
    assert _line(stream, workspace.id) == {"state": "on", "flagged": 1, "calls_last_hour": 1}


def test_a_non_installation_owner_is_not_configured(owner, workspace, direct_tv):
    owner.is_owner = False
    owner.save()
    assert home_direct_section(workspace, owner)["state"] == "not_configured"


def test_no_entity_id_service_or_content_in_the_line(stream, owner, workspace, direct_tv):
    direct_call(workspace, owner, data={"volume_level": 0.4}, service="volume_set")
    line = _line(stream, workspace.id)
    text = repr(line)
    assert TV not in text and "volume" not in text and "salon" not in text


def test_a_delta_when_a_call_lands_between_ticks(stream, owner, workspace, direct_tv, fake_clock):
    fake_clock.ticks = [lambda: direct_call(workspace, owner, ago=timedelta(seconds=1))]
    frames = stream(workspace.id)
    assert frames[1]["health"]["home_direct"]["calls_last_hour"] == 0
    delta = frames[2]
    assert delta["type"] == "delta" and "health" in delta
    assert delta["health"]["home_direct"] == {"state": "on", "flagged": 1, "calls_last_hour": 1}


def test_health_section_without_an_actor(workspace):
    assert health_section(workspace)["home_direct"] == {
        "state": "not_configured",
        "flagged": 0,
        "calls_last_hour": 0,
    }


# --- the port ---------------------------------------------------------------------


@pytest.fixture
def restore_reader():
    yield
    home_ports.register_direct_status(home_direct.hud_status)


def test_connectors_registers_the_reader():
    assert home_ports.direct_status_reader() is home_direct.hud_status


def test_an_unregistered_reader_is_not_configured(stream, workspace, direct_tv, restore_reader):
    home_ports.unregister_direct_status()
    assert _line(stream, workspace.id) == home_ports.not_configured_status()


@pytest.mark.parametrize(
    "answer",
    [
        RuntimeError("boom"),
        None,
        {"state": "on"},
        {"state": "maybe", "flagged": 1, "calls_last_hour": 0},
        {"state": "on", "flagged": -1, "calls_last_hour": 0},
        {"state": "on", "flagged": True, "calls_last_hour": 0},
        {"state": "on", "flagged": "1", "calls_last_hour": 0},
    ],
)
def test_a_failing_or_malformed_reader_is_not_configured(owner, workspace, restore_reader, answer):
    def reader(**_kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    home_ports.register_direct_status(reader)
    assert home_direct_section(workspace, owner) == home_ports.not_configured_status()


def test_the_port_passes_only_the_three_keys(owner, workspace, restore_reader):
    home_ports.register_direct_status(
        lambda **_: {"state": "on", "flagged": 1, "calls_last_hour": 2, "entity_id": TV}
    )
    assert home_direct_section(workspace, owner) == {
        "state": "on",
        "flagged": 1,
        "calls_last_hour": 2,
    }
