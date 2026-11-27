"""ADR-018's direct-execute home media class: the closed set, the classifier,
the rate windows, the volume rules, the executor and the owner's settings
(Phase 13b brief D4-D11; branches 1b and 1c).

This is the **single** exception to CLAUDE.md rule 4. It holds the closed
allowlist and its digest, the per-entity eligibility rule
`HomeEntity.save()` enforces, `classify` (the D7 order, read-only), the D8
rate windows (`peek_direct_calls` for the classifier, `count_direct_call`
for the executor's single increment right before the send), the D9 volume
rules, the owner-web settings service, the calls selector and -- branch 1c
-- `execute`, the one direct send (D10 steps 1-6 after the D10a
re-resolution of the owner's stored words), plus `mark_lost_direct_calls`
(the recovery scan: a lost `running` call -> `unknown_outcome`, never
re-sent).

Writers: `HomeDirectSettings` is created and changed only by
`get_settings` / `update_settings` here (installation owner, web channel,
personal workspace, audited `ha.direct_settings_changed`); the `direct`
flag only by `connectors.home_entities`; `HomeDirectCall` only by
`execute` (through `_start_call` / `_finalize_call`) and the recovery scan.
No tool, wake route, schedule, probe, manifest, node capability or
management command reaches any of them
(`connectors/tests/test_ha_allowlist_gate.py` walks the registries).
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from connectors.bridges import home_assistant as bridge
from connectors.models import (
    HOME_DIRECT_VOLUME_DECIMALS,
    HomeDirectCall,
    HomeDirectSettings,
    HomeEntity,
)
from connectors.reads import canonical_json
from connectors.services import ConnectorPermissionDenied, ConnectorServiceError
from core import unattended
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
from core.owner_web import OwnerWebRequired, require_owner_web
from tools.home_ports import DirectVerdict
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

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
# Executor-only (branch 1c, D10 step 5): the pre-send read of the entity
# refused the call (gone from Home Assistant, unreadable, the arguments no
# longer admitted) or the send itself was refused before anything reached
# the transport. Nothing was sent; the turn becomes the card.
TARGET_UNAVAILABLE = "target_unavailable"

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
EXECUTOR_REASONS = (PROVENANCE_MISMATCH, TARGET_UNAVAILABLE)
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
    TARGET_UNAVAILABLE: "cihaz şu an doğrulanamadı",
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


def env_disabled():
    """The environment turns the class off: the env kill switch, or a rate
    limit of 0 (documented in `.env.example`: 0 disables)."""
    return env_kill_switch() or min(rate_limits()) <= 0


def direct_state(*, kill_switch):
    if env_disabled():
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
    quota. Raises `RateStoreUnavailable` on any cache error, after taking
    back (best effort) any increment already made."""
    global_limit, entity_limit = rate_limits()
    if global_limit <= 0:
        return LIMIT_GLOBAL
    if entity_limit <= 0:
        return LIMIT_ENTITY
    now = _now_seconds()
    global_bucket = _global_key(workspace_id, now)
    entity_bucket = _entity_key(workspace_id, entity_id, now)
    counted = []
    try:
        _incr(global_bucket)
        counted.append(global_bucket)
        _incr(entity_bucket)
        counted.append(entity_bucket)
        global_count, entity_count = peek_direct_calls(workspace_id, entity_id)
        exceeded = None
        if global_count > global_limit:
            exceeded = LIMIT_GLOBAL
        elif entity_count > entity_limit:
            exceeded = LIMIT_ENTITY
        if exceeded is not None:
            _take_back(global_bucket)
            _take_back(entity_bucket)
    except Exception as exc:  # noqa: BLE001 -- any store failure fails closed
        # The call falls back to the card, so it spends no quota: take back
        # whatever was already counted (best effort -- the store is failing).
        _take_back_quietly(counted)
        if isinstance(exc, RateStoreUnavailable):
            raise
        raise RateStoreUnavailable(type(exc).__name__) from None
    return exceeded


def _take_back_quietly(keys):
    for key in keys:
        try:
            _take_back(key)
        except Exception:  # noqa: BLE001, S110 -- best effort on a failing store
            pass


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


def ha_usable(workspace):
    """Is Home Assistant usable for a direct call in `workspace` (the
    manifest active, the binding enabled, `ha_call_service`'s pin `ok`)?
    Read-only; the closeout's `ha_direct_media` preflight row asks it."""
    return _available(workspace)


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


# --- D10/D10a: the executor (branch 1c) --------------------------------------

# `assistant.Message.Intent.HOME_DIRECT` (this app never imports `assistant`).
HOME_DIRECT_INTENT = "home_direct"
UNATTENDED_EFFECT = "ha_direct_call"

# Fixed Turkish reply templates (Friday's text, never Home Assistant's).
FALLBACK_REPLY = "Bunu doğrudan yapmadım ({reason}); onay kartı ekranda."
UNKNOWN_OUTCOME_REPLY = "{name} yanıt vermedi; komut gitmiş olabilir, tekrar denemedim."
REFUSED_REPLY = "Home Assistant {name} komutunu reddetti; tekrar denemedim."
CLAMPED_REPLY = "Ses en fazla %{maximum} olabilir; %{maximum}{suffix} ayarladım."
_DONE_REPLIES = {
    "media_play": "{name} oynatılıyor.",
    "media_pause": "{name} duraklatıldı.",
    "media_stop": "{name} durduruldu.",
    "volume_up": "{name}: ses açıldı.",
    "volume_down": "{name}: ses kısıldı.",
}
_PLAY_PAUSE_REPLIES = {
    "playing": "{name} oynatılıyor.",
    "paused": "{name} duraklatıldı.",
}
PLAY_PAUSE_REPLY = "{name}: oynat/duraklat gönderildi."
VOLUME_SET_REPLY = "{name}: ses %{percent}{suffix} ayarlandı."
VOLUME_STEP_REPLY = "{name}: ses {verb} (%{percent})."
MUTE_REPLY = "{name}: ses kapatıldı."
UNMUTE_REPLY = "{name}: sessiz mod kapatıldı."

# The Turkish dative suffix after a percentage, by its last spoken word.
_DATIVE_UNITS = {1: "'e", 2: "'ye", 3: "'e", 4: "'e", 5: "'e", 6: "'ya", 7: "'ye", 8: "'e", 9: "'a"}
_DATIVE_TENS = {
    0: "'a",
    1: "'a",
    2: "'ye",
    3: "'a",
    4: "'a",
    5: "'ye",
    6: "'a",
    7: "'e",
    8: "'e",
    9: "'a",
}


def dative_suffix(number):
    """`'a` / `'e` / `'ya` / `'ye` for an integer percentage 0-100 ("%60'a",
    "%50'ye", "%100'e")."""
    number = int(number)
    if number == 100:
        return "'e"
    if number % 10:
        return _DATIVE_UNITS[number % 10]
    return _DATIVE_TENS[(number // 10) % 10]


def _percent(level):
    return int((Decimal(str(level)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# The call's `ConnectorError` codes -> `ConnectorWriteExecution.ErrorType`.
_ERROR_TYPES = {
    "pin_mismatch": "pin_mismatch",
    "tool_missing": "pin_mismatch",
    "not_configured": "not_configured",
    "reconnect_required": "not_configured",
    "timeout": "timeout",
    "transport_error": "transport_error",
    "protocol_error": "protocol_error",
    "target_missing": "target_missing",
    "not_allowed": "connector_changed",
    "scope_mismatch": "connector_changed",
}
# An unsent failure of these codes means the connector itself is not usable
# (the card will say so); any other unsent failure is the target's.
_UNAVAILABLE_CODES = frozenset({"pin_mismatch", "tool_missing", "not_configured"})


def _fallback(reason, *, arguments=None, reply=None):
    """Nothing was sent: the turn becomes the gate-class card with
    `arguments` (when given) or answers `reply` with no card."""
    from tools.home_ports import DIRECT_RUN_NOT_DIRECT, DirectRun

    if arguments:
        text = reply or FALLBACK_REPLY.format(reason=REASON_TEXT.get(reason, reason))
        return DirectRun(
            outcome=DIRECT_RUN_NOT_DIRECT,
            reason=reason,
            reply=text,
            tool_name=GATE,
            arguments=dict(arguments),
        )
    return DirectRun(outcome=DIRECT_RUN_NOT_DIRECT, reason=reason, reply=reply or "")


def _same_node(a, b):
    return (str(a) if a else None) == (str(b) if b else None)


def _provenance_refusal(*, workspace, actor, message, origin, channel, node_id):
    """D10a lock 3: `(resolution, None)` when the saved message is this
    turn's own `home_direct` message and the owner's stored words still
    resolve to exactly its arguments; else `(resolution or None, detail)`.
    `detail` is a closed token (never the arguments' text)."""
    from connectors.home_phrases import resolve_home_command
    from tools.home_ports import OUTCOME_DRAFT

    if (
        message is None
        or getattr(message, "role", None) != "user"
        or getattr(message, "intent", None) != HOME_DIRECT_INTENT
        or str(getattr(message, "workspace_id", "")) != str(workspace.pk)
        or str(getattr(message, "created_by_id", "")) != str(getattr(actor, "pk", ""))
        or getattr(message, "channel", None) != channel
    ):
        return None, "message"
    payload = message.intent_payload if isinstance(message.intent_payload, dict) else {}
    if payload.get("origin") != origin or not _same_node(payload.get("node_id"), node_id):
        return None, "origin"
    resolution = resolve_home_command(workspace=workspace, actor=actor, text=message.content)
    if resolution is None:
        return None, "resolution_none"
    if resolution.outcome != OUTCOME_DRAFT:
        return resolution, f"resolution_{resolution.outcome}"
    if resolution.tool_name != payload.get("tool_name") or resolution.tool_name != GATE:
        return resolution, "tool"
    if canonical_json(resolution.arguments) != canonical_json(payload.get("arguments")):
        return resolution, "arguments"
    return resolution, None


def _record_provenance_refused(*, actor, workspace, message, origin, detail):
    record_audit(
        action="ha.direct_provenance_refused",
        actor=actor,
        workspace=workspace,
        metadata={
            "source_message_id": str(message.pk) if getattr(message, "pk", None) else None,
            "origin": origin,
            "reason": PROVENANCE_MISMATCH,
            "detail": detail,
        },
    )


def _gate(workspace):
    """`(manifest, policy, binding)` of `ha_call_service`, or Nones."""
    from connectors.manifest import STATUS_ACTIVE, get_manifest
    from connectors.services import enabled_binding, tool_available

    manifest = get_manifest("home_assistant")
    if manifest is None or manifest.status != STATUS_ACTIVE:
        return None, None, None
    policy = manifest.get_tool(GATE)
    binding = enabled_binding(workspace, manifest.name)
    if policy is None or not tool_available(binding, policy):
        return None, None, None
    return manifest, policy, binding


def _state_summary(state):
    """What a call row keeps of a live state: the state token and the two
    volume attributes -- no names, titles or other Home Assistant text."""
    if not isinstance(state, dict):
        return None
    attributes = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
    summary = {"state": str(state.get("state", ""))[:40]}
    level = live_volume(state)
    if level is not None:
        summary["volume_level"] = float(level)
    muted = attributes.get("is_volume_muted")
    if isinstance(muted, bool):
        summary["is_volume_muted"] = muted
    return summary


def _start_call(*, workspace, actor, entity, arguments, requested, origin, node_id, message):
    """The single creation of a call record, `running`, committed before the
    send (so a lost worker leaves a row the recovery scan finalises)."""
    return HomeDirectCall.objects.create(
        workspace=workspace,
        home_entity=entity,
        entity_id=arguments["entity_id"],
        service=arguments["service"],
        data=dict(arguments.get("data") or {}),
        requested_data=requested,
        origin=origin,
        node=node_id if origin == ORIGIN_NODE else None,
        requested_by=actor,
        source_message_id=message.pk,
        status=HomeDirectCall.Status.RUNNING,
        started_at=timezone.now(),
    )


def _audit_call(call, *, actor):
    metadata = {
        "home_direct_call": str(call.pk),
        "source_message_id": str(call.source_message_id) if call.source_message_id else None,
        "origin": call.origin,
        "node": str(call.node) if call.node else None,
        "entity_id": call.entity_id,
        "service": call.service,
        "data": call.data,
        "status": call.status,
        "error_type": call.error_type,
    }
    if call.requested_data is not None:
        metadata["requested_data"] = call.requested_data
    record_audit(
        action="ha.direct_call",
        actor=actor,
        actor_type=AuditLog.ActorType.HUMAN if actor is not None else AuditLog.ActorType.SYSTEM,
        workspace=call.workspace,
        resource=call,
        metadata=metadata,
    )


def _finalize_call(
    call_id, *, actor, status, error_type="", state_before=None, state_after=None, read_back=False
):
    """The single finalize of a `running` call (fenced on the status: a row
    the recovery scan already moved is left alone), audited
    `ha.direct_call`. Returns the row, or None when it was no longer
    running."""
    with transaction.atomic():
        call = (
            HomeDirectCall.objects.select_for_update()
            .filter(pk=call_id, status=HomeDirectCall.Status.RUNNING)
            .first()
        )
        if call is None:
            return None
        call.status = status
        call.error_type = error_type
        call.state_before = _state_summary(state_before)
        call.state_after = _state_summary(state_after)
        call.read_back = bool(read_back)
        call.finished_at = timezone.now()
        call.save(
            update_fields=[
                "status",
                "error_type",
                "state_before",
                "state_after",
                "read_back",
                "finished_at",
                "updated_at",
            ]
        )
        _audit_call(call, actor=actor)
    return call


def _done_reply(name, arguments, *, requested, volume_max, state_after):
    service = arguments["service"]
    data = arguments.get("data") or {}
    if service == "volume_set":
        if requested is not None:
            maximum = _percent(volume_max)
            return CLAMPED_REPLY.format(maximum=maximum, suffix=dative_suffix(maximum))
        percent = _percent(data.get("volume_level", 0))
        return VOLUME_SET_REPLY.format(name=name, percent=percent, suffix=dative_suffix(percent))
    if service == "volume_mute":
        return (MUTE_REPLY if data.get("is_volume_muted") else UNMUTE_REPLY).format(name=name)
    if service == "media_play_pause":
        after = (state_after or {}).get("state") if isinstance(state_after, dict) else None
        return _PLAY_PAUSE_REPLIES.get(after, PLAY_PAUSE_REPLY).format(name=name)
    if service in ("volume_up", "volume_down"):
        level = live_volume(state_after)
        if level is not None:
            verb = "açıldı" if service == "volume_up" else "kısıldı"
            return VOLUME_STEP_REPLY.format(name=name, verb=verb, percent=_percent(level))
    return _DONE_REPLIES[service].format(name=name)


def execute(*, workspace, actor, message, origin, channel, node_id=None):
    """The one direct Home Assistant media call of a turn (ADR-018; D10
    steps 1-6 with D10a lock 3 first). `message` is the turn's saved user
    message -- never arguments:

    1. `deny_if_unattended("ha_direct_call")`;
    2. re-resolve the owner's stored words (`message.content`) with the
       deterministic matcher: the result must be the same `ha_call_service`
       draft with canonically equal arguments, from the same origin and
       node -- else nothing is sent, no call row is written,
       `ha.direct_provenance_refused` is audited and the turn answers with
       the matcher's own current result (its card, ask or refusal);
    3. re-run `classify` on the re-resolved arguments (the payload is never
       trusted) and read the entity once (`precondition_state`: still
       allowlisted, still present; the D9 `volume_up` rule is applied to
       this pre-send read);
    4. under the settings row lock: `classify` again with that live state
       (kill switch, flag, volume rules), count the rate windows (the only
       increment), clamp `volume_set`, create the `running` call row --
       committed before the send;
    5. one `call_tool` (the manifest's pinned schema, the write timeout),
       never retried;
    6. finalise the row once (`succeeded` / `failed` / `unknown_outcome`)
       and audit `ha.direct_call`.

    A refusal before the send is a `not_direct` `DirectRun` (the card); a
    failure after a send attempt is `sent` with a reply saying so."""
    from connectors import bridges, mcp_client
    from connectors.home_phrases import display_name
    from tools.home_ports import DIRECT_RUN_ERROR, DIRECT_RUN_SENT, DirectRun

    # 1
    unattended.deny_if_unattended(UNATTENDED_EFFECT)
    # 2 (D10a lock 3)
    resolution, detail = _provenance_refusal(
        workspace=workspace,
        actor=actor,
        message=message,
        origin=origin,
        channel=channel,
        node_id=node_id,
    )
    if detail is not None:
        _record_provenance_refused(
            actor=actor, workspace=workspace, message=message, origin=origin, detail=detail
        )
        if resolution is None:
            return _fallback(PROVENANCE_MISMATCH)
        if resolution.outcome == "draft":
            # The matcher's own current card, never one built from the
            # refused arguments.
            return _fallback(
                PROVENANCE_MISMATCH, arguments=resolution.arguments, reply=resolution.reply
            )
        return _fallback(PROVENANCE_MISMATCH, reply=resolution.reply)
    arguments = dict(resolution.arguments)
    input_mode = message.input_mode
    classify_kwargs = {
        "workspace": workspace,
        "actor": actor,
        "arguments": arguments,
        "origin": origin,
        "channel": channel,
        "input_mode": input_mode,
    }
    # 3
    verdict = classify(**classify_kwargs, audit_rate_limit=False)
    if not verdict.direct:
        return _fallback(verdict.reason, arguments=arguments)
    manifest, policy, binding = _gate(workspace)
    if policy is None:
        return _fallback(NOT_AVAILABLE, arguments=arguments)
    refused, state = bridge.precondition_state(binding, arguments)
    if refused is not None or state is None:
        return _fallback(TARGET_UNAVAILABLE, arguments=arguments)
    # 4
    limit = None
    with transaction.atomic():
        current = effective_settings(actor=actor, workspace=workspace, for_update=True)
        verdict = classify(**classify_kwargs, state=state, audit_rate_limit=False)
        if verdict.direct:
            try:
                limit = count_direct_call(workspace.pk, arguments["entity_id"])
            except RateStoreUnavailable:
                verdict = _verdict(RATE_STORE_UNAVAILABLE)
        if verdict.direct and limit is not None:
            record_rate_limited(
                actor=actor, workspace=workspace, arguments=arguments, origin=origin, limit=limit
            )
            verdict = _verdict(RATE_LIMITED, limit=limit)
        if verdict.direct:
            entity = (
                HomeEntity.objects.for_actor(actor, workspace=workspace)
                .select_related("room")
                .filter(entity_id=arguments["entity_id"], enabled=True)
                .first()
            )
            sent_arguments, requested = clamp_volume(arguments, current.volume_max)
            call = _start_call(
                workspace=workspace,
                actor=actor,
                entity=entity,
                arguments=sent_arguments,
                requested=requested,
                origin=origin,
                node_id=node_id,
                message=message,
            )
    if not verdict.direct:
        return _fallback(verdict.reason, arguments=arguments)
    name = display_name(entity) if entity is not None else arguments["entity_id"]
    # 5
    try:
        result = bridges.call_tool(
            manifest,
            server_tool=policy.server_tool,
            arguments=sent_arguments,
            schema_sha256=policy.schema_sha256,
            timeout=settings.CONNECTOR_WRITE_TIMEOUT_SECONDS,
        )
    except mcp_client.ConnectorError as exc:
        error_type = _ERROR_TYPES.get(exc.code, "unknown")
        if exc.sent:
            _finalize_call(call.pk, actor=actor, status=STATUS_UNKNOWN, error_type=error_type)
            return DirectRun(
                outcome=DIRECT_RUN_SENT,
                reason=error_type,
                reply=UNKNOWN_OUTCOME_REPLY.format(name=name),
                call_id=str(call.pk),
            )
        # Refused before anything reached the transport: recorded, and the
        # owner's request is still possible through the card.
        _finalize_call(call.pk, actor=actor, status=STATUS_FAILED, error_type=error_type)
        reason = NOT_AVAILABLE if exc.code in _UNAVAILABLE_CODES else TARGET_UNAVAILABLE
        return _fallback(reason, arguments=arguments)
    except Exception as exc:  # noqa: BLE001 -- whether it was sent is unknown
        logger.warning("direct call failed: %s", type(exc).__name__)
        _finalize_call(call.pk, actor=actor, status=STATUS_UNKNOWN, error_type="unknown")
        return DirectRun(outcome=DIRECT_RUN_ERROR, reason="executor_failed", call_id=str(call.pk))
    # 6
    if result.is_error:
        _finalize_call(call.pk, actor=actor, status=STATUS_FAILED, error_type="tool_error")
        return DirectRun(
            outcome=DIRECT_RUN_SENT,
            reason="tool_error",
            reply=REFUSED_REPLY.format(name=name),
            call_id=str(call.pk),
        )
    structured = result.structured if isinstance(result.structured, dict) else {}
    state_after = structured.get("state_after")
    _finalize_call(
        call.pk,
        actor=actor,
        status=STATUS_SUCCEEDED,
        state_before=structured.get("state_before"),
        state_after=state_after,
        read_back=structured.get("read_back") == "ok",
    )
    return DirectRun(
        outcome=DIRECT_RUN_SENT,
        reply=_done_reply(
            name,
            sent_arguments,
            requested=requested,
            volume_max=current.volume_max,
            state_after=state_after,
        ),
        call_id=str(call.pk),
    )


STATUS_SUCCEEDED = HomeDirectCall.Status.SUCCEEDED
STATUS_FAILED = HomeDirectCall.Status.FAILED
STATUS_UNKNOWN = HomeDirectCall.Status.UNKNOWN_OUTCOME


def mark_lost_direct_calls(now=None):
    """The recovery scan's direct half (`connectors.tasks.
    recover_stale_connector_writes`): a call still `running` longer than
    `CONNECTOR_WRITE_LEASE_SECONDS` lost its worker somewhere around the
    send -> `unknown_outcome` / `worker_lost`, audited, never re-sent.
    Returns how many were moved."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.CONNECTOR_WRITE_LEASE_SECONDS)
    stale_ids = list(
        HomeDirectCall.objects.filter(
            status=HomeDirectCall.Status.RUNNING, started_at__lt=cutoff
        ).values_list("id", flat=True)
    )
    moved = 0
    for call_id in stale_ids:
        if (
            _finalize_call(call_id, actor=None, status=STATUS_UNKNOWN, error_type="worker_lost")
            is not None
        ):
            moved += 1
    return moved


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


# --- the HUD line (D11, branch 1d) ----------------------------------------------

STATE_NOT_CONFIGURED = "not_configured"
HUD_CALLS_WINDOW = timedelta(hours=1)


def hud_status(*, workspace, actor):
    """The HUD's `home_direct` line `{state, flagged, calls_last_hour}`
    (D11), registered on `tools.home_ports.register_direct_status`. DB and
    settings reads only: never a Home Assistant request, never a row written
    (the settings row is not created here; the defaults apply until the
    owner opens `/connectors`). Informational only -- the classifier and the
    executor re-read everything themselves.

    - not the installation owner in his personal workspace ->
      `not_configured` with zero counts (the class does not exist there);
    - the environment disables the class -> `off_env`;
    - the owner kill switch -> `off_owner`;
    - Home Assistant not usable (manifest, binding, `ha_call_service` pin)
      or no enabled flagged entity -> `not_configured`;
    - otherwise `on`.
    `flagged` counts enabled flagged entities and `calls_last_hour` the
    direct call records (each one an attempted send) of the last hour, both
    through `for_actor()`."""
    if not _is_owner_personal(actor, workspace):
        return {"state": STATE_NOT_CONFIGURED, "flagged": 0, "calls_last_hour": 0}
    flagged = flagged_count(actor=actor, workspace=workspace)
    since = timezone.now() - HUD_CALLS_WINDOW
    calls = (
        HomeDirectCall.objects.for_actor(actor, workspace=workspace)
        .filter(created_at__gte=since)
        .count()
    )
    current = effective_settings(actor=actor, workspace=workspace)
    state = direct_state(kill_switch=current.kill_switch)
    if state == STATE_ON and (flagged == 0 or not _available(workspace)):
        state = STATE_NOT_CONFIGURED
    return {"state": state, "flagged": flagged, "calls_last_hour": calls}


def register():
    """`ConnectorsConfig.ready`: the direct classifier, (branch 1c) the
    direct executor and (branch 1d) the HUD status ports, reached by
    `assistant` and `hud` through `tools.home_ports`."""
    from tools.home_ports import (
        register_direct_classifier,
        register_direct_executor,
        register_direct_status,
    )

    register_direct_classifier(classify)
    register_direct_executor(execute)
    register_direct_status(hud_status)
