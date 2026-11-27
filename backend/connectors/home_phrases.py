"""Home commands in the owner's words (Phase 13 D13, branch 6).

Two halves, both deterministic -- no model call, no Home Assistant request,
no row written:

1. **The parser** (`parse_home_command`, pure). Turkish first, English
   second, over the owner's own vocabulary: the labels and aliases of the
   enabled `HomeEntity` rows and the names and aliases of the workspace's
   `Room` rows (`core.speech` matching: whole words, longest match, one
   Turkish case suffix on the last word, locative rooms). The shapes:

   - `[<oda>(da|de|ta|te)(ki)] <etiket|takma ad> <fiil>` --
     "salondaki TV'yi aç", "salon tv'yi kapat", "TV'nin sesini kıs",
     "televizyonu HDMI 2'ye geç", "sesi yüzde 30'a ayarla";
   - `<sahne> (modunu|sahnesini)? aç` -- "film gecesi modunu aç";
   - English: "turn on the living room TV", "turn off the living room
     lights", "mute the TV", "switch the TV to HDMI 2", "set the volume to
     30 percent".

   The verb table maps every verb to exactly one action; a number is
   accepted only where a serializer bounds one (volume 0-100 % -> 0-1,
   brightness / fan / cover 0-100). Anything that does not parse completely
   is not a home command: the turn falls through to the normal router.

2. **The resolver** (`resolve_home_command`, read-only DB). Against the
   owner's enabled allowlist rows of the conversation's **personal**
   workspace (through `for_actor()`): one entity -> a `draft` (the exact
   `ha_call_service` arguments, validated by the bridge's own serializer
   and `allowlist_refusal`); several -> `ask`; nothing allowed -> a fixed
   `refuse`. The resolver never creates an `ApprovalRequest`; the draft is
   a card the owner taps, then decides on `/approvals`.

`check_call_draft` is the typed-turn half (`router.connector_args` output
for `ha_call_service`): the allowlist, and the owner's room/label mention
against the model's `entity_id` (`target_mismatch` -> no draft).

Registered into `tools.home_ports` by `ConnectorsConfig.ready` (assistant
never imports connectors, ADR-010)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from core.speech import LOCATIVE_SUFFIXES, find_mentions, phrase_key

GATE = "ha_call_service"

# Stripped once from the start / repeatedly from the end, exactly as
# `assistant.wake_routes` does (a test pins the two tables equal; connectors
# may not import assistant).
WAKE_PHRASES = ("hey friday", "friday")
TRAILING_FILLERS = frozenset({"lütfen", "please", "friday"})

# --- actions ------------------------------------------------------------------

ON = "on"
OFF = "off"
MUTE = "mute"
UNMUTE = "unmute"
VOLUME_UP = "volume_up"
VOLUME_DOWN = "volume_down"
PLAY = "play"
PAUSE = "pause"
# Phase 13b D7: "durdur" / "stop" is `media_stop` (it was `media_pause`);
# "oynat duraklat" / "play pause" / "toggle play" is `media_play_pause`.
STOP = "stop"
PLAY_PAUSE = "play_pause"
SOURCE = "source"
VOLUME = "volume"
LEVEL = "level"

# Actions that make sense without a named device: they can only be about a
# media player ("sesi kıs", "HDMI 2'ye geç").
MEDIA_ONLY = frozenset(
    {MUTE, UNMUTE, VOLUME_UP, VOLUME_DOWN, PLAY, PAUSE, STOP, PLAY_PAUSE, SOURCE, VOLUME}
)

# (tokens at the end of a Turkish utterance, action), longest first at match
# time. Keys are `match_key`-folded (ı -> i).
_TR_VERBS = (
    (("sesini", "aç"), UNMUTE),
    (("sesi", "aç"), UNMUTE),
    (("sesini", "kapat"), MUTE),
    (("sesi", "kapat"), MUTE),
    (("sessize", "al"), MUTE),
    (("sesini", "kis"), VOLUME_DOWN),
    (("sesi", "kis"), VOLUME_DOWN),
    (("sesini", "azalt"), VOLUME_DOWN),
    (("sesi", "azalt"), VOLUME_DOWN),
    (("sesini", "yükselt"), VOLUME_UP),
    (("sesi", "yükselt"), VOLUME_UP),
    (("sesini", "artir"), VOLUME_UP),
    (("sesi", "artir"), VOLUME_UP),
    (("devam", "ettir"), PLAY),
    (("oynat", "duraklat"), PLAY_PAUSE),
    (("oynat",), PLAY),
    (("duraklat",), PAUSE),
    (("durdur",), STOP),
    (("aç",), ON),
    (("kapat",), OFF),
)
_TR_SOURCE_VERB = "geç"
_TR_LEVEL_VERB = "ayarla"
_TR_VOLUME_WORDS = frozenset({"sesini", "sesi"})
_TR_PERCENT = "yüzde"
# The dative ending a source or a number carries ("HDMI 2'ye", "30'a").
_DATIVE = frozenset({"a", "e", "ya", "ye", "na", "ne"})

# (tokens at the start of an English utterance, action).
_EN_VERBS = (
    (("turn", "on"), ON),
    (("switch", "on"), ON),
    (("turn", "off"), OFF),
    (("switch", "off"), OFF),
    (("turn", "up"), VOLUME_UP),
    (("turn", "down"), VOLUME_DOWN),
    (("unmute",), UNMUTE),
    (("mute",), MUTE),
    (("play", "pause"), PLAY_PAUSE),
    (("toggle", "play"), PLAY_PAUSE),
    (("pause",), PAUSE),
    (("stop",), STOP),
    (("resume",), PLAY),
    (("play",), PLAY),
    (("activate",), ON),
)
# "turn the TV on" / "turn the volume down".
_EN_TRAILING = {"on": ON, "off": OFF, "up": VOLUME_UP, "down": VOLUME_DOWN}
_EN_SOURCE_VERBS = frozenset({"switch", "change"})
_EN_ARTICLES = frozenset({"the", "my"})
_EN_PERCENT = frozenset({"percent", "per", "cent"})
_EN_VOLUME = "volume"

# Words after a scene's label ("film gecesi modunu aç").
_SCENE_WORDS = frozenset({"modunu", "modu", "sahnesini", "sahnesi", "mode", "scene"})

LEVEL_MIN, LEVEL_MAX = 0, 100

ROOM = "room"
ENTITY = "entity"


@dataclass(frozen=True)
class Action:
    kind: str
    # The verb as the owner said it (normalized), for the refusal text.
    verb: str
    value: Any = None


@dataclass(frozen=True)
class HomeCommand:
    """A parsed command: the action, the language it was said in, the
    targets its label matched (all equally good matches; empty when no
    device was named), the room it named (or None) and an unknown
    locative word ("mutfaktaki" with no such room)."""

    action: Action
    language: str
    entities: tuple = ()
    entity_phrase: str = ""
    room: Any = None
    room_phrase: str = ""
    unknown_room: str = ""


# --- tokens -------------------------------------------------------------------


def _raw_tokens(text):
    """`normalize_speech`'s tokenization without the lower-casing, so a
    source keeps the owner's spelling ("HDMI 2")."""
    text = unicodedata.normalize("NFC", str(text or ""))
    kept = [char if unicodedata.category(char)[0] in ("L", "N") else " " for char in text]
    return "".join(kept).split()


def _tokens(text):
    """(raw tokens, keys) of `text` with one leading wake phrase and the
    trailing fillers stripped; None when a token does not normalize to
    exactly one key (never guess)."""
    raw = _raw_tokens(text)
    keys = [phrase_key(token) for token in raw]
    if any(not key or " " in key for key in keys):
        return None
    for phrase in WAKE_PHRASES:
        words = phrase.split()
        if keys[: len(words)] == words:
            raw, keys = raw[len(words) :], keys[len(words) :]
            break
    while keys and keys[-1] in TRAILING_FILLERS:
        raw, keys = raw[:-1], keys[:-1]
    return raw, keys


def _spoken(raw):
    return " ".join(raw).replace("I", "ı").replace("İ", "i").lower() if raw else ""


def _number(key):
    if not key.isdigit() or len(key) > 3:
        return None
    value = int(key)
    return value if LEVEL_MIN <= value <= LEVEL_MAX else None


# --- the object phrase ("salondaki TV'yi") ------------------------------------


@dataclass(frozen=True)
class _Object:
    end: int
    room: Any = None
    room_phrase: str = ""
    entities: tuple = ()
    entity_phrase: str = ""
    unknown_room: str = ""


def _is_locative(key):
    return any(key.endswith(suffix) and len(key) > len(suffix) + 1 for suffix in LOCATIVE_SUFFIXES)


def _parse_object(raw, keys, vocabulary):
    """The leading room/device words of `raw`: `[article] [room | unknown
    locative] [article] [device] [scene word]`. Returns how far it got."""
    if not raw:
        return _Object(end=0)
    mentions = find_mentions(" ".join(raw), vocabulary)
    at = {}
    for mention in mentions:
        at.setdefault(mention.start, []).append(mention)
    pos = 0
    room, room_phrase, unknown = None, "", ""

    def skip_articles(index):
        while index < len(keys) and keys[index] in _EN_ARTICLES:
            index += 1
        return index

    def kinds(index, kind):
        return [m for m in at.get(index, ()) if m.target[0] == kind]

    pos = skip_articles(pos)
    rooms = kinds(pos, ROOM)
    if rooms:
        end = max(m.end for m in rooms)
        devices_here = kinds(pos, ENTITY)
        # A label equal to a room name ("Salon" the light): a room only when
        # a device follows it.
        if not devices_here or kinds(skip_articles(end), ENTITY):
            room, room_phrase = rooms[0].target[1], rooms[0].phrase
            pos = end
    elif pos < len(keys) and not at.get(pos):
        # "mutfaktaki TV" with no room "mutfak": an unknown room, said as one.
        if pos + 1 < len(keys) and keys[pos + 1] in LOCATIVE_SUFFIXES:
            if kinds(skip_articles(pos + 2), ENTITY):
                unknown, pos = raw[pos], pos + 2
        elif _is_locative(keys[pos]) and kinds(skip_articles(pos + 1), ENTITY):
            unknown, pos = raw[pos], pos + 1
    pos = skip_articles(pos)
    devices = kinds(pos, ENTITY)
    entities = ()
    entity_phrase = ""
    if devices:
        seen = []
        for mention in devices:
            if mention.target[1] not in seen:
                seen.append(mention.target[1])
        entities, entity_phrase = tuple(seen), devices[0].phrase
        pos = max(m.end for m in devices)
        if pos < len(keys) and keys[pos] in _SCENE_WORDS:
            pos += 1
    return _Object(
        end=pos,
        room=room,
        room_phrase=room_phrase,
        entities=entities,
        entity_phrase=entity_phrase,
        unknown_room=unknown,
    )


def _command(action, language, obj):
    if not obj.entities and not obj.unknown_room and action.kind not in MEDIA_ONLY:
        # "salonu kapat": a room alone is not a device.
        return None
    return HomeCommand(
        action=action,
        language=language,
        entities=obj.entities,
        entity_phrase=obj.entity_phrase,
        room=obj.room,
        room_phrase=obj.room_phrase,
        unknown_room=obj.unknown_room,
    )


def _whole_object(raw, keys, vocabulary):
    obj = _parse_object(raw, keys, vocabulary)
    return obj if obj.end == len(keys) else None


# --- Turkish ------------------------------------------------------------------


def _source_text(raw, keys):
    """The source words before "geç", without their dative ending."""
    if not raw:
        return ""
    if len(keys) > 1 and keys[-1] in _DATIVE:
        raw = raw[:-1]
    else:
        last_key, last = keys[-1], raw[-1]
        for suffix in ("ya", "ye", "a", "e"):
            if last_key.endswith(suffix) and len(last_key) - len(suffix) >= 2:
                raw = [*raw[:-1], last[: len(last) - len(suffix)]]
                break
    return " ".join(raw).strip()


def _parse_tr(raw, keys, vocabulary):
    if not keys:
        return None
    tail = keys[-1]
    if tail == _TR_SOURCE_VERB:
        body_raw, body_keys = raw[:-1], keys[:-1]
        obj = _parse_object(body_raw, body_keys, vocabulary)
        source = _source_text(body_raw[obj.end :], body_keys[obj.end :])
        if not source:
            return None
        return _command(Action(SOURCE, f"{source} girişine geç", source), "tr", obj)
    if tail == _TR_LEVEL_VERB:
        body_raw, body_keys = raw[:-1], keys[:-1]
        if body_keys and body_keys[-1] in _DATIVE:
            body_raw, body_keys = body_raw[:-1], body_keys[:-1]
        if not body_keys:
            return None
        value = _number(body_keys[-1])
        if value is None:
            return None
        body_raw, body_keys = body_raw[:-1], body_keys[:-1]
        if body_keys and body_keys[-1] == _TR_PERCENT:
            body_raw, body_keys = body_raw[:-1], body_keys[:-1]
        kind = LEVEL
        if body_keys and body_keys[-1] in _TR_VOLUME_WORDS:
            kind = VOLUME
            body_raw, body_keys = body_raw[:-1], body_keys[:-1]
        obj = _whole_object(body_raw, body_keys, vocabulary)
        if obj is None:
            return None
        verb = f"sesi %{value}" if kind == VOLUME else f"%{value} ayarla"
        return _command(Action(kind, verb, value), "tr", obj)
    for words, kind in sorted(_TR_VERBS, key=lambda item: -len(item[0])):
        if tuple(keys[-len(words) :]) == words and len(keys) >= len(words):
            body_raw, body_keys = raw[: -len(words)], keys[: -len(words)]
            obj = _whole_object(body_raw, body_keys, vocabulary)
            if obj is None:
                return None
            return _command(Action(kind, _spoken(raw[-len(words) :])), "tr", obj)
    return None


# --- English ------------------------------------------------------------------


def _parse_en(raw, keys, vocabulary):
    if not keys:
        return None
    head = keys[0]
    if head in _EN_SOURCE_VERBS and "to" in keys[1:] and keys[1] not in ("on", "off"):
        cut = len(keys) - 1 - keys[::-1].index("to")
        obj = _whole_object(raw[1:cut], keys[1:cut], vocabulary)
        source = " ".join(raw[cut + 1 :]).strip()
        if obj is None or not source:
            return None
        return _command(Action(SOURCE, f"switch to {source}", source), "en", obj)
    if head == "set" and "to" in keys[1:]:
        cut = len(keys) - 1 - keys[::-1].index("to")
        rest = keys[cut + 1 :]
        while rest and rest[-1] in _EN_PERCENT:
            rest = rest[:-1]
        if len(rest) != 1 or _number(rest[0]) is None:
            return None
        value = _number(rest[0])
        body_raw, body_keys = raw[1:cut], keys[1:cut]
        kind = LEVEL
        if body_keys and body_keys[-1] == _EN_VOLUME:
            kind = VOLUME
            body_raw, body_keys = body_raw[:-1], body_keys[:-1]
        obj = _whole_object(body_raw, body_keys, vocabulary)
        if obj is None:
            return None
        verb = f"volume {value}%" if kind == VOLUME else f"set to {value}%"
        return _command(Action(kind, verb, value), "en", obj)
    for words, kind in sorted(_EN_VERBS, key=lambda item: -len(item[0])):
        if tuple(keys[: len(words)]) == words:
            body_raw, body_keys = raw[len(words) :], keys[len(words) :]
            if kind in (VOLUME_UP, VOLUME_DOWN) and body_keys[-1:] == [_EN_VOLUME]:
                body_raw, body_keys = body_raw[:-1], body_keys[:-1]
            obj = _whole_object(body_raw, body_keys, vocabulary)
            if obj is None:
                return None
            return _command(Action(kind, " ".join(words)), "en", obj)
    if head == "turn" and len(keys) > 2 and keys[-1] in _EN_TRAILING:
        kind = _EN_TRAILING[keys[-1]]
        body_raw, body_keys = raw[1:-1], keys[1:-1]
        if kind in (VOLUME_UP, VOLUME_DOWN) and body_keys[-1:] == [_EN_VOLUME]:
            body_raw, body_keys = body_raw[:-1], body_keys[:-1]
        obj = _whole_object(body_raw, body_keys, vocabulary)
        if obj is None:
            return None
        return _command(Action(kind, f"turn {keys[-1]}"), "en", obj)
    return None


def parse_home_command(text, *, entities=(), rooms=()):
    """The home command `text` says, or None. `entities` are `(phrase,
    target)` pairs (labels, aliases and their plural forms), `rooms` the
    same for room names and aliases. Pure: no I/O."""
    tokens = _tokens(text)
    if tokens is None:
        return None
    raw, keys = tokens
    if not keys:
        return None
    vocabulary = [(phrase, (ROOM, room)) for phrase, room in rooms] + [
        (phrase, (ENTITY, entity)) for phrase, entity in entities
    ]
    return _parse_tr(raw, keys, vocabulary) or _parse_en(raw, keys, vocabulary)


# --- vocabulary from the owner's rows ------------------------------------------

_BACK_VOWELS = frozenset("aıou")
_FRONT_VOWELS = frozenset("eiöü")


def _plurals(phrase):
    """A label's plural forms: Turkish (-lar/-ler by vowel harmony, so
    "ışıkları kapat" matches "ışık") and English (-s, "lights")."""
    spoken = " ".join(phrase.split())
    if not spoken:
        return ()
    lowered = spoken.replace("I", "ı").replace("İ", "i").lower()
    vowel = next((c for c in reversed(lowered) if c in _BACK_VOWELS | _FRONT_VOWELS), "")
    forms = [spoken + ("lar" if vowel in _BACK_VOWELS else "ler")]
    if not lowered.endswith("s") and lowered[-1:].isalpha():
        forms.append(spoken + "s")
    return tuple(forms)


def entity_vocabulary(rows):
    """`(phrase, row)` for every label, alias and their plurals."""
    vocabulary = []
    for row in rows:
        for phrase in (row.label, *(row.aliases or [])):
            if not isinstance(phrase, str) or not phrase.strip():
                continue
            vocabulary.append((phrase, row))
            vocabulary.extend((plural, row) for plural in _plurals(phrase))
    return vocabulary


def room_vocabulary(rooms):
    vocabulary = []
    for room in rooms:
        for phrase in (room.name, *(room.aliases or [])):
            if isinstance(phrase, str) and phrase.strip():
                vocabulary.append((phrase, room))
    return vocabulary


# --- action -> (service, data) ------------------------------------------------


def service_for(action, domain):
    """`(service, data)` an action means on `domain`, or None."""
    kind, value = action.kind, action.value
    if kind == ON:
        if domain in ("light", "switch", "fan", "media_player", "scene"):
            return "turn_on", {}
        if domain == "cover":
            return "open_cover", {}
        return None
    if kind == OFF:
        if domain in ("light", "switch", "fan", "media_player"):
            return "turn_off", {}
        if domain == "cover":
            return "close_cover", {}
        return None
    if domain == "media_player":
        simple = {
            MUTE: ("volume_mute", {"is_volume_muted": True}),
            UNMUTE: ("volume_mute", {"is_volume_muted": False}),
            VOLUME_UP: ("volume_up", {}),
            VOLUME_DOWN: ("volume_down", {}),
            PLAY: ("media_play", {}),
            PAUSE: ("media_pause", {}),
            STOP: ("media_stop", {}),
            PLAY_PAUSE: ("media_play_pause", {}),
        }
        if kind in simple:
            return simple[kind]
        if kind == SOURCE:
            return "select_source", {"source": value}
        if kind in (VOLUME, LEVEL):
            return "volume_set", {"volume_level": round(value / 100, 2)}
        return None
    if kind == LEVEL:
        if domain == "light" and value >= 1:
            return "turn_on", {"brightness_pct": value}
        if domain == "fan":
            return "set_percentage", {"percentage": value}
        if domain == "cover":
            return "set_cover_position", {"position": value}
    return None


# --- templates (Friday-authored, never HA or model text) ------------------------


def display_name(row):
    """ "Salon TV": the room's name before the label unless the label already
    says it."""
    label = row.label
    room = getattr(row, "room", None)
    if room is None or phrase_key(room.name) in phrase_key(label):
        return label
    return f"{room.name} {label}"


_TEXT = {
    "tr": {
        "draft": "Onay kartı hazır: {name} · {verb}. Kararı ekranda verirsin.",
        "ambiguous": "{count} cihaz eşleşti: {names}. Hangisi?",
        "unknown_room": "Bu odayı tanımıyorum: {word}.",
        "not_found_room": "{room} için '{phrase}' adlı izinli bir cihaz yok.",
        "not_found": "'{phrase}' adlı izinli bir cihaz yok.",
        "no_media": "Bunun için izinli bir medya cihazı yok.",
        "service_not_allowed": "Bunu yapmaya iznim yok: {name} için '{verb}' açık değil.",
        "invalid": "Bu komutu {name} için hazırlayamadım.",
        "not_connected": "Home Assistant bağlı değil; taslak oluşturmadım.",
    },
    "en": {
        "draft": "The card is ready: {name} · {verb}. Decide on screen.",
        "ambiguous": "{count} devices match: {names}. Which one?",
        "unknown_room": "I don't know that room: {word}.",
        "not_found_room": "There is no allowed device called '{phrase}' in {room}.",
        "not_found": "There is no allowed device called '{phrase}'.",
        "no_media": "There is no allowed media device for that.",
        "service_not_allowed": "I'm not allowed to do that: '{verb}' is not enabled for {name}.",
        "invalid": "I couldn't prepare that command for {name}.",
        "not_connected": "Home Assistant is not connected; no card was made.",
    },
}


def _text(language, key, **values):
    return _TEXT.get(language, _TEXT["tr"])[key].format(**values)


# --- the resolver (read-only DB) ---------------------------------------------


def _allowlist(workspace, actor):
    from connectors.models import HomeEntity

    return list(
        HomeEntity.objects.for_actor(actor, workspace=workspace)
        .filter(enabled=True)
        .select_related("room")
        .order_by("entity_id")
    )


def _rooms(workspace, actor):
    from workspaces.models import Room

    return list(Room.objects.for_actor(actor, workspace=workspace))


def _personal(workspace):
    from workspaces.models import Workspace

    return workspace is not None and workspace.type == Workspace.Type.PERSONAL


def _connected(workspace):
    from connectors.manifest import get_manifest
    from connectors.services import enabled_binding, tool_available

    manifest = get_manifest("home_assistant")
    if manifest is None or manifest.blocked:
        return False
    policy = manifest.get_tool(GATE)
    return policy is not None and tool_available(enabled_binding(workspace, manifest.name), policy)


def _resolution(outcome, reply, code, **extra):
    from tools.home_ports import HomeResolution

    return HomeResolution(outcome=outcome, reply=reply, code=code, **extra)


def _ask(language, rows):
    from tools.home_ports import OUTCOME_ASK

    names = sorted(display_name(row) for row in rows)
    return _resolution(
        OUTCOME_ASK,
        _text(language, "ambiguous", count=len(names), names=", ".join(names)),
        "ambiguous",
        candidates=tuple(row.entity_id for row in sorted(rows, key=lambda r: r.entity_id)),
    )


def _refuse(language, key, code=None, **values):
    from tools.home_ports import OUTCOME_REFUSE

    return _resolution(OUTCOME_REFUSE, _text(language, key, **values), code or key)


def _arguments_for(workspace, row, action):
    """`(arguments, refusal code)` for one entity: the action's service on
    its domain, inside its allowlisted services, valid by the bridge's own
    serializer."""
    from connectors.bridges import home_assistant as bridge

    mapped = service_for(action, row.domain)
    if mapped is None or mapped[0] not in (row.services or []):
        return None, "service_not_allowed"
    service, data = mapped
    arguments = {"domain": row.domain, "service": service, "entity_id": row.entity_id, "data": data}
    serializer = bridge.CallServiceArguments(data=arguments)
    if not serializer.is_valid():
        return None, "invalid"
    arguments = dict(serializer.validated_data)
    refusal = bridge.allowlist_refusal(workspace, arguments)
    if refusal is not None:
        return None, refusal
    return arguments, None


def resolve_home_command(*, workspace, actor, text):
    """`tools.home_ports` resolver: a `HomeResolution`, or None when `text`
    is not a home command of this workspace's vocabulary."""
    from tools.home_ports import OUTCOME_DRAFT

    if not _personal(workspace):
        return None
    rows = _allowlist(workspace, actor)
    if not rows:
        return None
    rooms = _rooms(workspace, actor)
    command = parse_home_command(
        text, entities=entity_vocabulary(rows), rooms=room_vocabulary(rooms)
    )
    if command is None:
        return None
    language = command.language
    if command.unknown_room:
        return _refuse(language, "unknown_room", word=command.unknown_room)
    if not _connected(workspace):
        return _refuse(language, "not_connected")
    action = command.action
    if command.entities:
        candidates = list(command.entities)
        if command.room is not None:
            candidates = [row for row in candidates if row.room_id == command.room.pk]
        if not candidates:
            if command.room is not None:
                return _refuse(
                    language,
                    "not_found_room",
                    code="not_found",
                    room=command.room.name,
                    phrase=command.entity_phrase,
                )
            return _refuse(language, "not_found", phrase=command.entity_phrase)
        if len(candidates) > 1:
            return _ask(language, candidates)
        (row,) = candidates
    else:
        # No device named: a media-only action ("sesi yüzde 30'a ayarla")
        # on the one media player that allows it.
        players = [row for row in rows if row.domain == "media_player"]
        if command.room is not None:
            players = [row for row in players if row.room_id == command.room.pk]
        if not players:
            return _refuse(language, "no_media", code="not_found")
        allowed = [row for row in players if _arguments_for(workspace, row, action)[0]]
        if len(allowed) > 1:
            return _ask(language, allowed)
        row = allowed[0] if allowed else (players[0] if len(players) == 1 else None)
        if row is None:
            return _refuse(language, "no_media", code="service_not_allowed")
    arguments, refusal = _arguments_for(workspace, row, action)
    name = display_name(row)
    if refusal is not None:
        key = "invalid" if refusal == "invalid" else "service_not_allowed"
        return _refuse(language, key, code=refusal, name=name, verb=action.verb)
    return _resolution(
        OUTCOME_DRAFT,
        _text(language, "draft", name=name, verb=action.verb),
        "draft",
        tool_name=GATE,
        arguments=arguments,
        candidates=(row.entity_id,),
    )


# --- the typed-turn check (router.connector_args output) -----------------------

MISMATCH_ONE = (
    "Mesajında {name} geçiyor ama taslak başka bir cihaz içindi ({entity_id}); "
    "taslak oluşturmadım. {name} için yeniden ister misin?"
)
MISMATCH_MANY = (
    "Mesajında şu cihazlar geçiyor: {names}; taslak başka bir cihaz içindi "
    "({entity_id}). Taslak oluşturmadım; hangisini kastettin?"
)
MISMATCH_NONE = (
    "Mesajında söylediğin yerde izinli bir cihaz yok; taslak başka bir cihaz içindi "
    "({entity_id}). Taslak oluşturmadım."
)
ALLOWLIST_REFUSAL = {
    "entity_not_allowed": "Bu cihaz Home Assistant izin listende yok; taslak oluşturmadım.",
    "service_not_allowed": "Bu işlem bu cihaz için izin listende açık değil; taslak oluşturmadım.",
}


def mentioned_targets(text, rows, rooms):
    """The allowlist rows the owner's own words point at, or None when they
    name neither a device nor a room. A named device narrows by a named room
    ("salondaki TV", "salon tv'yi"); a room alone ("salondakini") means its
    devices. A room counts when said in the locative or right before a
    device."""
    vocabulary = [(phrase, (ROOM, room)) for phrase, room in room_vocabulary(rooms)] + [
        (phrase, (ENTITY, row)) for phrase, row in entity_vocabulary(rows)
    ]
    mentions = find_mentions(text, vocabulary)
    entity_starts = {m.start for m in mentions if m.target[0] == ENTITY}
    room_ids = {
        m.target[1].pk
        for m in mentions
        if m.target[0] == ROOM and (m.suffix in LOCATIVE_SUFFIXES or m.end in entity_starts)
    }
    named = []
    for mention in mentions:
        if mention.target[0] == ENTITY and mention.target[1] not in named:
            named.append(mention.target[1])
    if named:
        if room_ids:
            return [row for row in named if row.room_id in room_ids]
        return named
    if room_ids:
        return [row for row in rows if row.room_id in room_ids]
    return None


def check_call_draft(*, workspace, actor, arguments, source_text):
    """`tools.home_ports` draft check of `ha_call_service`: None (the card
    may be shown) or the refusal the turn answers with. The model's
    `entity_id` never overrides the device the owner named."""
    from connectors.bridges import home_assistant as bridge

    arguments = arguments or {}
    refusal = bridge.allowlist_refusal(workspace, arguments)
    if refusal is not None:
        return ALLOWLIST_REFUSAL.get(refusal, ALLOWLIST_REFUSAL["entity_not_allowed"])
    if arguments.get("domain") == bridge.WAKE_ON_LAN:
        return None
    rows = _allowlist(workspace, actor)
    expected = mentioned_targets(source_text or "", rows, _rooms(workspace, actor))
    if expected is None:
        return None
    entity_id = arguments.get("entity_id")
    if entity_id in {row.entity_id for row in expected}:
        return None
    if not expected:
        return MISMATCH_NONE.format(entity_id=entity_id)
    if len(expected) == 1:
        return MISMATCH_ONE.format(name=display_name(expected[0]), entity_id=entity_id)
    names = ", ".join(sorted(display_name(row) for row in expected))
    return MISMATCH_MANY.format(names=names, entity_id=entity_id)


def call_vocabulary(*, workspace, actor):
    """`tools.home_ports` draft vocabulary of `ha_call_service`: the owner's
    allowlist rows as data for `router.connector_args`."""
    if not _personal(workspace):
        return []
    return [
        {
            "entity_id": row.entity_id,
            "room": row.room.name if row.room is not None else None,
            "label": row.label,
            "aliases": list(row.aliases or []),
            "services": list(row.services or []),
        }
        for row in _allowlist(workspace, actor)
    ]


def register():
    """`ConnectorsConfig.ready`: the three home ports."""
    from tools.home_ports import (
        register_draft_check,
        register_draft_vocabulary,
        register_home_resolver,
    )

    register_home_resolver(resolve_home_command)
    register_draft_check(GATE, check_call_draft)
    register_draft_vocabulary(GATE, call_vocabulary)
