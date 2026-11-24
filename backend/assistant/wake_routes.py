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
- **Scope.** Routes answer only the owner of the conversation's workspace,
  from that workspace's rows (`for_actor()` through `proactive.api` and
  `memory.probes`): a firm digest never answers a personal conversation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from assistant.models import Message, WakeRouteAlias

EFFECT_READ = "read"
EFFECT_PROPOSAL = "proposal"
ALLOWED_EFFECTS = frozenset({EFFECT_READ, EFFECT_PROPOSAL})

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


def _turkish_lower(text: str) -> str:
    # `toLocaleLowerCase("tr-TR")`: dotted capital I -> i, dotless capital
    # I -> dotless ı, then the ordinary lower-casing.
    return text.replace("İ", "i").replace("I", "ı").lower()


def normalize_speech(text: str) -> str:
    """`self-trigger.ts` `normalizeSpeech`, ported: NFC, Turkish lower
    case, NFC again, non-letter/digit/space characters to spaces,
    whitespace collapsed."""
    text = unicodedata.normalize("NFC", str(text or ""))
    text = unicodedata.normalize("NFC", _turkish_lower(text))
    kept = []
    for char in text:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char)[0] in ("L", "N"):
            kept.append(char)
        else:
            kept.append(" ")
    return " ".join("".join(kept).split())


def match_key(text: str) -> str:
    """The comparison form of a normalized phrase: dotless ı folded to i.
    An all-caps transcript ("HEY FRIDAY", "BRIEFING") lower-cases the
    Turkish way to "frıday" / "brıefıng"; the closed phrase table is small
    enough that folding costs no real collision, and every comparison
    (wake phrase, fillers, routes, aliases, clashes) uses it."""
    return text.replace("ı", "i")


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


@dataclass
class RouteResult:
    """A handler's answer: the classification it stands for (checked by
    the ADR-015 cap), the reply text spoken back with no model call, and
    the effect to commit only if the cap keeps the route."""

    intent: str = Message.Intent.CHAT
    intent_payload: dict = field(default_factory=dict)
    reply_text: str | None = None
    commit: Callable[[], None] | None = None


@dataclass(frozen=True)
class WakeRoute:
    name: str
    effect: str
    default_phrases: tuple
    handler: Callable[[WakeTurn], RouteResult]


@dataclass(frozen=True)
class RoutedTurn:
    """`route_voice_turn`'s answer to the chat turn."""

    route: str
    classification: dict
    reply_text: str | None


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
    routes = ROUTES if routes is None else routes
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


def get_route(name):
    for route in ROUTES:
        if route.name == name:
            return route
    return None


def route_names():
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


def route_voice_turn(conversation, *, actor, content, channel, input_mode, now=None):
    """The chat turn's hook (D8). None unless the turn is a voice turn of
    the workspace owner whose whole utterance is a route phrase; otherwise
    the route's capped classification and its reply text (None when the
    cap degraded the route: the turn is then answered as plain chat).
    Makes no model call."""
    from assistant.services import apply_voice_capability_cap

    if input_mode != Message.InputMode.VOICE:
        return None
    workspace = conversation.workspace
    if not _is_workspace_owner(actor, workspace):
        return None
    route = match_utterance(content, aliases=aliases_for(workspace))
    if route is None:
        return None
    turn = WakeTurn(
        conversation=conversation,
        actor=actor,
        workspace=workspace,
        channel=channel,
        content=content,
        now=now or timezone.now(),
    )
    result = route.handler(turn)
    classification = {"intent": result.intent, "intent_payload": dict(result.intent_payload)}
    capped = apply_voice_capability_cap(classification, input_mode=input_mode)
    if capped != classification or route.effect not in ALLOWED_EFFECTS:
        return RoutedTurn(
            route=route.name,
            classification={"intent": Message.Intent.CHAT, "intent_payload": {}},
            reply_text=None,
        )
    if result.commit is not None:
        result.commit()
    return RoutedTurn(route=route.name, classification=capped, reply_text=result.reply_text)
