# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined
"""Phase 13b D4/D7/D9 (ADR-018, branch 1b): the closed allowlist and its
pinned digest, and `connectors.home_direct.classify` -- each closed reason
produced by exactly its condition, in the D7 order; origins only with their
own channel; the low-trust channels, an unattended run, a non-owner, a
non-media domain, a non-flagged entity, a non-direct service, a scene and
Wake-on-LAN are never direct; the kill switches; the volume rules on a
given state and the `volume_set` clamp. `classify` is read-only: no Home
Assistant request, no row, no quota."""

from decimal import Decimal
from pathlib import Path

import pytest

from accounts.models import User
from accounts.services import register_user
from audit.unattended import recorded_unattended_run
from connectors import home_direct
from connectors.bridges import home_assistant as bridge
from connectors.models import ConnectorBinding, HomeDirectCall, HomeDirectSettings
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from connectors.tests.home_direct_helpers import (
    MEDIA_SERVICES,
    ORIGINS,
    TV,
    bound_ha,  # noqa: F401
    call,
    classify,
    direct_tv,  # noqa: F401
    flagged,
)
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    CHANNEL_SYSTEM,
    CHANNEL_TELEGRAM,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
)
from tools.home_ports import DirectVerdict
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

ADR = Path(__file__).resolve().parents[3] / "docs/adr/ADR-018-direct-home-media-class.md"


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6
    return settings


# --- D4: the closed allowlist ---------------------------------------------------


def test_the_closed_set_is_exactly_adr_018s_eight_services():
    assert home_direct.DIRECT_MEDIA_DOMAIN == "media_player"
    assert home_direct.DIRECT_MEDIA_SERVICES == frozenset(
        {
            "media_play",
            "media_pause",
            "media_play_pause",
            "media_stop",
            "volume_up",
            "volume_down",
            "volume_set",
            "volume_mute",
        }
    )
    assert isinstance(home_direct.DIRECT_MEDIA_SERVICES, frozenset)
    # Every name exists in the bridge's code maximum (branch 1a).
    assert home_direct.DIRECT_MEDIA_SERVICES <= set(bridge.SERVICES["media_player"])


def test_the_digest_matches_the_value_recorded_in_adr_018():
    expected = home_direct.closed_set_digest(home_direct.DIRECT_MEDIA_SERVICES)
    assert home_direct.DIRECT_MEDIA_DIGEST == expected == home_direct.ADR_018_DIGEST
    # The ADR's implementation note records the same value.
    assert home_direct.ADR_018_DIGEST in ADR.read_text(encoding="utf-8")


@pytest.mark.parametrize("extra", ["turn_on", "select_source", "play_media", "toggle"])
def test_adding_a_name_without_amending_the_pin_fails(extra):
    widened = home_direct.DIRECT_MEDIA_SERVICES | {extra}
    assert home_direct.closed_set_digest(widened) != home_direct.ADR_018_DIGEST
    narrowed = home_direct.DIRECT_MEDIA_SERVICES - {"volume_up"}
    assert home_direct.closed_set_digest(narrowed) != home_direct.ADR_018_DIGEST


def test_the_reason_set_is_closed_and_ordered():
    assert len(set(home_direct.CLASSIFY_REASONS)) == len(home_direct.CLASSIFY_REASONS)
    assert home_direct.CLASSIFY_REASONS[0] == home_direct.UNATTENDED
    assert home_direct.CLASSIFY_REASONS[-2:] == (
        home_direct.RATE_LIMITED,
        home_direct.RATE_STORE_UNAVAILABLE,
    )
    assert home_direct.PROVENANCE_MISMATCH not in home_direct.CLASSIFY_REASONS
    assert home_direct.REASONS == set(home_direct.REASON_TEXT)


# --- the baseline: everything holds ----------------------------------------------


@pytest.mark.parametrize("origin", sorted(ORIGINS))
@pytest.mark.parametrize("service", MEDIA_SERVICES)
def test_every_closed_service_on_a_flagged_tv_is_direct_from_each_origin(
    direct_tv, owner, workspace, ha, origin, service
):
    verdict = classify(owner, workspace, call(service), origin=origin)
    assert verdict == DirectVerdict(direct=True, needs_live_volume=service == "volume_up")
    # Read-only: nothing sent, nothing written, no quota spent.
    assert ha.requests == []
    assert not HomeDirectCall.objects.exists()
    assert not HomeDirectSettings.objects.exists()
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (0, 0)


# --- the D7 table: each reason by exactly its condition ---------------------------


def _other_owner_workspace():
    other = register_user(email="other@test.dev", password="Str0ngDevPass!42")
    return other, Workspace.objects.get(owner=other, type=Workspace.Type.PERSONAL)


def test_unattended(direct_tv, owner, workspace):
    with recorded_unattended_run("run-1"):
        assert classify(owner, workspace).reason == home_direct.UNATTENDED


LOW_TRUST = [
    CHANNEL_CLI,
    CHANNEL_OPENAI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_TELEGRAM,
    CHANNEL_SYSTEM,
    CHANNEL_UNKNOWN,
]


@pytest.mark.parametrize("origin", sorted(ORIGINS))
@pytest.mark.parametrize("channel", LOW_TRUST)
def test_low_trust_channels_are_never_a_direct_origin(direct_tv, owner, workspace, origin, channel):
    for input_mode in ("text", "voice"):
        verdict = classify(owner, workspace, origin=origin, channel=channel, input_mode=input_mode)
        assert verdict.reason == home_direct.ORIGIN_NOT_DIRECT


@pytest.mark.parametrize(
    ("origin", "channel", "input_mode"),
    [
        ("chat", CHANNEL_WEB, "voice"),
        ("chat", CHANNEL_NODE_VOICE, "text"),
        ("voice", CHANNEL_WEB, "text"),
        ("voice", CHANNEL_NODE_VOICE, "voice"),
        ("node", CHANNEL_WEB, "voice"),
        ("node", CHANNEL_NODE_VOICE, "text"),
        ("cli", CHANNEL_CLI, "text"),
        ("system", CHANNEL_SYSTEM, "text"),
        ("", CHANNEL_WEB, "text"),
        (None, CHANNEL_WEB, "text"),
    ],
)
def test_each_origin_only_with_its_own_channel(
    direct_tv, owner, workspace, origin, channel, input_mode
):
    verdict = classify(owner, workspace, origin=origin, channel=channel, input_mode=input_mode)
    assert verdict.reason == home_direct.ORIGIN_NOT_DIRECT


def test_not_owner(direct_tv, owner, workspace):
    # The installation owner flag off.
    owner.is_owner = False
    owner.save()
    assert classify(owner, workspace).reason == home_direct.NOT_OWNER
    owner.is_owner = True
    owner.save()
    # An agent principal, even a member of the workspace.
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    WorkspaceMember.objects.create(workspace=workspace, user=agent, role="member")
    assert classify(agent, workspace).reason == home_direct.NOT_OWNER
    # Another user's personal workspace, and a firm the owner owns.
    _, other_ws = _other_owner_workspace()
    assert classify(owner, other_ws).reason == home_direct.NOT_OWNER
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    assert classify(owner, firm).reason == home_direct.NOT_OWNER
    assert classify(None, workspace).reason == home_direct.NOT_OWNER


def test_another_user_never_classifies_his_own_flagged_tv(direct_tv):
    other, other_ws = _other_owner_workspace()
    flagged(other_ws)
    assert classify(other, other_ws).reason == home_direct.NOT_OWNER


def test_kill_switch_env(direct_tv, owner, workspace, ha_settings):
    ha_settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    assert classify(owner, workspace).reason == home_direct.KILL_SWITCH_ENV


def test_kill_switch_owner(direct_tv, owner, workspace):
    home_direct.update_settings(
        actor=owner, workspace=workspace, channel=CHANNEL_WEB, kill_switch=True
    )
    assert classify(owner, workspace).reason == home_direct.KILL_SWITCH_OWNER
    home_direct.update_settings(
        actor=owner, workspace=workspace, channel=CHANNEL_WEB, kill_switch=False
    )
    assert classify(owner, workspace).direct is True


def test_both_kill_switches_name_the_env_first(direct_tv, owner, workspace, ha_settings):
    home_direct.update_settings(
        actor=owner, workspace=workspace, channel=CHANNEL_WEB, kill_switch=True
    )
    ha_settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    assert classify(owner, workspace).reason == home_direct.KILL_SWITCH_ENV


@pytest.mark.parametrize(
    "arguments",
    [
        call("turn_on", entity_id="light.salon", domain="light"),
        call("turn_off", entity_id="switch.priz", domain="switch"),
        call("turn_on", entity_id="scene.film_gecesi", domain="scene"),
        {
            "domain": "wake_on_lan",
            "service": "send_magic_packet",
            "data": {"mac": "AA:BB:CC:DD:EE:FF"},
        },
        call("media_pause", entity_id="lock.kapi", domain="lock"),
        call("media_pause", domain="media_player_x"),
        {},
        None,
    ],
)
def test_not_media_player(direct_tv, owner, workspace, arguments):
    for entity_id in ("light.salon", "scene.film_gecesi"):
        allow(workspace, entity_id)
    verdict = classify(owner, workspace, arguments if arguments is not None else "junk")
    assert verdict.reason == home_direct.NOT_MEDIA_PLAYER


@pytest.mark.parametrize("service", ["turn_on", "turn_off", "select_source", "toggle", "", None])
def test_service_not_direct_on_a_flagged_tv(direct_tv, owner, workspace, service):
    arguments = call("media_pause")
    arguments["service"] = service
    if service == "select_source":
        arguments["data"] = {"source": "HDMI 1"}
    assert classify(owner, workspace, arguments).reason == home_direct.SERVICE_NOT_DIRECT


@pytest.mark.parametrize(
    "arguments",
    [
        call("volume_set", data={"volume_level": 1.5}),
        call("volume_set", data={}),
        call("volume_mute", data={"is_volume_muted": "yes"}),
        call("media_pause", data={"volume_level": 0.2}),
        # A model's `"direct": true` is never a flag: the strict serializer.
        {**call("media_pause"), "direct": True},
        call("media_pause", entity_id="media_player"),
        call("media_pause", entity_id="light.salon"),
    ],
)
def test_arguments_the_strict_serializer_refuses(direct_tv, owner, workspace, arguments):
    assert classify(owner, workspace, arguments).reason == home_direct.ARGUMENTS_INVALID


def test_a_domain_narrowed_away_is_invalid(direct_tv, owner, workspace, ha_settings):
    ha_settings.HA_ALLOWED_DOMAINS = ["light"]
    assert classify(owner, workspace).reason == home_direct.ARGUMENTS_INVALID


def test_entity_not_allowed(bound_ha, owner, workspace):
    assert classify(owner, workspace).reason == home_direct.ENTITY_NOT_ALLOWED
    tv = flagged(workspace, enabled=False)
    assert classify(owner, workspace).reason == home_direct.ENTITY_NOT_ALLOWED
    tv.enabled = True
    tv.save()
    assert classify(owner, workspace).direct is True
    other_tv = call("media_pause", entity_id="media_player.yatak")
    assert classify(owner, workspace, other_tv).reason == home_direct.ENTITY_NOT_ALLOWED


def test_another_workspaces_flagged_tv_never_counts(bound_ha, owner, workspace):
    _, other_ws = _other_owner_workspace()
    flagged(other_ws)
    assert classify(owner, workspace).reason == home_direct.ENTITY_NOT_ALLOWED


def test_service_not_allowed(bound_ha, owner, workspace):
    flagged(workspace, services=["turn_on", "volume_up"])
    assert classify(owner, workspace).reason == home_direct.SERVICE_NOT_ALLOWED
    assert classify(owner, workspace, call("volume_up")).direct is True


def test_not_flagged(bound_ha, owner, workspace):
    allow(workspace, TV)
    assert classify(owner, workspace).reason == home_direct.NOT_FLAGGED


@pytest.mark.parametrize("service", ["turn_on", "turn_off", "select_source"])
def test_a_flagged_tv_with_a_non_direct_service_stays_gate_class(
    direct_tv, owner, workspace, service
):
    data = {"source": "HDMI 1"} if service == "select_source" else {}
    verdict = classify(owner, workspace, call(service, data=data))
    assert verdict.direct is False and verdict.reason == home_direct.SERVICE_NOT_DIRECT


def test_not_available_without_an_enabled_binding(owner, workspace):
    flagged(workspace)
    assert classify(owner, workspace).reason == home_direct.NOT_AVAILABLE


def test_not_available_when_the_binding_is_off_or_the_pin_mismatched(direct_tv, owner, workspace):
    binding = ConnectorBinding.objects.get(workspace=workspace, connector="home_assistant")
    health = dict(binding.last_health)
    health["tools"] = {**health["tools"], "ha_call_service": "mismatch"}
    binding.last_health = health
    binding.save()
    assert classify(owner, workspace).reason == home_direct.NOT_AVAILABLE
    binding.enabled = False
    binding.save()
    assert classify(owner, workspace).reason == home_direct.NOT_AVAILABLE


# --- D9: the volume rules (classification half) ----------------------------------


def _tv(volume):
    if volume is None:
        return salon_tv_state(volume_level=None)
    return salon_tv_state(volume_level=volume)


@pytest.mark.parametrize(
    ("volume", "reason"),
    [
        (0.45, None),
        (0.5, None),
        (0.0, None),
        (0.55, home_direct.VOLUME_AT_MAX),
        (0.6, home_direct.VOLUME_AT_MAX),
        (1.0, home_direct.VOLUME_AT_MAX),
        (None, home_direct.VOLUME_UNKNOWN),
        ("0.2", home_direct.VOLUME_UNKNOWN),
        (True, home_direct.VOLUME_UNKNOWN),
        (float("nan"), home_direct.VOLUME_UNKNOWN),
    ],
)
def test_volume_up_needs_headroom_on_the_given_state(direct_tv, owner, workspace, volume, reason):
    verdict = classify(owner, workspace, call("volume_up"), state=_tv(volume))
    if reason is None:
        assert verdict == DirectVerdict(direct=True)
    else:
        assert verdict.reason == reason


def test_volume_up_without_any_state_is_unknown(direct_tv, owner, workspace):
    assert classify(owner, workspace, call("volume_up"), state=None).reason == (
        home_direct.VOLUME_UNKNOWN
    )
    state = salon_tv_state()
    del state["attributes"]["volume_level"]
    assert classify(owner, workspace, call("volume_up"), state=state).reason == (
        home_direct.VOLUME_UNKNOWN
    )


def test_volume_up_without_a_live_read_is_deferred_to_the_executor(direct_tv, owner, workspace):
    verdict = classify(owner, workspace, call("volume_up"))
    assert verdict.direct is True and verdict.needs_live_volume is True
    # The executor re-runs the rule on its pre-send read (D9).
    assert home_direct.volume_refusal("volume_up", _tv(0.55), Decimal("0.6")) == (
        home_direct.VOLUME_AT_MAX
    )


def test_the_owners_volume_max_governs_the_headroom(direct_tv, owner, workspace):
    home_direct.update_settings(
        actor=owner, workspace=workspace, channel=CHANNEL_WEB, volume_max=Decimal("0.3")
    )
    assert classify(owner, workspace, call("volume_up"), state=_tv(0.25)).reason == (
        home_direct.VOLUME_AT_MAX
    )
    assert classify(owner, workspace, call("volume_up"), state=_tv(0.2)).direct is True


@pytest.mark.parametrize(
    "service", ["volume_down", "volume_mute", "volume_set", "media_pause", "media_play"]
)
def test_no_volume_rule_but_for_volume_up(direct_tv, owner, workspace, service):
    for volume in (None, 0.59, 1.0):
        assert classify(owner, workspace, call(service), state=_tv(volume)).direct is True


@pytest.mark.parametrize(
    ("asked", "maximum", "sent", "requested"),
    [
        (0.9, "0.6", 0.6, {"volume_level": 0.9}),
        (1.0, "0.3", 0.3, {"volume_level": 1.0}),
        (0.6, "0.6", 0.6, None),
        (0.4, "0.6", 0.4, None),
        (0.0, "0.0", 0.0, None),
    ],
)
def test_volume_set_is_clamped_to_the_maximum(asked, maximum, sent, requested):
    arguments = call("volume_set", data={"volume_level": asked})
    clamped, kept = home_direct.clamp_volume(arguments, Decimal(maximum))
    assert clamped["data"] == {"volume_level": sent}
    assert kept == requested
    # The input is never mutated.
    assert arguments["data"] == {"volume_level": asked}


def test_the_clamp_leaves_every_other_service_alone():
    for service in ("volume_up", "media_pause", "volume_mute"):
        arguments = call(service)
        assert home_direct.clamp_volume(arguments, Decimal("0.1")) == (arguments, None)


# --- the order: the first failing condition wins ----------------------------------


def test_the_first_failing_condition_is_the_reason(direct_tv, owner, workspace, ha_settings):
    light = call("turn_on", entity_id="light.salon", domain="light")
    ha_settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    with recorded_unattended_run("run-2"):
        verdict = classify(owner, workspace, light, channel=CHANNEL_CLI)
    assert verdict.reason == home_direct.UNATTENDED
    assert classify(owner, workspace, light, channel=CHANNEL_CLI).reason == (
        home_direct.ORIGIN_NOT_DIRECT
    )
    owner.is_owner = False
    assert classify(owner, workspace, light).reason == home_direct.NOT_OWNER
    owner.is_owner = True
    assert classify(owner, workspace, light).reason == home_direct.KILL_SWITCH_ENV
    ha_settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    assert classify(owner, workspace, light).reason == home_direct.NOT_MEDIA_PLAYER


def test_every_classify_reason_is_reachable_by_this_module():
    """Completeness: a new reason added to the closed set without a test
    above fails here (the names are the tests' own)."""
    covered = {
        home_direct.UNATTENDED: test_unattended,
        home_direct.ORIGIN_NOT_DIRECT: test_each_origin_only_with_its_own_channel,
        home_direct.NOT_OWNER: test_not_owner,
        home_direct.KILL_SWITCH_ENV: test_kill_switch_env,
        home_direct.KILL_SWITCH_OWNER: test_kill_switch_owner,
        home_direct.NOT_MEDIA_PLAYER: test_not_media_player,
        home_direct.SERVICE_NOT_DIRECT: test_service_not_direct_on_a_flagged_tv,
        home_direct.ARGUMENTS_INVALID: test_arguments_the_strict_serializer_refuses,
        home_direct.ENTITY_NOT_ALLOWED: test_entity_not_allowed,
        home_direct.SERVICE_NOT_ALLOWED: test_service_not_allowed,
        home_direct.NOT_FLAGGED: test_not_flagged,
        home_direct.NOT_AVAILABLE: test_not_available_without_an_enabled_binding,
        home_direct.VOLUME_UNKNOWN: test_volume_up_without_any_state_is_unknown,
        home_direct.VOLUME_AT_MAX: test_volume_up_needs_headroom_on_the_given_state,
        # test_home_direct_rate_limit.py
        home_direct.RATE_LIMITED: "test_the_eleventh_call_on_one_entity_is_rate_limited",
        home_direct.RATE_STORE_UNAVAILABLE: "test_a_cache_failure_is_never_direct",
    }
    assert set(covered) == set(home_direct.CLASSIFY_REASONS)
