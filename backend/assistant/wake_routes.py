"""Phase 12 D8: wake-phrase routing for voice turns.

A code-defined table of routes and a deterministic matcher. The chat turn
(`assistant.views.ConversationMessageListView.post`) asks `route_voice_turn`
**only when the turn's `input_mode` is `voice`** (web voice and every
`node_voice` turn), before `classify_intent`. A match replaces the
`router.intent` call with the route's handler; a miss returns None and the
normal router runs. Typed turns are never routed.

- **Normalizer.** `normalize_utterance` is the Python port of
  `frontend/src/lib/voice/self-trigger.ts` `normalizeSpeech` (NFC, Turkish
  lower-casing, every character that is not a letter, a digit or a space
  becomes a space, whitespace collapsed) plus D8's two strips: one leading
  wake phrase ("hey friday" / "friday") and trailing filler words. The
  shared vectors live in `docs/voice/wake-route-vectors.json` (pytest).
- **Matching.** Zero-argument routes match the whole normalized utterance
  against their default phrases and the workspace's owner aliases. No
  prefix match for them, no fuzzy matching, no model call.
- **Cap.** Every route's `effect` is `read` or `proposal`; `validate_routes`
  runs at boot (`AssistantConfig.ready`) and refuses to start otherwise.
  `apply_voice_capability_cap` still runs on the route's classification: a
  route whose result the cap degrades loses its reply and the turn is
  answered as plain chat.
- **Writes.** A route reads. The one write any route makes is
  `digest.today`'s own `spoken_at` / `spoken_channel` telemetry on the
  digest it answered with (`proactive.api.mark_digest_spoken`, ADR-015
  implementation note), made only after the cap has kept the route.
- **Tool routes (branch 4).** `tool.<name>` routes exist for every
  registered read tool and user-invocable gated tool, with owner aliases
  only, matched as the leading words of the utterance; they yield exactly
  what the router would (a `connector_read` turn, or a `tool_request` draft
  card) and make exactly the router's argument call, nothing more.
- **Home route (Phase 13 D13).** `home.control` has no phrase table: its
  matcher is code (`connectors.home_phrases`, reached through
  `tools.home_ports`) over the owner's own room and device rows. Tried after
  the phrase and `tool.*` routes, before `skill.*` aliases. Effect
  `proposal`: one allowlisted entity -> the `ha_call_service` draft card
  (the exact arguments, a fixed spoken confirmation, no model call);
  several -> the ask template; nothing allowed -> a fixed refusal. It never
  approves or executes; the card goes the normal gated path.
- **Direct media (Phase 13b D10/D10a, ADR-018).** A `home.control` draft
  that ADR-018's classifier admits (an owner-flagged `media_player`, one of
  the eight closed services, kill switch off, within the rate limits)
  becomes a `home_direct` classification carrying the code-only provenance
  marker; the chat view then runs the one call after the cap. Only
  `home.control` may return that effect (`extra_effects`, checked by
  `validate_routes` and again on every result by `_finish`).
- **Scope.** Routes answer only the owner of the conversation's workspace,
  from that workspace's rows (`for_actor()` through `proactive.api` and
  `memory.probes`): a firm digest never answers a personal conversation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from assistant.models import Message, WakeRouteAlias

# Phase 13 D16: the normalizer moved to `core.speech` (stdlib only) so
# `nodes` and `connectors` share it; re-exported here for every caller.
from core.speech import match_key, normalize_speech  # noqa: F401

EFFECT_READ = "read"
EFFECT_PROPOSAL = "proposal"
ALLOWED_EFFECTS = frozenset({EFFECT_READ, EFFECT_PROPOSAL})
# Phase 13b D10 (ADR-018): the one extra effect a route may declare, and the
# one route that may declare it (`validate_routes` refuses anything else).
EFFECT_DIRECT_HOME = "direct_home"
ALLOWED_EXTRA_EFFECTS = frozenset({EFFECT_DIRECT_HOME})

# Stripped once from the start of an utterance (longest first).
WAKE_PHRASES = ("hey friday", "friday")
# Stripped (repeatedly) from the end of an utterance: politeness and a
# trailing vocative, nothing that could change what was asked.
TRAILING_FILLERS = frozenset({"lütfen", "please", "friday"})

ALIAS_MIN_CHARS = 2
ALIAS_MAX_CHARS = 40
MAX_ALIASES_PER_ROUTE = 5
_ROUTE_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

NO_DIGEST_TEXT = "Bugün için brifing yok."
NO_NOTICES_TEXT = "Okunmamış bildirim yok."
NO_LEARNING_TEXT = "Öğrenme kartında bekleyen öneri yok."
NOTICES_READ_LIMIT = 5


def normalize_utterance(text: str) -> str:
    """`normalize_speech` plus one leading wake phrase and trailing fillers
    stripped (D8). Used for transcripts, default phrases and aliases."""
    tokens = normalize_speech(text).split()
    keys = [match_key(token) for token in tokens]
    for phrase in WAKE_PHRASES:
        words = phrase.split()
        if keys[: len(words)] == words:
            tokens, keys = tokens[len(words) :], keys[len(words) :]
            break
    while keys and keys[-1] in TRAILING_FILLERS:
        tokens.pop()
        keys.pop()
    return " ".join(tokens)


@dataclass(frozen=True)
class WakeTurn:
    """What a route handler sees: the turn, never the request."""

    conversation: Any
    actor: Any
    workspace: Any
    channel: str
    content: str
    remainder: str = ""
    now: Any = None
    # Phase 13b: the node a `node_voice` turn was spoken to (the voice
    # token's device), else None -- recorded on a direct call's origin.
    origin_node_id: str | None = None
    input_mode: str = Message.InputMode.VOICE


@dataclass
class RouteResult:
    """A handler's answer: the classification it stands for (checked by
    the ADR-015 cap), the reply text spoken back with no model call, and
    the effect to commit only if the cap keeps the route. `provenance`
    (Phase 13b D10a lock 2) is the code-only marker of a `home_direct`
    classification, carried into the route's classification by identity."""

    intent: str = Message.Intent.CHAT
    intent_payload: dict = field(default_factory=dict)
    reply_text: str | None = None
    commit: Callable[[], None] | None = None
    provenance: Any = None


@dataclass(frozen=True)
class WakeRoute:
    """One route. A phrase route matches its `default_phrases` (and owner
    aliases); a `matched_by_code` route (Phase 13 `home.control`) has none:
    its handler is also its matcher and returns None on a miss.
    `extra_effects` (Phase 13b D10): effects the route's results may have
    beyond `effect` -- only `home.control`, only `direct_home`."""

    name: str
    effect: str
    default_phrases: tuple
    handler: Callable[[WakeTurn], RouteResult | None]
    matched_by_code: bool = False
    extra_effects: tuple = ()


@dataclass(frozen=True)
class RoutedTurn:
    """`route_voice_turn`'s answer to the chat turn. A `skill.<name>` route
    (branch 5) has no classification of its own (`None`: the normal router
    runs) and no reply; it only names the skill the turn must use.
    `args_text` (a `tool.*` route): the words after the alias, from which
    the read's arguments are extracted."""

    route: str
    classification: dict | None
    reply_text: str | None
    skill_name: str | None = None
    args_text: str = ""


# --- handlers -----------------------------------------------------------------


def _is_today(value, now):
    return value is not None and timezone.localdate(value) == timezone.localdate(now)


def _spoken_channel(channel):
    return Message.Channel.NODE_VOICE if channel == Message.Channel.NODE_VOICE else "web"


def _digest_today(turn: WakeTurn) -> RouteResult:
    from proactive import api as proactive_api

    row = proactive_api.latest_digest_row(actor=turn.actor, workspace=turn.workspace)
    if row is None or not _is_today(row.created_at, turn.now) or not row.spoken_text.strip():
        return RouteResult(reply_text=NO_DIGEST_TEXT)

    def commit():
        proactive_api.mark_digest_spoken(
            actor=turn.actor,
            workspace=turn.workspace,
            digest_id=row.pk,
            channel=_spoken_channel(turn.channel),
            now=turn.now,
        )

    return RouteResult(reply_text=row.spoken_text, commit=commit)


_LEARNING_LABELS = (
    ("memory", "{n} anı önerisi"),
    ("core-block", "{n} çekirdek blok önerisi"),
    ("fact", "{n} olgu önerisi"),
)


def _digest_learning(turn: WakeTurn) -> RouteResult:
    from memory.probes import learning_counts

    counts = learning_counts(
        turn.workspace, turn.actor, turn.now, days=settings.MEMORY_LEARNING_DIGEST_DAYS
    )
    parts = [label.format(n=counts[key]) for key, label in _LEARNING_LABELS if counts.get(key)]
    if not parts:
        return RouteResult(reply_text=NO_LEARNING_TEXT)
    listed = ", ".join(parts)
    return RouteResult(
        reply_text=(
            f"Öğrenme kartında {listed} var. Kararları ekranda, Öğrenme sayfasında verirsin."
        )
    )


def _notices_read(turn: WakeTurn) -> RouteResult:
    from proactive import api as proactive_api

    section = proactive_api.notice_section(actor=turn.actor, workspace=turn.workspace)
    titles = [item["title"] for item in section["latest"]][:NOTICES_READ_LIMIT]
    if not section["unread"] or not titles:
        return RouteResult(reply_text=NO_NOTICES_TEXT)
    sentences = [f"{section['unread']} okunmamış bildirim var."]
    sentences += [title.rstrip(".") + "." for title in titles]
    return RouteResult(reply_text=" ".join(sentences))


ROUTES: tuple[WakeRoute, ...] = (
    WakeRoute(
        name="digest.today",
        effect=EFFECT_READ,
        default_phrases=(
            "günaydın",
            "brifing",
            "bugünün brifingi",
            "sabah brifingi",
            "good morning",
            "briefing",
        ),
        handler=_digest_today,
    ),
    WakeRoute(
        name="digest.learning",
        effect=EFFECT_READ,
        default_phrases=("öğrenme kartı", "ne öğrendin", "learning card"),
        handler=_digest_learning,
    ),
    WakeRoute(
        name="notices.read",
        effect=EFFECT_READ,
        default_phrases=("bildirimler", "bildirimleri oku", "notifications"),
        handler=_notices_read,
    ),
)


# --- home.control (Phase 13 D13, branch 6) -------------------------------------
#
# The deterministic home route: "salondaki TV'yi aç" -> the `ha_call_service`
# draft card for the one allowlisted entity the owner named, with a fixed
# spoken confirmation. The matcher and resolver live in `connectors`
# (`connectors.home_phrases`), reached through `tools.home_ports`. No model
# call, no Home Assistant request, no `ApprovalRequest`.

HOME_ROUTE_NAME = "home.control"


def _home_control(turn: WakeTurn) -> RouteResult | None:
    from tools.home_ports import OUTCOME_DRAFT, resolve_home_command
    from tools.registry import list_tool_names

    resolution = resolve_home_command(workspace=turn.workspace, actor=turn.actor, text=turn.content)
    if resolution is None:
        return None
    if resolution.outcome != OUTCOME_DRAFT:
        # The ask or the refusal: Friday's own text, no card.
        return RouteResult(reply_text=resolution.reply)
    if resolution.tool_name not in list_tool_names():
        return None
    # Phase 13b D10 (ADR-018): a draft ADR-018 admits as direct is the
    # `home_direct` classification (the call runs in the chat view, after
    # the cap, with the saved message); any other draft is today's card,
    # carrying the closed reason it is not direct.
    from assistant.services import (
        PROVENANCE_KEY,
        classify_direct_draft,
        direct_origin,
        home_direct_classification,
    )

    verdict = classify_direct_draft(
        resolution,
        workspace=turn.workspace,
        actor=turn.actor,
        channel=turn.channel,
        input_mode=turn.input_mode,
    )
    if verdict.direct:
        origin = direct_origin(channel=turn.channel, input_mode=turn.input_mode)
        classification = home_direct_classification(
            resolution, origin=origin, node_id=turn.origin_node_id
        )
        return RouteResult(
            intent=classification["intent"],
            intent_payload=classification["intent_payload"],
            reply_text=None,
            provenance=classification[PROVENANCE_KEY],
        )
    return RouteResult(
        intent=Message.Intent.TOOL_REQUEST,
        intent_payload={
            "tool_name": resolution.tool_name,
            "payload": {"arguments": dict(resolution.arguments)},
            "direct_reason": verdict.reason,
        },
        reply_text=resolution.reply,
    )


# Routes whose matcher is code, tried after the phrase and `tool.*` routes.
CODE_ROUTES: tuple[WakeRoute, ...] = (
    WakeRoute(
        name=HOME_ROUTE_NAME,
        effect=EFFECT_PROPOSAL,
        default_phrases=(),
        handler=_home_control,
        matched_by_code=True,
        # ADR-018: the only route that may return `home_direct`.
        extra_effects=(EFFECT_DIRECT_HOME,),
    ),
)


# --- the table ------------------------------------------------------------------


def phrase_clash(a: str, b: str) -> bool:
    """Equal, or one is a whole-word prefix of the other (compared by
    `match_key`)."""
    a, b = match_key(a), match_key(b)
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return long.startswith(short + " ")


def validate_routes(routes=None) -> None:
    """Boot check (`AssistantConfig.ready`): names well-formed and unique,
    every effect `read` or `proposal`, phrases already normalized,
    non-empty, and no phrase equal to or a word-prefix of another route's.
    Anything else raises `ImproperlyConfigured` -- the app refuses to
    start."""
    routes = (*ROUTES, *CODE_ROUTES) if routes is None else routes
    names = set()
    owned = []
    for route in routes:
        if not _ROUTE_NAME.match(route.name or "") or route.name in names:
            raise ImproperlyConfigured(f"wake route name invalid or duplicated: {route.name!r}")
        names.add(route.name)
        if route.effect not in ALLOWED_EFFECTS:
            raise ImproperlyConfigured(
                f"wake route {route.name} has effect {route.effect!r}; only read or proposal"
            )
        if not callable(route.handler):
            raise ImproperlyConfigured(f"wake route {route.name} has no handler")
        extra = tuple(getattr(route, "extra_effects", ()) or ())
        if extra:
            # Phase 13b D10 (ADR-018): only `home.control` may return the
            # direct effect, and it is the only extra effect there is.
            if route.name != HOME_ROUTE_NAME or not route.matched_by_code:
                raise ImproperlyConfigured(
                    f"wake route {route.name} may not declare extra effects {extra!r}"
                )
            if any(effect not in ALLOWED_EXTRA_EFFECTS for effect in extra):
                raise ImproperlyConfigured(
                    f"wake route {route.name} declares an unknown extra effect {extra!r}"
                )
        if route.matched_by_code:
            # Phase 13 D13: a code matcher, never a phrase an alias could
            # shadow or that could shadow another route.
            if route.default_phrases:
                raise ImproperlyConfigured(
                    f"wake route {route.name} is matched by code and takes no phrase"
                )
            continue
        if not route.default_phrases:
            raise ImproperlyConfigured(f"wake route {route.name} has no phrase")
        for phrase in route.default_phrases:
            if not phrase or normalize_utterance(phrase) != phrase:
                raise ImproperlyConfigured(
                    f"wake route {route.name} phrase {phrase!r} is not normalized"
                )
            for other_name, other in owned:
                if other_name != route.name and phrase_clash(phrase, other):
                    raise ImproperlyConfigured(
                        f"wake route phrases clash: {route.name} {phrase!r} / "
                        f"{other_name} {other!r}"
                    )
            owned.append((route.name, phrase))


# --- tool routes (branch 4) -----------------------------------------------------
#
# `tool.<name>`: an owner alias for one read tool or one user-invocable gated
# tool. No default phrases (aliases only); the alias is matched as the
# leading words of the utterance and the rest is the request. A read tool
# yields exactly the router's `connector_read` turn (arguments from the
# remainder through `router.connector_args`, then the same read path, sync
# or async); a gated tool yields exactly the router's `tool_request` draft
# (D12: with router-extracted arguments for a tool that declares them) --
# a card, never an approval or an execution. Built from the registries at
# call time, so a tool that is no longer registered has no route.

TOOL_ROUTE_PREFIX = "tool."
# Phase 10 D13: the node action draft needs its own proposal step and the
# trust check's evidence; the router path stays its only door.
_TOOL_ROUTE_EXCLUDED = frozenset({"node_action"})


def _tool_read_handler(tool_name):
    def handler(turn: WakeTurn) -> RouteResult:
        return RouteResult(
            intent=Message.Intent.CONNECTOR_READ, intent_payload={"tool_name": tool_name}
        )

    return handler


def _tool_write_handler(tool_name):
    def handler(turn: WakeTurn) -> RouteResult:
        from assistant.services import DraftRefused, draft_tool_payload

        try:
            payload = draft_tool_payload(
                turn.conversation,
                actor=turn.actor,
                content=turn.remainder or turn.content,
                tool_name=tool_name,
                source_text=turn.content,
            )
        except DraftRefused as refused:
            # Phase 13 D13: the draft check refused (e.g. a device other
            # than the one the owner named) -- no card.
            return RouteResult(reply_text=refused.reply)
        if payload is None:
            return RouteResult()
        return RouteResult(
            intent=Message.Intent.TOOL_REQUEST,
            intent_payload={"tool_name": tool_name, "payload": payload},
        )

    return handler


def tool_route_label(name):
    """The Friday-authored label of a `tool.*` route's tool (its manifest
    label), else the tool name; "" for a non-tool route."""
    from tools.registry import get_read_tool, get_tool

    if not name.startswith(TOOL_ROUTE_PREFIX):
        return ""
    tool_name = name[len(TOOL_ROUTE_PREFIX) :]
    read = get_read_tool(tool_name)
    if read is not None:
        return read.display_label
    gated = get_tool(tool_name)
    return (getattr(gated, "label", "") or tool_name) if gated is not None else tool_name


def tool_routes():
    """Every `tool.*` route of the tools registered now, sorted by name."""
    from tools.registry import list_read_tool_names, list_tool_names

    routes = [
        WakeRoute(
            name=f"{TOOL_ROUTE_PREFIX}{name}",
            effect=EFFECT_READ,
            default_phrases=(),
            handler=_tool_read_handler(name),
        )
        for name in list_read_tool_names()
    ]
    routes += [
        WakeRoute(
            name=f"{TOOL_ROUTE_PREFIX}{name}",
            effect=EFFECT_PROPOSAL,
            default_phrases=(),
            handler=_tool_write_handler(name),
        )
        for name in list_tool_names()
        if name not in _TOOL_ROUTE_EXCLUDED
    ]
    return sorted(
        (route for route in routes if _ROUTE_NAME.match(route.name)), key=lambda r: r.name
    )


def all_routes():
    """The code-defined table, then the `tool.*` routes."""
    return (*ROUTES, *tool_routes())


def get_route(name):
    for route in ROUTES:
        if route.name == name:
            return route
    if isinstance(name, str) and name.startswith(TOOL_ROUTE_PREFIX):
        for route in tool_routes():
            if route.name == name:
                return route
    return None


# --- skill.<name> routes (Phase 12 D8/D15, branch 5) ------------------------------
#
# One route per usable (active or stale) instruction skill of the workspace,
# named `skill.<skill name>`. It has no default phrase: the owner gives it
# aliases on `/settings/wake-routes`. It matches a leading alias followed by
# the remainder ("pr açıklaması yaz şunu" with alias "pr açıklaması") and
# force-selects that skill for the turn -- no `skills.match` call. The
# effect is `read`: the skill only changes the completion's instructions;
# the normal router, the ADR-015 cap and the trust check still run.

SKILL_ROUTE_PREFIX = "skill."


def skill_route_name(skill_name):
    return f"{SKILL_ROUTE_PREFIX}{skill_name}"


def skill_name_of(route_name):
    if isinstance(route_name, str) and route_name.startswith(SKILL_ROUTE_PREFIX):
        return route_name[len(SKILL_ROUTE_PREFIX) :]
    return None


def skill_routes(actor, workspace):
    """The workspace's `skill.<name>` route names (usable skills only)."""
    from skills.selectors import usable_skills

    return [skill_route_name(skill.name) for skill in usable_skills(actor, workspace)]


def is_known_route(name, *, workspace=None):
    """A code-defined route, or the `skill.<name>` route of a usable skill
    of this workspace."""
    if get_route(name) is not None:
        return True
    skill_name = skill_name_of(name)
    if skill_name is None or workspace is None:
        return False
    from skills.selectors import usable_skill_by_name

    return usable_skill_by_name(workspace.owner, workspace, skill_name) is not None


def match_skill_alias(text, *, aliases=None):
    """(skill name, remainder) for an utterance that starts with an alias of
    a `skill.<name>` route, or None. The longest alias wins."""
    utterance = normalize_utterance(text)
    key = match_key(utterance)
    if not key:
        return None
    best = None
    for route_name, phrases in (aliases or {}).items():
        skill_name = skill_name_of(route_name)
        if skill_name is None:
            continue
        for phrase in phrases:
            alias_key = match_key(phrase)
            if key == alias_key or key.startswith(alias_key + " "):
                if best is None or len(alias_key) > len(best[2]):
                    best = (skill_name, utterance[len(phrase) :].strip(), alias_key)
    if best is None:
        return None
    return best[0], best[1]


def route_names():
    """The code-defined table's route names (the shared vectors cover
    these); `all_routes()` adds the `tool.*` routes."""
    return [route.name for route in ROUTES]


def aliases_for(workspace):
    """{route name: [phrase, ...]} of the workspace's owner aliases."""
    result = {}
    rows = WakeRouteAlias.objects.filter(workspace=workspace).order_by("created_at", "id")
    for route, phrase in rows.values_list("route", "phrase"):
        result.setdefault(route, []).append(phrase)
    return result


def match_utterance(text, *, aliases=None):
    """The route whose phrase (default or alias) equals the whole
    normalized utterance, or None. `aliases` is `aliases_for(workspace)`."""
    utterance = match_key(normalize_utterance(text))
    if not utterance:
        return None
    aliases = aliases or {}
    for route in ROUTES:
        phrases = (*route.default_phrases, *aliases.get(route.name, ()))
        if any(utterance == match_key(phrase) for phrase in phrases):
            return route
    return None


def match_turn(text, *, aliases=None):
    """`(route, remainder)` for an utterance, or `(None, "")`. The
    zero-argument routes first (whole utterance, `match_utterance`); then
    the `tool.*` aliases as leading words (longest alias first), the
    remainder being the normalized words after it."""
    route = match_utterance(text, aliases=aliases)
    if route is not None:
        return route, ""
    utterance = normalize_utterance(text)
    key = match_key(utterance)
    if not key:
        return None, ""
    candidates = [
        (phrase, name)
        for name, phrases in (aliases or {}).items()
        if name.startswith(TOOL_ROUTE_PREFIX)
        for phrase in phrases
    ]
    for phrase, name in sorted(candidates, key=lambda item: -len(item[0])):
        alias_key = match_key(phrase)
        if key == alias_key or key.startswith(alias_key + " "):
            tool_route = get_route(name)
            if tool_route is None:
                continue
            remainder = " ".join(utterance.split()[len(alias_key.split()) :])
            return tool_route, remainder
    return None, ""


def _is_workspace_owner(actor, workspace):
    return (
        actor is not None
        and getattr(actor, "is_authenticated", False)
        and not getattr(actor, "is_agent", False)
        and str(getattr(workspace, "owner_id", "")) == str(actor.pk)
    )


def static_reply_stream(text):
    """A route's reply as the chat turn's stream: one chunk, no gateway."""
    yield text


def route_voice_turn(
    conversation, *, actor, content, channel, input_mode, now=None, origin_node_id=None
):
    """The chat turn's hook (D8). None unless the turn is a voice turn of
    the workspace owner whose whole utterance is a route phrase; otherwise
    the route's capped classification and its reply text (None when the
    cap degraded the route: the turn is then answered as plain chat).
    Makes no model call. `origin_node_id` (Phase 13b): the speaking node of
    a `node_voice` turn, recorded on a direct call."""
    if input_mode != Message.InputMode.VOICE:
        return None
    workspace = conversation.workspace
    if not _is_workspace_owner(actor, workspace):
        return None
    aliases = aliases_for(workspace)
    route, remainder = match_turn(content, aliases=aliases)
    turn = WakeTurn(
        conversation=conversation,
        actor=actor,
        workspace=workspace,
        channel=channel,
        content=content,
        remainder=remainder,
        now=now or timezone.now(),
        origin_node_id=origin_node_id,
        input_mode=input_mode,
    )
    if route is None:
        # Phase 13 D13: the code-matched routes (`home.control`).
        for code_route in CODE_ROUTES:
            result = code_route.handler(turn)
            if result is not None:
                return _finish(code_route, result, input_mode=input_mode, remainder="")
        return _route_skill(actor, workspace, content, aliases)
    return _finish(route, route.handler(turn), input_mode=input_mode, remainder=remainder)


def _finish(route, result, *, input_mode, remainder):
    """The route's capped classification (ADR-015): degraded to plain chat
    with no reply when the cap changes it, or -- Phase 13b D10a lock 2 --
    when the result's own effect is not one the route declared (a read
    route proposing, any route but `home.control` returning `home_direct`);
    else its commit runs. A read result is always within a route's
    effect (it narrows, never widens)."""
    from assistant.services import INTENT_EFFECTS, PROVENANCE_KEY, apply_voice_capability_cap

    classification = {"intent": result.intent, "intent_payload": dict(result.intent_payload)}
    if result.provenance is not None:
        classification[PROVENANCE_KEY] = result.provenance
    permitted = {EFFECT_READ, route.effect, *tuple(route.extra_effects or ())}
    result_effect = INTENT_EFFECTS.get(result.intent)
    capped = apply_voice_capability_cap(classification, input_mode=input_mode)
    if (
        capped != classification
        or route.effect not in ALLOWED_EFFECTS
        or result_effect not in permitted
    ):
        return RoutedTurn(
            route=route.name,
            classification={"intent": Message.Intent.CHAT, "intent_payload": {}},
            reply_text=None,
        )
    if result.commit is not None:
        result.commit()
    return RoutedTurn(
        route=route.name,
        classification=capped,
        reply_text=result.reply_text,
        args_text=remainder,
    )


def _route_skill(actor, workspace, content, aliases):
    """A `skill.<name>` alias at the start of the utterance: the turn keeps
    the normal router and uses that skill (when it is still usable in this
    workspace); otherwise a miss."""
    from skills.selectors import usable_skill_by_name

    matched = match_skill_alias(content, aliases=aliases)
    if matched is None:
        return None
    skill_name, _remainder = matched
    if usable_skill_by_name(actor, workspace, skill_name) is None:
        return None
    return RoutedTurn(
        route=skill_route_name(skill_name),
        classification=None,
        reply_text=None,
        skill_name=skill_name,
    )
