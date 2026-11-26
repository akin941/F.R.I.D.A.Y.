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
        refusal = fn(
            workspace=workspace, actor=actor, arguments=arguments, source_text=source_text
        )
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
