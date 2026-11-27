"""ADR-018's direct-execute home media class: the closed set, the classifier,
the rate windows, the volume rules and the owner's settings (Phase 13b
brief D4-D9, D11; branch 1b).

This is the **single** exception to CLAUDE.md rule 4. Branch 1b only stores
and admits: it holds the closed allowlist and its digest, the per-entity
eligibility rule `HomeEntity.save()` enforces, `classify` (the D7 order,
read-only), the D8 rate windows (`peek_direct_calls` for the classifier,
`count_direct_call` for the executor's single increment right before the
send), the D9 volume rules on a given state, the owner-web settings service
and the calls selector. **No executor exists yet** (branch 1c): nothing in
this module sends a request to Home Assistant or writes a `HomeDirectCall`,
and `tools.home_ports.run_home_direct` therefore answers "not direct".

Writers: `HomeDirectSettings` is created and changed only by
`get_settings` / `update_settings` here (installation owner, web channel,
personal workspace, audited `ha.direct_settings_changed`); the `direct`
flag only by `connectors.home_entities`. No tool, wake route, schedule,
probe, manifest, node capability or management command reaches either
(`connectors/tests/test_ha_allowlist_gate.py` walks the registries).
"""

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from audit.services import record_audit
from connectors.bridges import home_assistant as bridge
from connectors.models import (
    HOME_DIRECT_VOLUME_DECIMALS,
    HomeDirectCall,
    HomeDirectSettings,
    HomeEntity,
)
from connectors.services import ConnectorPermissionDenied, ConnectorServiceError
from core import unattended
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
from core.owner_web import OwnerWebRequired, require_owner_web
from tools.home_ports import DirectVerdict
from workspaces.models import Workspace

UNSET = object()

# --- D4: the closed allowlist (ADR-018 decision 1) ---------------------------

DIRECT_MEDIA_DOMAIN = "media_player"
DIRECT_MEDIA_SERVICES = frozenset(
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


def closed_set_digest(services):
    """sha256 of the sorted service names joined by a newline."""
    return hashlib.sha256("\n".join(sorted(services)).encode("utf-8")).hexdigest()


DIRECT_MEDIA_DIGEST = closed_set_digest(DIRECT_MEDIA_SERVICES)
# The value recorded in ADR-018's implementation note
# (docs/adr/ADR-018-direct-home-media-class.md). A test and the closeout's
# `ha_direct_media` preflight row compare the computed digest with it:
# changing the set without an ADR amendment fails CI and preflight.
ADR_018_DIGEST = "9ca85ee245440d6c31779bf26dfd256489eeafff83e3e678bc3c7572ad2d3183"

# `HomeEntity.save()`'s refusal code for a flag outside the class (D5).
DIRECT_NOT_ALLOWED = "ha_entity.direct_not_allowed"

# D9: a direct `volume_up` needs this much headroom below the maximum on the
# live read (a code constant; Phase 13b Q2).
DIRECT_VOLUME_UP_HEADROOM = Decimal("0.1")

# --- the origins (D7 condition 1) --------------------------------------------

ORIGIN_VOICE = HomeDirectCall.Origin.VOICE.value
ORIGIN_NODE = HomeDirectCall.Origin.NODE.value
ORIGIN_CHAT = HomeDirectCall.Origin.CHAT.value
# origin -> (the only channel, the only input mode) it is admitted with:
# web voice, a paired node's voice child, typed web chat. Every other
# channel (CLI, the OpenAI facade, Telegram, hooks, a node credential,
# system) is never a direct origin.
INPUT_MODE_VOICE = "voice"
INPUT_MODE_TEXT = "text"
ORIGIN_CHANNELS = {
    ORIGIN_VOICE: (CHANNEL_WEB, INPUT_MODE_VOICE),
    ORIGIN_NODE: (CHANNEL_NODE_VOICE, INPUT_MODE_VOICE),
    ORIGIN_CHAT: (CHANNEL_WEB, INPUT_MODE_TEXT),
}

# --- the closed reason set, in the D7 order ----------------------------------

UNATTENDED = "unattended"
ORIGIN_NOT_DIRECT = "origin_not_direct"
NOT_OWNER = "not_owner"
KILL_SWITCH_ENV = "kill_switch_env"
KILL_SWITCH_OWNER = "kill_switch_owner"
NOT_MEDIA_PLAYER = "not_media_player"
SERVICE_NOT_DIRECT = "service_not_direct"
ARGUMENTS_INVALID = "arguments_invalid"
ENTITY_NOT_ALLOWED = "entity_not_allowed"
SERVICE_NOT_ALLOWED = "service_not_allowed"
NOT_FLAGGED = "not_flagged"
NOT_AVAILABLE = "not_available"
VOLUME_UNKNOWN = "volume_unknown"
VOLUME_AT_MAX = "volume_at_max"
RATE_LIMITED = "rate_limited"
RATE_STORE_UNAVAILABLE = "rate_store_unavailable"
# Executor-only (branch 1c, D10a lock 3): the stored text no longer resolves
# to the classification's arguments. Not one of `classify`'s conditions.
PROVENANCE_MISMATCH = "provenance_mismatch"

CLASSIFY_REASONS = (
    UNATTENDED,
    ORIGIN_NOT_DIRECT,
    NOT_OWNER,
    KILL_SWITCH_ENV,
    KILL_SWITCH_OWNER,
    NOT_MEDIA_PLAYER,
    SERVICE_NOT_DIRECT,
    ARGUMENTS_INVALID,
    ENTITY_NOT_ALLOWED,
    SERVICE_NOT_ALLOWED,
    NOT_FLAGGED,
    NOT_AVAILABLE,
    VOLUME_UNKNOWN,
    VOLUME_AT_MAX,
    RATE_LIMITED,
    RATE_STORE_UNAVAILABLE,
)
EXECUTOR_REASONS = (PROVENANCE_MISMATCH,)
REASONS = frozenset(CLASSIFY_REASONS + EXECUTOR_REASONS)

# The short Turkish text the card shows after "Doğrudan değil: " (D7).
REASON_TEXT = {
    UNATTENDED: "sahip yokken çalışan bir iş",
    ORIGIN_NOT_DIRECT: "bu kanaldan doğrudan çağrı yapılmaz",
    NOT_OWNER: "yalnızca sahibin kişisel alanında",
    KILL_SWITCH_ENV: "doğrudan medya ortam ayarıyla kapalı",
    KILL_SWITCH_OWNER: "doğrudan medya kapalı",
    NOT_MEDIA_PLAYER: "yalnızca medya oynatıcılar",
    SERVICE_NOT_DIRECT: "bu işlem doğrudan sınıfta değil",
    ARGUMENTS_INVALID: "istek geçersiz",
    ENTITY_NOT_ALLOWED: "cihaz izin listesinde değil",
    SERVICE_NOT_ALLOWED: "bu işlem bu cihaz için izinli değil",
    NOT_FLAGGED: "işaretli değil",
    NOT_AVAILABLE: "Home Assistant şu an kullanılamıyor",
    VOLUME_UNKNOWN: "güncel ses düzeyi bilinmiyor",
    VOLUME_AT_MAX: "ses en yüksek düzeye yakın",
    RATE_LIMITED: "dakikalık sınır aşıldı",
    RATE_STORE_UNAVAILABLE: "sınır sayacı şu an kullanılamıyor",
    PROVENANCE_MISMATCH: "istek söylediğinle eşleşmiyor",
}

# The state of the class for the owner's settings view and the HUD (D6).
STATE_ON = "on"
STATE_OFF_OWNER = "off_owner"
STATE_OFF_ENV = "off_env"

GATE = "ha_call_service"


# --- eligibility (D5) --------------------------------------------------------


def direct_services(entity_id, services):
    """`services` ∩ the closed set, in the code maximum's order; empty for
    any entity outside the `media_player` domain."""
    if bridge.entity_domain(entity_id) != DIRECT_MEDIA_DOMAIN:
        return []
    services = [s for s in (services or []) if isinstance(s, str)]
    return [
        s
        for s in sorted(bridge.SERVICES[DIRECT_MEDIA_DOMAIN])
        if s in services and s in DIRECT_MEDIA_SERVICES
    ]


def direct_eligible(entity_id, services):
    """May an entity with these services carry `direct=True` at all?"""
    return bool(direct_services(entity_id, services))


# --- settings ----------------------------------------------------------------


def default_volume_max():
    return _quantize(Decimal(str(settings.HA_DIRECT_VOLUME_MAX)))


def _quantize(value):
    return value.quantize(Decimal(1).scaleb(-HOME_DIRECT_VOLUME_DECIMALS), rounding=ROUND_HALF_UP)


def env_kill_switch():
    return bool(settings.HA_DIRECT_MEDIA_KILL_SWITCH)


def rate_limits():
    """`(global per minute, per entity per minute)`; 0 disables the class."""
    return (
        int(settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE),
        int(settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE),
    )


@dataclass(frozen=True)
class EffectiveSettings:
    """What the classifier reads: the row's values, or the defaults while
    the owner has not opened the settings yet (no row is ever created
    here)."""

    kill_switch: bool
    volume_max: Decimal


def effective_settings(*, actor, workspace, for_update=False):
    queryset = HomeDirectSettings.objects.for_actor(actor, workspace=workspace)
    if for_update:
        queryset = queryset.select_for_update()
    row = queryset.first()
    if row is None:
        return EffectiveSettings(kill_switch=False, volume_max=default_volume_max())
    return EffectiveSettings(kill_switch=row.kill_switch, volume_max=_quantize(row.volume_max))


def direct_state(*, kill_switch):
    if env_kill_switch():
        return STATE_OFF_ENV
    if kill_switch:
        return STATE_OFF_OWNER
    return STATE_ON


# --- D8: the rate windows ----------------------------------------------------

RATE_WINDOW_SECONDS = 60
LIMIT_GLOBAL = "global"
LIMIT_ENTITY = "entity"


class RateStoreUnavailable(Exception):  # noqa: N818 -- the brief's name
    """The cache failed: the call is not direct (`rate_store_unavailable`)."""


def _now_seconds():
    return int(timezone.now().timestamp())


def _global_key(workspace_id, second):
    return f"connectors:ha-direct:{workspace_id}:{second}"


def _entity_key(workspace_id, entity_id, second):
    return f"connectors:ha-direct:{workspace_id}:{entity_id}:{second}"


def _window(make_key, now):
    return [make_key(now - offset) for offset in range(RATE_WINDOW_SECONDS)]


def _keys(workspace_id, entity_id, now):
    return (
        _window(lambda s: _global_key(workspace_id, s), now),
        _window(lambda s: _entity_key(workspace_id, entity_id, s), now),
    )


def peek_direct_calls(workspace_id, entity_id):
    """`(global, entity)` direct calls counted in the last 60 s -- read only
    (the classifier's view; Phase 13b follow-up 1: classify never spends
    quota). Raises `RateStoreUnavailable` on any cache error."""
    now = _now_seconds()
    global_keys, entity_keys = _keys(workspace_id, entity_id, now)
    try:
        values = cache.get_many(global_keys + entity_keys)
        global_count = sum(int(values[k]) for k in global_keys if k in values)
        entity_count = sum(int(values[k]) for k in entity_keys if k in values)
    except Exception as exc:  # noqa: BLE001 -- any store failure fails closed
        raise RateStoreUnavailable(type(exc).__name__) from None
    return global_count, entity_count


def _incr(key):
    timeout = RATE_WINDOW_SECONDS + 10
    cache.add(key, 0, timeout=timeout)
    try:
        cache.incr(key)
    except ValueError:
        # Expired or evicted between add and incr (the voice-storm pattern):
        # start it again at 1 -- with add, so a concurrent call that
        # recreated it first is counted on top of, not clobbered.
        if not cache.add(key, 1, timeout=timeout):
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=timeout)


def _take_back(key):
    try:
        cache.decr(key)
    except ValueError:
        pass  # the bucket is gone: nothing left to take back


def over_limit(global_count, entity_count):
    """`global` / `entity` when a count is at or over its limit (0 = the
    class is disabled), else `None`."""
    global_limit, entity_limit = rate_limits()
    if global_limit <= 0 or global_count >= global_limit:
        return LIMIT_GLOBAL
    if entity_limit <= 0 or entity_count >= entity_limit:
        return LIMIT_ENTITY
    return None


def count_direct_call(workspace_id, entity_id):
    """Count one direct call about to be sent -- the **single** increment of
    each window per call (branch 1c's executor, D10 step 3, immediately
    before the send). Returns `None` when the call is within both limits,
    else the exceeded window (`global` / `entity`), in which case the
    increments are taken back: a call that falls back to the card spends no
    quota. Raises `RateStoreUnavailable` on any cache error."""
    global_limit, entity_limit = rate_limits()
    if global_limit <= 0:
        return LIMIT_GLOBAL
    if entity_limit <= 0:
        return LIMIT_ENTITY
    now = _now_seconds()
    global_bucket = _global_key(workspace_id, now)
    entity_bucket = _entity_key(workspace_id, entity_id, now)
    try:
        _incr(global_bucket)
        _incr(entity_bucket)
        global_count, entity_count = peek_direct_calls(workspace_id, entity_id)
        exceeded = None
        if global_count > global_limit:
            exceeded = LIMIT_GLOBAL
        elif entity_count > entity_limit:
            exceeded = LIMIT_ENTITY
        if exceeded is not None:
            _take_back(global_bucket)
            _take_back(entity_bucket)
    except RateStoreUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- any store failure fails closed
        raise RateStoreUnavailable(type(exc).__name__) from None
    return exceeded


def record_rate_limited(*, actor, workspace, arguments, origin, limit):
    record_audit(
        action="ha.direct_rate_limited",
        actor=actor,
        workspace=workspace,
        metadata={
            "entity_id": (arguments or {}).get("entity_id"),
            "service": (arguments or {}).get("service"),
            "origin": origin,
            "limit": limit,
        },
    )


# --- D9: the volume rules ----------------------------------------------------


def _number(value):
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def live_volume(state):
    """The live `volume_level` of a public state, or `None`."""
    attributes = (state or {}).get("attributes") if isinstance(state, dict) else None
    if not isinstance(attributes, dict):
        return None
    return _number(attributes.get("volume_level"))


def volume_refusal(service, state, volume_max):
    """D9 on a given (pre-send) state: `volume_unknown` / `volume_at_max`
    for a `volume_up` that may not run directly, else `None`. Only
    `volume_up` has a rule here; `volume_set` is clamped instead
    (`clamp_volume`), every other service has no volume rule."""
    if service != "volume_up":
        return None
    level = live_volume(state)
    if level is None:
        return VOLUME_UNKNOWN
    if level + DIRECT_VOLUME_UP_HEADROOM > Decimal(volume_max):
        return VOLUME_AT_MAX
    return None


def clamp_volume(arguments, volume_max):
    """`(arguments to send, requested data or None)`: a direct `volume_set`
    above the maximum is sent at the maximum and the asked value is kept
    for `HomeDirectCall.requested_data`. Never applied to a gate-class
    call."""
    arguments = dict(arguments or {})
    if arguments.get("service") != "volume_set":
        return arguments, None
    data = dict(arguments.get("data") or {})
    level = _number(data.get("volume_level"))
    maximum = Decimal(volume_max)
    if level is None or level <= maximum:
        return arguments, None
    requested = dict(data)
    data["volume_level"] = float(maximum)
    arguments["data"] = data
    return arguments, requested


# --- D7: the classifier ------------------------------------------------------

# `classify(state=...)` default: no live state was read at classification
# time. A `volume_up` then passes condition 7 with `needs_live_volume=True`
# and the executor applies `volume_refusal` to its pre-send read (D9).
LIVE_STATE_DEFERRED = object()


def _verdict(reason, **extra):
    return DirectVerdict(direct=False, reason=reason, **extra)


def _is_owner_personal(actor, workspace):
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    if getattr(actor, "is_agent", False) or not getattr(actor, "is_active", False):
        return False
    if not getattr(actor, "is_owner", False):
        return False
    if workspace is None or getattr(workspace, "type", None) != Workspace.Type.PERSONAL:
        return False
    return str(getattr(workspace, "owner_id", None)) == str(actor.pk)


def _available(workspace):
    from connectors.manifest import STATUS_ACTIVE, get_manifest
    from connectors.services import enabled_binding, tool_available

    manifest = get_manifest("home_assistant")
    if manifest is None or manifest.status != STATUS_ACTIVE:
        return False
    policy = manifest.get_tool(GATE)
    return policy is not None and tool_available(enabled_binding(workspace, manifest.name), policy)


def classify(
    *,
    workspace,
    actor,
    arguments,
    origin,
    channel,
    input_mode,
    state=LIVE_STATE_DEFERRED,
    audit_rate_limit=True,
):
    """ADR-018's admission check, the D7 order. `direct` only when every
    condition holds; otherwise the first failing closed reason. Read-only:
    no Home Assistant request, no row created, no rate quota spent (the
    windows are only peeked; `count_direct_call` is the executor's). A
    `rate_limited` verdict is audited `ha.direct_rate_limited` (unless
    `audit_rate_limit=False`, for a caller re-checking a verdict it already
    recorded). Must only ever be given the deterministic home matcher's
    arguments (D7, D10a) -- never router-extracted ones.

    1. not unattended; `origin` with its own channel and input mode;
    2. the installation owner in his personal workspace;
    3. env kill switch, owner kill switch;
    4. the `media_player` domain, a service of the closed set, arguments the
       bridge's strict serializer accepts;
    5. an enabled allowlisted entity (`for_actor()`), the service ticked
       on it, the entity flagged `direct`;
    6. the manifest active and `ha_call_service` enabled with status `ok`;
    7. the volume rules (D9);
    8. the rate windows (D8), last."""
    # 1
    if unattended.current() is not None:
        return _verdict(UNATTENDED)
    expected = ORIGIN_CHANNELS.get(origin) if isinstance(origin, str) else None
    if expected is None or (channel, input_mode) != expected:
        return _verdict(ORIGIN_NOT_DIRECT)
    # 2
    if not _is_owner_personal(actor, workspace):
        return _verdict(NOT_OWNER)
    # 3
    if env_kill_switch():
        return _verdict(KILL_SWITCH_ENV)
    current = effective_settings(actor=actor, workspace=workspace)
    if current.kill_switch:
        return _verdict(KILL_SWITCH_OWNER)
    # 4
    arguments = arguments if isinstance(arguments, dict) else {}
    if arguments.get("domain") != DIRECT_MEDIA_DOMAIN:
        return _verdict(NOT_MEDIA_PLAYER)
    service = arguments.get("service")
    if not isinstance(service, str) or service not in DIRECT_MEDIA_SERVICES:
        return _verdict(SERVICE_NOT_DIRECT)
    serializer = bridge.CallServiceArguments(data=arguments)
    if not serializer.is_valid():
        return _verdict(ARGUMENTS_INVALID)
    clean = dict(serializer.validated_data)
    entity_id = clean["entity_id"]
    # 5
    entity = (
        HomeEntity.objects.for_actor(actor, workspace=workspace)
        .filter(entity_id=entity_id, enabled=True)
        .first()
    )
    if entity is None:
        return _verdict(ENTITY_NOT_ALLOWED)
    if service not in (entity.services or []):
        return _verdict(SERVICE_NOT_ALLOWED)
    if not entity.direct:
        return _verdict(NOT_FLAGGED)
    # 6
    if not _available(workspace):
        return _verdict(NOT_AVAILABLE)
    # 7
    needs_live_volume = False
    if state is LIVE_STATE_DEFERRED:
        needs_live_volume = service == "volume_up"
    else:
        refusal = volume_refusal(service, state, current.volume_max)
        if refusal is not None:
            return _verdict(refusal)
    # 8
    try:
        limit = over_limit(*peek_direct_calls(workspace.pk, entity_id))
    except RateStoreUnavailable:
        return _verdict(RATE_STORE_UNAVAILABLE)
    if limit is not None:
        if audit_rate_limit:
            record_rate_limited(
                actor=actor, workspace=workspace, arguments=clean, origin=origin, limit=limit
            )
        return _verdict(RATE_LIMITED, limit=limit)
    return DirectVerdict(direct=True, needs_live_volume=needs_live_volume)


# --- the owner's settings service (D6) ----------------------------------------


class HomeDirectError(ConnectorServiceError):
    code = "ha_direct.invalid"
    http_status = 400


class HomeDirectInvalid(HomeDirectError):
    """A malformed settings change (`volume_max` outside 0-1, ...)."""

    code = "ha_direct.invalid"


class HomeDirectPermissionDenied(ConnectorPermissionDenied):
    """Not the owner over the web, or not the personal workspace (403)."""


def require_direct_owner_web(*, actor, workspace, channel):
    try:
        require_owner_web(actor=actor, workspace=workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise HomeDirectPermissionDenied(str(exc)) from exc
    if workspace.type != Workspace.Type.PERSONAL:
        # The views answer 404 before this; a direct caller is refused too.
        raise HomeDirectPermissionDenied("Direct media settings belong to the personal workspace.")


def get_settings(*, actor, workspace, channel):
    """The owner's settings row, created with the defaults on his first
    read (owner, web, personal workspace)."""
    require_direct_owner_web(actor=actor, workspace=workspace, channel=channel)
    row = HomeDirectSettings.objects.for_actor(actor, workspace=workspace).first()
    if row is not None:
        return row
    with transaction.atomic():
        row, _ = HomeDirectSettings.objects.get_or_create(
            workspace=workspace, defaults={"kill_switch": False, "volume_max": default_volume_max()}
        )
    return row


def _clean_volume_max(value):
    if isinstance(value, bool):
        raise HomeDirectInvalid("volume_max must be a number from 0 to 1.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HomeDirectInvalid("volume_max must be a number from 0 to 1.") from None
    if not number.is_finite() or number < 0 or number > 1:
        raise HomeDirectInvalid("volume_max must be a number from 0 to 1.")
    return _quantize(number)


@transaction.atomic
def update_settings(*, actor, workspace, channel, kill_switch=UNSET, volume_max=UNSET):
    """Change the owner kill switch and/or the volume maximum (owner, web,
    personal workspace). Audited `ha.direct_settings_changed` with the
    changed fields and their new values; a no-op writes nothing. The kill
    switch takes effect from the next classification (no queue)."""
    require_direct_owner_web(actor=actor, workspace=workspace, channel=channel)
    get_settings(actor=actor, workspace=workspace, channel=channel)
    row = HomeDirectSettings.objects.for_actor(actor, workspace=workspace).select_for_update().get()
    changed = {}
    if kill_switch is not UNSET:
        if not isinstance(kill_switch, bool):
            raise HomeDirectInvalid("kill_switch must be true or false.")
        if kill_switch != row.kill_switch:
            row.kill_switch = kill_switch
            changed["kill_switch"] = kill_switch
    if volume_max is not UNSET:
        clean = _clean_volume_max(volume_max)
        if clean != _quantize(row.volume_max):
            row.volume_max = clean
            changed["volume_max"] = str(clean)
    if not changed:
        return row
    row.save(update_fields=[*sorted(changed), "updated_at"])
    record_audit(
        action="ha.direct_settings_changed",
        actor=actor,
        workspace=workspace,
        resource=row,
        metadata={"fields": sorted(changed), **changed},
    )
    return row


def flagged_count(*, actor, workspace):
    return (
        HomeEntity.objects.for_actor(actor, workspace=workspace)
        .filter(direct=True, enabled=True)
        .count()
    )


def settings_view(*, actor, workspace, row):
    """The `GET/PATCH /direct` body (D6)."""
    global_limit, entity_limit = rate_limits()
    return {
        "workspace": str(workspace.pk),
        "kill_switch": row.kill_switch,
        "env_kill_switch": env_kill_switch(),
        "state": direct_state(kill_switch=row.kill_switch),
        "volume_max": float(_quantize(row.volume_max)),
        "rate_global_per_minute": global_limit,
        "rate_entity_per_minute": entity_limit,
        "services": sorted(DIRECT_MEDIA_SERVICES),
        "digest": DIRECT_MEDIA_DIGEST,
        "flagged_count": flagged_count(actor=actor, workspace=workspace),
    }


# --- the calls selector (D11) --------------------------------------------------

CALLS_LIMIT_MAX = 100
CALLS_LIMIT_DEFAULT = 20


def list_calls(*, actor, workspace, channel, limit=CALLS_LIMIT_DEFAULT):
    """The workspace's direct calls, newest first, at most 100 (owner, web,
    personal workspace, `for_actor()`)."""
    require_direct_owner_web(actor=actor, workspace=workspace, channel=channel)
    limit = max(1, min(int(limit), CALLS_LIMIT_MAX))
    return list(
        HomeDirectCall.objects.for_actor(actor, workspace=workspace).order_by("-created_at", "-id")[
            :limit
        ]
    )


def register():
    """`ConnectorsConfig.ready`: the direct classifier port only. Branch 1b
    registers **no executor** (`tools.home_ports.run_home_direct` answers
    `not_direct`), so no turn behaves differently yet."""
    from tools.home_ports import register_direct_classifier

    register_direct_classifier(classify)
