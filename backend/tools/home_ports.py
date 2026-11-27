"""The home-control ports (Phase 13 D13, branch 6).

`assistant` may not import `connectors` (ADR-010 contract "kernel apps must
not import connectors or deploys"), yet the `home.control` wake route and the
typed-turn draft check need the owner's Home Assistant allowlist. So
`connectors` registers three functions here at startup
(`ConnectorsConfig.ready`) and `assistant` calls them through this module
(assistant -> tools -> port). This module imports nothing of ours.

- `register_home_resolver(fn)`: `fn(*, workspace, actor, text) ->
  HomeResolution | None` -- the deterministic `home.control` matcher and
  resolver (`connectors.home_phrases`). `None` = not a home command (the
  turn falls through to the normal router). It never creates an approval,
  never calls Home Assistant and never calls a model.
- `register_draft_check(tool_name, fn)`: `fn(*, workspace, actor, arguments,
  source_text) -> str | None` -- run on a gated tool's router-extracted
  draft arguments before the card exists. A returned string is the
  Friday-authored refusal the turn answers with instead of a draft
  (`target_mismatch`, `entity_not_allowed`, ...).
- `register_draft_vocabulary(tool_name, fn)`: `fn(*, workspace, actor) ->
  list[dict]` -- owner-set rows (an allowlisted entity's id, room, label,
  services) shown to `router.connector_args` **as data** so the argument
  step can name a real target; never instructions, never server text.

Phase 13b (ADR-018, brief D7/D10, branch 1b) adds the direct-media ports:

- `register_direct_classifier(fn)`: `fn(*, workspace, actor, arguments,
  origin, channel, input_mode, **options) -> DirectVerdict` -- the closed,
  ordered admission check of ADR-018's direct class
  (`connectors.home_direct.classify`). Read-only: it never sends, never
  creates a row and never spends rate-limit quota.
- `register_direct_executor(fn)`: `fn(*, workspace, actor, message, origin,
  channel, node_id) -> DirectRun` -- the single direct send
  (`connectors.home_direct.execute`, registered by `ConnectorsConfig.ready`
  since branch 1c). It takes the saved user message, never arguments: it
  re-resolves the owner's stored words first (D10a lock 3). Without a
  registered executor every `run_home_direct` answers `not_direct` and
  sends nothing.

Phase 13b branch 1d (brief D11) adds the read-only status port:

- `register_direct_status(fn)`: `fn(*, workspace, actor) -> dict` --
  `{state, flagged, calls_last_hour}` for the HUD's `home_direct` line
  (`connectors.home_direct.hud_status`; DB only, never a Home Assistant
  request). `hud` may not import `connectors`, so it reads the line here.
  Unregistered, raising or malformed -> `not_configured` with zero counts.

An unregistered or raising classifier/executor is **never direct**
(`not_available`): the turn stays exactly the gate-class Phase 13 turn.

Unregistered resolver -> every utterance is a miss. Unregistered check ->
no extra check (the tool's own serializer and request-time scope check
still run when the owner taps the card). A port that raises is treated as
a miss / a refusal, never as an approval."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

OUTCOME_DRAFT = "draft"
OUTCOME_ASK = "ask"
OUTCOME_REFUSE = "refuse"
OUTCOMES = frozenset({OUTCOME_DRAFT, OUTCOME_ASK, OUTCOME_REFUSE})

# Said when a draft check itself failed: no card, nothing guessed.
CHECK_FAILED_REPLY = "Bunu şu an doğrulayamadım; taslak oluşturmadım."


@dataclass(frozen=True)
class HomeResolution:
    """One home command, resolved.

    - `draft`: exactly one allowlisted entity and service; `tool_name` and
      `arguments` are the gated tool's connector-write arguments the draft
      card carries (`{"arguments": arguments}`), `reply` the short spoken
      confirmation that the card is on screen;
    - `ask`: several entities matched; `reply` lists them, no card;
    - `refuse`: nothing allowed matched; `reply` says why, no card.

    `code` is a closed token (`draft`, `ambiguous`, `unknown_room`,
    `not_found`, `service_not_allowed`, `entity_not_allowed`,
    `not_connected`, `invalid`)."""

    outcome: str
    reply: str
    code: str = ""
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    candidates: tuple = ()

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown home outcome {self.outcome!r}")
        if self.outcome == OUTCOME_DRAFT and not (self.tool_name and self.arguments):
            raise ValueError("a draft names its tool and arguments")


_RESOLVER: list[Callable[..., Any]] = []
_DRAFT_CHECKS: dict[str, Callable[..., Any]] = {}
_DRAFT_VOCABULARY: dict[str, Callable[..., Any]] = {}


def register_home_resolver(fn):
    _RESOLVER[:] = [fn]


def unregister_home_resolver():
    """Test-only."""
    _RESOLVER.clear()


def home_resolver():
    return _RESOLVER[0] if _RESOLVER else None


def resolve_home_command(*, workspace, actor, text):
    """The registered resolver's answer, or None (not a home command, no
    resolver, or the resolver failed)."""
    fn = home_resolver()
    if fn is None:
        return None
    try:
        result = fn(workspace=workspace, actor=actor, text=text)
    except Exception as exc:  # noqa: BLE001 -- a broken resolver is a miss
        logger.warning("home resolver failed: %s", type(exc).__name__)
        return None
    return result if isinstance(result, HomeResolution) else None


def register_draft_check(tool_name, fn):
    _DRAFT_CHECKS[tool_name] = fn


def unregister_draft_check(tool_name):
    """Test-only."""
    _DRAFT_CHECKS.pop(tool_name, None)


def check_draft(tool_name, *, workspace, actor, arguments, source_text):
    """`None` when the draft may be shown, else the refusal text. A check
    that raises refuses (fail closed)."""
    fn = _DRAFT_CHECKS.get(tool_name)
    if fn is None:
        return None
    try:
        refusal = fn(workspace=workspace, actor=actor, arguments=arguments, source_text=source_text)
    except Exception as exc:  # noqa: BLE001 -- fail closed: no draft
        logger.warning("draft check for %s failed: %s", tool_name, type(exc).__name__)
        return CHECK_FAILED_REPLY
    return refusal or None


def register_draft_vocabulary(tool_name, fn):
    _DRAFT_VOCABULARY[tool_name] = fn


def unregister_draft_vocabulary(tool_name):
    """Test-only."""
    _DRAFT_VOCABULARY.pop(tool_name, None)


def draft_vocabulary(tool_name, *, workspace, actor):
    """The owner-set targets for `tool_name`'s argument step, or []."""
    fn = _DRAFT_VOCABULARY.get(tool_name)
    if fn is None:
        return []
    try:
        rows = fn(workspace=workspace, actor=actor)
    except Exception as exc:  # noqa: BLE001 -- no vocabulary, the check still runs
        logger.warning("draft vocabulary for %s failed: %s", tool_name, type(exc).__name__)
        return []
    return list(rows or [])


# --- Phase 13b: the direct-media class (ADR-018) -----------------------------

# The one reason the port itself produces: no (working) classifier or
# executor. Every other reason is `connectors.home_direct`'s closed set.
DIRECT_NOT_AVAILABLE = "not_available"

DIRECT_RUN_SENT = "sent"
DIRECT_RUN_NOT_DIRECT = "not_direct"
# The executor raised: a send may or may not have been attempted, so the
# caller must neither build a card from the arguments nor claim success.
DIRECT_RUN_ERROR = "error"
DIRECT_RUN_OUTCOMES = frozenset({DIRECT_RUN_SENT, DIRECT_RUN_NOT_DIRECT, DIRECT_RUN_ERROR})
DIRECT_EXECUTOR_FAILED = "executor_failed"


@dataclass(frozen=True)
class DirectVerdict:
    """The classifier's answer. `direct` only when every ADR-018 condition
    held; otherwise `reason` is the first failing closed code. `limit` names
    the window (`global` / `entity`) of a `rate_limited` verdict.
    `needs_live_volume` marks a direct `volume_up` classified without a live
    state: the executor must re-check the volume rule on its pre-send read
    (D9) and send nothing if it fails."""

    direct: bool
    reason: str = ""
    limit: str = ""
    needs_live_volume: bool = False

    def __post_init__(self):
        if self.direct and self.reason:
            raise ValueError("a direct verdict carries no reason")
        if not self.direct and not self.reason:
            raise ValueError("a non-direct verdict names its reason")


NOT_AVAILABLE = DirectVerdict(direct=False, reason=DIRECT_NOT_AVAILABLE)


@dataclass(frozen=True)
class DirectRun:
    """The executor's answer (branch 1c).

    - `sent`: the one send was attempted; `call_id` names the call record,
      `reply` is Friday's fixed text for its outcome (success, refused by
      Home Assistant, or "may have gone").
    - `not_direct`: nothing was sent; `reason` is the closed code. When
      `arguments` is set the turn becomes the gate-class `tool_name` card
      with exactly those arguments (the executor's own re-resolution of the
      owner's words, never the classification's payload); otherwise `reply`
      (the matcher's current ask or refusal) answers with no card.
    - `error`: the executor raised; see `run_home_direct`."""

    outcome: str
    reason: str = ""
    reply: str = ""
    call_id: str = ""
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.outcome not in DIRECT_RUN_OUTCOMES:
            raise ValueError(f"unknown direct run outcome {self.outcome!r}")


_DIRECT_CLASSIFIER: list[Callable[..., Any]] = []
_DIRECT_EXECUTOR: list[Callable[..., Any]] = []


def register_direct_classifier(fn):
    _DIRECT_CLASSIFIER[:] = [fn]


def unregister_direct_classifier():
    """Test-only."""
    _DIRECT_CLASSIFIER.clear()


def direct_classifier():
    return _DIRECT_CLASSIFIER[0] if _DIRECT_CLASSIFIER else None


def classify_home_direct(*, workspace, actor, arguments, origin, channel, input_mode, **options):
    """The registered classifier's verdict; `NOT_AVAILABLE` (never direct)
    when none is registered, it raises, or it answers anything but a
    `DirectVerdict`."""
    fn = direct_classifier()
    if fn is None:
        return NOT_AVAILABLE
    try:
        verdict = fn(
            workspace=workspace,
            actor=actor,
            arguments=arguments,
            origin=origin,
            channel=channel,
            input_mode=input_mode,
            **options,
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed: the card
        logger.warning("direct classifier failed: %s", type(exc).__name__)
        return NOT_AVAILABLE
    return verdict if isinstance(verdict, DirectVerdict) else NOT_AVAILABLE


def register_direct_executor(fn):
    _DIRECT_EXECUTOR[:] = [fn]


def unregister_direct_executor():
    """Test-only."""
    _DIRECT_EXECUTOR.clear()


def direct_executor():
    return _DIRECT_EXECUTOR[0] if _DIRECT_EXECUTOR else None


def run_home_direct(*, workspace, actor, message, origin, channel, node_id=None):
    """The registered executor's result; `not_direct` / `not_available`
    (nothing sent) when none is registered. An executor that raises or
    answers anything but a `DirectRun` is reported `error` /
    `executor_failed`: it may have reached the send, so the caller must not
    substitute a card (ADR-018 decision 9); its own `HomeDirectCall` row is
    the record."""
    fn = direct_executor()
    if fn is None:
        return DirectRun(outcome=DIRECT_RUN_NOT_DIRECT, reason=DIRECT_NOT_AVAILABLE)
    try:
        result = fn(
            workspace=workspace,
            actor=actor,
            message=message,
            origin=origin,
            channel=channel,
            node_id=node_id,
        )
    except Exception as exc:  # noqa: BLE001 -- never an approval
        logger.warning("direct executor failed: %s", type(exc).__name__)
        return DirectRun(outcome=DIRECT_RUN_ERROR, reason=DIRECT_EXECUTOR_FAILED)
    if isinstance(result, DirectRun):
        return result
    return DirectRun(outcome=DIRECT_RUN_ERROR, reason=DIRECT_EXECUTOR_FAILED)


# --- Phase 13b branch 1d: the HUD status line (D11) --------------------------

DIRECT_STATUS_ON = "on"
DIRECT_STATUS_OFF_OWNER = "off_owner"
DIRECT_STATUS_OFF_ENV = "off_env"
DIRECT_STATUS_NOT_CONFIGURED = "not_configured"
DIRECT_STATUS_STATES = frozenset(
    {
        DIRECT_STATUS_ON,
        DIRECT_STATUS_OFF_OWNER,
        DIRECT_STATUS_OFF_ENV,
        DIRECT_STATUS_NOT_CONFIGURED,
    }
)

_DIRECT_STATUS: list[Callable[..., Any]] = []


def not_configured_status():
    return {"state": DIRECT_STATUS_NOT_CONFIGURED, "flagged": 0, "calls_last_hour": 0}


def register_direct_status(fn):
    _DIRECT_STATUS[:] = [fn]


def unregister_direct_status():
    """Test-only."""
    _DIRECT_STATUS.clear()


def direct_status_reader():
    return _DIRECT_STATUS[0] if _DIRECT_STATUS else None


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("a count is a non-negative int")
    return value


def home_direct_status(*, workspace, actor):
    """The registered reader's `{state, flagged, calls_last_hour}` -- only
    those three keys, the state one of the four closed words. Read-only and
    informational: nothing decides on it (the classifier and the executor
    read the settings themselves). Unregistered, raising or malformed ->
    `not_configured` with zero counts."""
    fn = direct_status_reader()
    if fn is None:
        return not_configured_status()
    try:
        raw = fn(workspace=workspace, actor=actor)
        state = raw["state"]
        if state not in DIRECT_STATUS_STATES:
            raise ValueError("unknown state")
        return {
            "state": state,
            "flagged": _count(raw["flagged"]),
            "calls_last_hour": _count(raw["calls_last_hour"]),
        }
    except Exception as exc:  # noqa: BLE001 -- the HUD line is informational
        logger.warning("direct status failed: %s", type(exc).__name__)
        return not_configured_status()
