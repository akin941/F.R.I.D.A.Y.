"""Live voice on the node: the always-on gate, the trigger backstop and the
storm backstop (Phase 11 brief D11, D12; ADR-015 decisions 5 and 7,
roadmap d3).

Everything stateful about the node voice credential lives in `accounts`
(brief D11 placement): `voice` is a leaf no app may import, while the chat
turn (`assistant`), preflight (`ops`) and the authenticator need these
checks. The two DRF permission classes below are injected into the `voice`
routes by `config/urls.py` (`node_voice_permissions=`), so `voice` never
imports `accounts`.

- `policy_verdict` -- the single verdict function (the policy endpoint, the
  `/voice` page and, in the closeout, preflight call it; never a copy).
  Judges only the LATEST evaluation row per `(model_sha256, kind)`: an
  older passing row never counts once a newer one exists.
- `NodeVoiceTriggerGuard` -- `X-Friday-Voice-Trigger` on the live-session
  and transcription routes: required on the node voice channel (400
  `voice.trigger_invalid` when missing or unknown), `wake` refused with 403
  `voice.always_on_not_permitted` while no model's latest pair passes. It
  stops an honest but faulty child; a child that lies `ptt` is bounded by
  the storm backstop, the ADR-015 cap and the on-screen card instead.
- `NodeVoiceStormGuard` -- per node device, a sliding 60 s window of
  transcription requests in the shared cache (Redis in production); above
  VOICE_LIVE_STORM_MAX_STT_PER_MINUTE it engages `VoiceLiveMute` (audited)
  and answers 423 `voice.live_muted`. From then on the 423 comes from
  `NodeVoiceTokenAuthentication` on every route the token reaches until the
  owner releases the mute on screen.

Every read goes through `for_actor()` with the actor's personal workspace
(ADR-001).
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.models import PersonalAccessToken, VoiceLiveMute, VoiceWakeEvaluation
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_NODE_VOICE, request_channel

MIN_HOUSEHOLD_HOURS = Decimal("24")
STORM_WINDOW_SECONDS = 60
TRIGGER_HEADER = "X-Friday-Voice-Trigger"
TRIGGERS = ("wake", "ptt", "barge_in")

# The reason codes of a refused verdict, in the order they are reported.
REASON_DISABLED = "disabled"
REASON_NO_HOUSEHOLD = "no_household_eval"
REASON_FAR = "far_above_budget"
REASON_NO_POSITIVES = "no_positive_eval"
REASON_FRR = "frr_above_budget"
REASON_MODEL = "model_mismatch"
REASON_THRESHOLD = "threshold_mismatch"
REASON_STORM = "storm_muted"
REASONS = (
    REASON_DISABLED,
    REASON_NO_HOUSEHOLD,
    REASON_FAR,
    REASON_NO_POSITIVES,
    REASON_FRR,
    REASON_MODEL,
    REASON_THRESHOLD,
    REASON_STORM,
)

KIND = VoiceWakeEvaluation.Kind
THRESHOLD_QUANTUM = Decimal("0.0001")


# --- API exceptions ---


class VoiceLiveMuted(APIException):
    """423: the node device is storm-muted until the owner releases it."""

    status_code = status.HTTP_423_LOCKED
    default_detail = "Live voice on this node is muted. Release the mute on screen."
    default_code = "voice.live_muted"


class AlwaysOnNotPermitted(PermissionDenied):
    default_detail = "Always-on wake mode is not permitted."
    default_code = "voice.always_on_not_permitted"


class VoiceTriggerInvalid(APIException):
    """400: the node voice channel must say what opened the utterance."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "X-Friday-Voice-Trigger must be one of: wake, ptt, barge_in."
    default_code = "voice.trigger_invalid"


# --- settings ---


def budgets():
    """(max FAR per hour, max FRR) as Decimals, from the settings."""
    return (
        Decimal(str(settings.VOICE_LIVE_MAX_FAR_PER_HOUR)),
        Decimal(str(settings.VOICE_LIVE_MAX_FRR)),
    )


def always_on_enabled() -> bool:
    return bool(getattr(settings, "VOICE_LIVE_ALWAYS_ON_ENABLED", False))


def quantize_threshold(value) -> Decimal:
    return Decimal(str(value)).quantize(THRESHOLD_QUANTUM)


# --- the evaluation rows ---


def personal_workspace_id(user):
    from workspaces.models import Workspace

    return (
        Workspace.objects.filter(owner=user, type=Workspace.Type.PERSONAL)
        .values_list("pk", flat=True)
        .first()
    )


def latest_evaluations(actor, workspace_id, model_sha256=None) -> dict:
    """{(model_sha256, kind): the latest row} -- by `recorded_at`, then id
    -- for the actor's workspace, through `for_actor()`."""
    if workspace_id is None:
        return {}
    rows = VoiceWakeEvaluation.objects.for_actor(actor, workspace=workspace_id)
    if model_sha256 is not None:
        rows = rows.filter(model_sha256=model_sha256)
    rows = rows.order_by("model_sha256", "kind", "-recorded_at", "-id").distinct(
        "model_sha256", "kind"
    )
    return {(row.model_sha256, row.kind): row for row in rows}


def _household_reasons(row, threshold, refractory_ms, far_budget):
    if row is None or row.hours < MIN_HOUSEHOLD_HOURS:
        return [REASON_NO_HOUSEHOLD]
    reasons = []
    if row.far_per_hour is None or row.far_per_hour > far_budget:
        reasons.append(REASON_FAR)
    if row.threshold != threshold or row.refractory_ms != refractory_ms:
        reasons.append(REASON_THRESHOLD)
    return reasons


def _positives_reasons(row, threshold, refractory_ms, frr_budget):
    if row is None or row.positives <= 0:
        return [REASON_NO_POSITIVES]
    reasons = []
    if row.frr is None or row.frr > frr_budget:
        reasons.append(REASON_FRR)
    if row.threshold != threshold or row.refractory_ms != refractory_ms:
        reasons.append(REASON_THRESHOLD)
    return reasons


@dataclass
class Verdict:
    permitted: bool
    reasons: list = field(default_factory=list)
    household_id: str | None = None
    positives_id: str | None = None

    def as_dict(self):
        return {
            "always_on_permitted": self.permitted,
            "reasons": list(self.reasons),
            "household_eval_id": self.household_id,
            "positives_eval_id": self.positives_id,
        }


def _ordered(reasons):
    unique = set(reasons)
    return [reason for reason in REASONS if reason in unique]


def judge(latest, model_sha256, threshold, refractory_ms, *, enabled=None, storm_muted=False):
    """The D11 verdict over already-loaded latest rows (`latest_evaluations`).
    Pure: no query. `threshold` is compared exactly at 4 decimals."""
    enabled = always_on_enabled() if enabled is None else enabled
    far_budget, frr_budget = budgets()
    threshold = quantize_threshold(threshold)
    refractory_ms = int(refractory_ms)
    household = latest.get((model_sha256, KIND.HOUSEHOLD))
    positives = latest.get((model_sha256, KIND.POSITIVES))
    reasons = [] if enabled else [REASON_DISABLED]
    gate_kinds = (KIND.HOUSEHOLD, KIND.POSITIVES)
    other_models = any(sha != model_sha256 and kind in gate_kinds for sha, kind in latest)
    if household is None and positives is None and other_models:
        # Rows exist, but for another wake model: the child loaded a model
        # that was never measured (a retrained or swapped `hey_friday`).
        reasons.append(REASON_MODEL)
    else:
        reasons += _household_reasons(household, threshold, refractory_ms, far_budget)
        reasons += _positives_reasons(positives, threshold, refractory_ms, frr_budget)
    if storm_muted:
        reasons.append(REASON_STORM)
    return Verdict(
        permitted=not reasons,
        reasons=_ordered(reasons),
        household_id=str(household.pk) if household else None,
        positives_id=str(positives.pk) if positives else None,
    )


def policy_verdict(actor, workspace_id, model_sha256, threshold, refractory_ms, *, device_id=None):
    """`GET /api/v1/auth/nodes/voice-policy`: may the child arm wake mode
    for the model it loaded, at the threshold and refractory period it runs
    at? `device_id` (the token's node device) adds `storm_muted`."""
    latest = latest_evaluations(actor, workspace_id)
    muted = device_id is not None and engaged_mute(actor, workspace_id, device_id) is not None
    return judge(latest, model_sha256, threshold, refractory_ms, storm_muted=muted)


def model_verdicts(actor, workspace_id) -> list:
    """Per measured wake model: its latest rows and its verdict at the
    threshold and refractory period its latest household row (else
    positives row) was measured at -- the `/voice` panel and the trigger
    backstop ("is any model's latest pair passing?")."""
    latest = latest_evaluations(actor, workspace_id)
    out = []
    for sha in sorted({sha for sha, _kind in latest}):
        anchor = latest.get((sha, KIND.HOUSEHOLD)) or latest.get((sha, KIND.POSITIVES))
        if anchor is None:  # only a negative-set row: nothing to judge against
            verdict = Verdict(
                permitted=False,
                reasons=_ordered(
                    ([] if always_on_enabled() else [REASON_DISABLED])
                    + [REASON_NO_HOUSEHOLD, REASON_NO_POSITIVES]
                ),
            )
            threshold, refractory_ms = None, None
        else:
            threshold, refractory_ms = anchor.threshold, anchor.refractory_ms
            verdict = judge(latest, sha, threshold, refractory_ms)
        out.append(
            {
                "model_sha256": sha,
                "threshold": threshold,
                "refractory_ms": refractory_ms,
                "verdict": verdict,
                "rows": {kind: row for (row_sha, kind), row in latest.items() if row_sha == sha},
            }
        )
    return out


def any_model_permitted(actor, workspace_id) -> bool:
    """The trigger backstop's question: the server cannot see which model
    the child loaded, so it asks whether ANY model's latest household +
    positives pair passes (at the threshold they were measured at) with the
    setting on."""
    if not always_on_enabled():
        return False
    return any(entry["verdict"].permitted for entry in model_verdicts(actor, workspace_id))


# --- recording (the `voice_wake_eval` command) ---


class EvaluationRefused(ValueError):
    """Impossible or inconsistent numbers: nothing is written."""


def _decimal(name, value, *, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise EvaluationRefused(f"{name} is required")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise EvaluationRefused(f"{name} must be a number") from exc
    if not number.is_finite() or number < 0:
        raise EvaluationRefused(f"{name} must be a finite, non-negative number")
    return number


def _non_negative_int(name, value):
    if value is None:
        return 0
    if isinstance(value, bool) or int(value) != value or value < 0:
        raise EvaluationRefused(f"{name} must be a non-negative integer")
    return int(value)


RATE_TOLERANCE = Decimal("0.0001")


def record_evaluation(
    *,
    owner,
    model_sha256,
    kind,
    threshold,
    refractory_ms,
    hours=None,
    false_accepts=None,
    far_per_hour=None,
    positives=None,
    false_rejects=None,
    frr=None,
    report_sha256="",
    recorded_by=None,
):
    """Validate and append one evaluation row to the owner's personal
    workspace; audited `accounts.voice_wake_eval.recorded`. A FAR (FRR)
    given explicitly must equal false accepts / hours (false rejects /
    positives) within 0.0001; omitted, it is computed."""
    import re

    if not re.fullmatch(r"[0-9a-f]{64}", str(model_sha256 or "")):
        raise EvaluationRefused("model_sha256 must be 64 lowercase hex characters")
    if report_sha256 and not re.fullmatch(r"[0-9a-f]{64}", report_sha256):
        raise EvaluationRefused("report_sha256 must be 64 lowercase hex characters")
    if kind not in KIND.values:
        raise EvaluationRefused(f"kind must be one of {', '.join(KIND.values)}")
    threshold = _decimal("threshold", threshold)
    if not Decimal("0") < threshold <= Decimal("1"):
        raise EvaluationRefused("threshold must be in (0, 1]")
    if threshold != threshold.quantize(THRESHOLD_QUANTUM):
        raise EvaluationRefused("threshold has at most 4 decimals")
    refractory_ms = _non_negative_int("refractory_ms", refractory_ms)
    if refractory_ms > 60_000:
        raise EvaluationRefused("refractory_ms must be at most 60000")
    fields = {
        "model_sha256": model_sha256,
        "kind": kind,
        "threshold": threshold,
        "refractory_ms": refractory_ms,
        "report_sha256": report_sha256 or "",
    }
    if kind in (KIND.HOUSEHOLD, KIND.NEGATIVE_SET):
        hours = _decimal("hours", hours)
        if hours <= 0:
            raise EvaluationRefused("hours must be positive")
        if hours != hours.quantize(Decimal("0.001")):
            raise EvaluationRefused("hours has at most 3 decimals")
        accepts = _non_negative_int("false_accepts", false_accepts)
        computed = (Decimal(accepts) / hours).quantize(THRESHOLD_QUANTUM)
        given = _decimal("far_per_hour", far_per_hour, allow_none=True)
        if given is not None and abs(given - Decimal(accepts) / hours) > RATE_TOLERANCE:
            raise EvaluationRefused("far_per_hour is not false_accepts / hours")
        if positives or false_rejects or frr is not None:
            raise EvaluationRefused(f"a {kind} evaluation carries no positives, rejects or FRR")
        fields |= {"hours": hours, "false_accepts": accepts, "far_per_hour": computed}
    else:
        count = _non_negative_int("positives", positives)
        if count <= 0:
            raise EvaluationRefused("positives must be positive")
        rejects = _non_negative_int("false_rejects", false_rejects)
        if rejects > count:
            raise EvaluationRefused("false_rejects cannot exceed positives")
        computed = (Decimal(rejects) / Decimal(count)).quantize(THRESHOLD_QUANTUM)
        given = _decimal("frr", frr, allow_none=True)
        if given is not None and abs(given - Decimal(rejects) / Decimal(count)) > RATE_TOLERANCE:
            raise EvaluationRefused("frr is not false_rejects / positives")
        if hours or false_accepts or far_per_hour is not None:
            raise EvaluationRefused("a positives evaluation carries no hours, accepts or FAR")
        fields |= {"positives": count, "false_rejects": rejects, "frr": computed}
    workspace_id = personal_workspace_id(owner)
    if workspace_id is None:
        raise EvaluationRefused("the owner has no personal workspace")
    with transaction.atomic():
        row = VoiceWakeEvaluation.objects.create(
            workspace_id=workspace_id, recorded_by=recorded_by, **fields
        )
        record_audit(
            action="accounts.voice_wake_eval.recorded",
            actor=recorded_by,
            actor_type=AuditLog.ActorType.HUMAN if recorded_by else AuditLog.ActorType.SYSTEM,
            resource=row,
            metadata={
                "model_sha256": model_sha256,
                "kind": kind,
                "threshold": str(threshold),
                "refractory_ms": refractory_ms,
                "far_per_hour": str(row.far_per_hour) if row.far_per_hour is not None else "",
                "frr": str(row.frr) if row.frr is not None else "",
            },
        )
    return row


# --- the storm mute ---


def engaged_mute(actor, workspace_id, device_id):
    """The unreleased mute of a node device, read through `for_actor()`."""
    if workspace_id is None or device_id is None:
        return None
    return (
        VoiceLiveMute.objects.for_actor(actor, workspace=workspace_id)
        .filter(node_device_id=device_id, released_at__isnull=True)
        .first()
    )


def engage_mute(token):
    """Engage the storm mute for the token's node device (idempotent: at
    most one unreleased row per device). Audited when it engages."""
    try:
        with transaction.atomic():
            mute = VoiceLiveMute.objects.create(
                workspace_id=token.workspace_id,
                node_device_id=token.node_device_id,
                reason=VoiceLiveMute.Reason.STT_STORM,
            )
    except IntegrityError:
        return engaged_mute(token.user, token.workspace_id, token.node_device_id)
    record_audit(
        action="accounts.voice_live_mute.engaged",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=mute.workspace,
        resource=mute,
        metadata={
            "node_device_id": str(token.node_device_id),
            "reason": mute.reason,
            "max_stt_per_minute": settings.VOICE_LIVE_STORM_MAX_STT_PER_MINUTE,
        },
    )
    return mute


def release_mute(*, owner, device, via_device_id=""):
    """Release the device's engaged mute (web, owner; the view checks the
    channel). Returns the released row, or None when none was engaged."""
    workspace_id = personal_workspace_id(owner)
    with transaction.atomic():
        mute = engaged_mute(owner, workspace_id, device.pk)
        if mute is None:
            return None
        mute = VoiceLiveMute.objects.select_for_update().get(pk=mute.pk)
        if mute.released_at is not None:
            return None
        mute.released_at = timezone.now()
        mute.released_by = owner
        mute.save(update_fields=["released_at", "released_by"])
        record_audit(
            action="accounts.voice_live_mute.released",
            actor=owner,
            workspace=mute.workspace,
            resource=mute,
            metadata={"node_device_id": str(device.pk), "released_by_device_id": via_device_id},
        )
    # The owner released the mute: the storm's own requests must not re-mute
    # the very next utterance, so the device starts a fresh window.
    reset_transcription_window(device.pk)
    return mute


def _now_seconds() -> int:
    return int(timezone.now().timestamp())


def _bucket_key(device_id, second):
    return f"accounts:voice-stt:{device_id}:{second}"


def count_transcription(device_id) -> int:
    """Count one transcription request of a node device and return how many
    it made in the last STORM_WINDOW_SECONDS (this one included): one-second
    buckets with atomic increments, summed with one multi-get -- a sliding
    window at one-second granularity, shared through the cache (Redis in
    production), keyed by the DEVICE so a rotated token does not reset it."""
    now = _now_seconds()
    key = _bucket_key(device_id, now)
    timeout = STORM_WINDOW_SECONDS + 10
    cache.add(key, 0, timeout=timeout)
    try:
        cache.incr(key)
    except ValueError:
        # The bucket expired or was evicted between add and incr: start it
        # again at 1 -- with add, so a concurrent request that recreated it
        # first is counted on top of, not clobbered.
        if not cache.add(key, 1, timeout=timeout):
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=timeout)
    keys = [_bucket_key(device_id, now - offset) for offset in range(STORM_WINDOW_SECONDS)]
    return sum(int(value) for value in cache.get_many(keys).values())


def reset_transcription_window(device_id) -> None:
    """Forget the device's counted transcriptions (every bucket of the current
    window): called when the owner releases a storm mute, so the next request
    after the release succeeds instead of re-engaging the mute at once."""
    now = _now_seconds()
    cache.delete_many(
        [_bucket_key(device_id, now - offset) for offset in range(STORM_WINDOW_SECONDS + 1)]
    )


# --- the guards injected into the `voice` routes ---


def _token_of(request):
    token_id = getattr(request.auth, "token_id", None)
    if not token_id:
        return None
    return (
        PersonalAccessToken.objects.select_related("user")
        .filter(pk=token_id, kind=PersonalAccessToken.Kind.NODE_VOICE)
        .first()
    )


class NodeVoiceTriggerGuard(BasePermission):
    """`X-Friday-Voice-Trigger` (D11 backstop) on the live-session and
    transcription routes. Off the node voice channel the header is ignored.
    On it: missing or unknown -> 400 `voice.trigger_invalid`; `wake` while
    no model's latest household + positives pair passes (or the setting is
    off) -> 403 `voice.always_on_not_permitted`; `ptt` and `barge_in` pass."""

    def has_permission(self, request, view):
        if request_channel(request) != CHANNEL_NODE_VOICE:
            return True
        trigger = request.headers.get(TRIGGER_HEADER, "")
        if trigger not in TRIGGERS:
            raise VoiceTriggerInvalid()
        if trigger == "wake":
            workspace_id = getattr(request.auth, "bound_workspace_id", None)
            if not any_model_permitted(request.user, workspace_id):
                raise AlwaysOnNotPermitted()
        return True


class NodeVoiceStormGuard(BasePermission):
    """The server's storm backstop (D12) on the transcription route: counts
    the node device's transcriptions in a sliding 60 s window; above
    VOICE_LIVE_STORM_MAX_STT_PER_MINUTE it engages the device's
    `VoiceLiveMute` and answers 423 `voice.live_muted`. Off the node voice
    channel it does nothing."""

    def has_permission(self, request, view):
        if request_channel(request) != CHANNEL_NODE_VOICE:
            return True
        token = _token_of(request)
        if token is None or token.node_device_id is None:
            return False
        if count_transcription(token.node_device_id) > settings.VOICE_LIVE_STORM_MAX_STT_PER_MINUTE:
            engage_mute(token)
            raise VoiceLiveMuted()
        return True
