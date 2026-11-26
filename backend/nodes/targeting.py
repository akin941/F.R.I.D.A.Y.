"""The "which PC" resolver (Phase 13 D6): one deterministic rule set per node call.

```
resolve_target(owner, *, capabilities, source_text="", origin_node_id=None,
               requested_node_id=None, requested_label=None, now=None) -> Target
Target = Resolved(node, reason) | Ambiguous(candidates, reason)
       | Refused(code, node=None)
```

No model call, no write, no I/O beyond the owner's node rows, their latest
snapshots and their presence keys. The owner's nodes are read through
`nodes.selectors.nodes_for()` (`for_actor()`); another owner's node is never
a candidate, never matched and never named.

Terms:
- **labelled** nodes: the owner's nodes that hold their labels -- usable
  ones and kill-switched ones (Phase 13 branch 1: the switch keeps names
  reserved). Revoked or expired nodes are never matched: a new node may
  carry their old name.
- **usable**: labelled and not refused at all (`node_device_refusal`).
- **eligible**: usable, online (a live presence key) and offering one of
  `capabilities` as invocable in its latest snapshot.

Rule order (the first rule that decides wins):

1. **Explicit.** The owner's own words: `source_text` -- only ever the
   owner's own message, never connector, transcript or node text -- is
   searched with `core.speech.find_mentions` for the labelled nodes' names
   and aliases. One node named -> that node when eligible, else `Refused`
   naming it (`node_offline`, `node_lacks_capability`, `node_unusable`):
   never a silent fall-through to another node. Two nodes named ->
   `Ambiguous` among them. Without a name: a room in locative form
   ("salondaki", "çalışma odasındaki", `core.speech.match_room`), a role
   word ("medya bilgisayarı", "dizüstü") or a platform word ("Mac'te",
   "Linux'ta") narrows the labelled nodes; one left -> as a named node;
   several -> the eligible ones among them (one -> it, several ->
   `Ambiguous`, none -> `not_available`). A room of the owner with no node
   in it -> `Refused(no_node_in_room)`; a role or platform word no node has
   is ignored (they are common words).
2. **The speaking node.** `origin_node_id` (a `node_voice` turn: the
   token's `node_device_id`) when that node is eligible.
3. **Last active.** Among the eligible nodes: exactly one -> it
   (`only_candidate`); several -> the one whose `last_active_at` is within
   `NODE_LAST_ACTIVE_WINDOW_SECONDS` and strictly later than every other's
   (`last_active`).
4. **Ask.** Otherwise `Ambiguous(eligible)`. No eligible node: the owner's
   only usable node, named (`node_offline` / `node_lacks_capability`), else
   `Refused(not_available)`.

`requested_node_id` (the router's `node_id` argument) and `requested_label`
(the router's `node` argument) are **untrusted model output**: they never
decide. The resolution above runs without them and they must agree with
it -- a different node is `Refused(target_mismatch)`; a label or id naming
no labelled node of the owner is `Refused(node_unknown)`; an `Ambiguous`
result stays `Ambiguous` whatever the model picked (the model's choice never
overrides, or settles, the owner's words).
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.devices import node_device_refusal
from core.speech import find_mentions, match_room, phrase_key
from nodes import presence
from nodes.models import Node
from nodes.selectors import latest_snapshot, nodes_for
from workspaces.models import Room, Workspace

# Reasons of a `Resolved` / `Ambiguous` result.
REASON_MENTION = "mention"
REASON_ORIGIN = "origin"
REASON_ONLY_CANDIDATE = "only_candidate"
REASON_LAST_ACTIVE = "last_active"
REASON_ASK = "ask"

# Codes of a `Refused` result (closed vocabulary).
NODE_OFFLINE = "node_offline"
NODE_LACKS_CAPABILITY = "node_lacks_capability"
NODE_UNUSABLE = "node_unusable"
NODE_UNKNOWN = "node_unknown"
NO_NODE_IN_ROOM = "no_node_in_room"
TARGET_MISMATCH = "target_mismatch"
NOT_AVAILABLE = "not_available"
REFUSAL_CODES = frozenset(
    {
        NODE_OFFLINE,
        NODE_LACKS_CAPABILITY,
        NODE_UNUSABLE,
        NODE_UNKNOWN,
        NO_NODE_IN_ROOM,
        TARGET_MISMATCH,
        NOT_AVAILABLE,
    }
)
# The code an `Ambiguous` result is reported as (audit rows, tool errors).
NODE_AMBIGUOUS = "node_ambiguous"

# Role words (Friday-authored, Turkish first): "medya bilgisayarında",
# "dizüstündeki". A bare "masaüstü" is deliberately absent: it also means
# the screen ("masaüstündeki pencereler"); owners who call a PC that add it
# as the node's alias.
ROLE_WORDS = {
    Node.Role.WORKSTATION: ("masaüstü bilgisayar", "iş istasyonu", "workstation"),
    Node.Role.LAPTOP: ("dizüstü", "dizüstü bilgisayar", "laptop"),
    Node.Role.MEDIA: ("medya bilgisayarı", "medya pc", "media pc"),
    Node.Role.SERVER: ("sunucu", "ev sunucusu", "server", "home server"),
}
PLATFORM_WORDS = {
    Node.Platform.MACOS: ("mac", "macos"),
    Node.Platform.LINUX: ("linux",),
    Node.Platform.WINDOWS: ("windows",),
}
# How many candidate names the ask template lists.
MAX_ASK_NAMES = 5


@dataclass(frozen=True)
class Resolved:
    node: Node
    reason: str

    @property
    def code(self) -> str:
        return ""


@dataclass(frozen=True)
class Ambiguous:
    candidates: tuple
    reason: str

    @property
    def code(self) -> str:
        return NODE_AMBIGUOUS

    @property
    def candidate_ids(self) -> list:
        return [str(node.pk) for node in self.candidates]


@dataclass(frozen=True)
class Refused:
    code: str
    node: Node | None = None


@dataclass
class _World:
    """The owner's nodes for one resolution, with the per-node facts
    computed at most once (presence is a Redis call)."""

    owner: object
    labelled: list
    usable: list
    capabilities: frozenset
    now: object
    _online: dict = field(default_factory=dict)
    _offers: dict = field(default_factory=dict)

    def online(self, node) -> bool:
        if node.pk not in self._online:
            self._online[node.pk] = presence.is_online(node)
        return self._online[node.pk]

    def offers(self, node) -> bool:
        if node.pk not in self._offers:
            self._offers[node.pk] = bool(invocable_names(node) & self.capabilities)
        return self._offers[node.pk]

    def is_usable(self, node) -> bool:
        return any(other.pk == node.pk for other in self.usable)

    def eligible(self, node) -> bool:
        # Database facts first: no Redis call for a node that cannot serve.
        return self.is_usable(node) and self.offers(node) and self.online(node)

    def why_not(self, node) -> str:
        """The refusal naming `node` when it is not eligible."""
        if not self.is_usable(node):
            return NODE_UNUSABLE
        if not self.offers(node):
            return NODE_LACKS_CAPABILITY
        return NODE_OFFLINE


def is_usable(node, now=None) -> bool:
    """Not revoked, killed or expired (`accounts.devices.node_device_refusal`)."""
    return node_device_refusal(node.device, now or timezone.now()) is None


def invocable_names(node) -> frozenset:
    """Every capability name (read, confirm or gate class) the node's latest
    snapshot marks invocable."""
    snapshot = latest_snapshot(node)
    if snapshot is None:
        return frozenset()
    return frozenset(
        entry.get("name") for entry in snapshot.entries or () if entry.get("invocable")
    )


def _ordered(nodes):
    """Display order: room `sort_order`, room name, node name."""

    def key(node):
        room = node.room
        return (
            room is None,
            room.sort_order if room is not None else 0,
            room.name if room is not None else "",
            node.name,
            str(node.pk),
        )

    return sorted(nodes, key=key)


def _world(owner, capabilities, now):
    labelled, usable = [], []
    for node in nodes_for(owner):
        refusal = node_device_refusal(node.device, now)
        if refusal is None:
            usable.append(node)
            labelled.append(node)
        elif refusal == "kill_switch":
            labelled.append(node)
    return _World(
        owner=owner,
        labelled=labelled,
        usable=usable,
        capabilities=frozenset(capabilities),
        now=now,
    )


def _aliases(row) -> list:
    return [alias for alias in row.aliases or () if isinstance(alias, str)]


def _label_vocabulary(nodes):
    vocabulary = []
    for node in nodes:
        vocabulary.append((node.name, node))
        vocabulary.extend((alias, node) for alias in _aliases(node))
    return vocabulary


def _room_vocabulary(owner):
    """Every room of the owner's personal workspace (`for_actor()`), with or
    without a node: a room the owner names is never ignored."""
    rooms = Room.objects.for_actor(owner).filter(
        workspace__owner=owner, workspace__type=Workspace.Type.PERSONAL
    )
    vocabulary = []
    for room in rooms:
        vocabulary.append((room.name, room.pk))
        vocabulary.extend((alias, room.pk) for alias in _aliases(room))
    return vocabulary


def _word_vocabulary(table):
    return [(word, value) for value, words in table.items() for word in words]


def _decide_one(world, node, reason=REASON_MENTION):
    if world.eligible(node):
        return Resolved(node, reason)
    return Refused(world.why_not(node), node)


def _by_mention(world, text):
    """Rule 1. None when the owner's words name no PC."""
    if not text:
        return None
    named = []
    for mention in find_mentions(text, _label_vocabulary(world.labelled)):
        if all(node.pk != mention.target.pk for node in named):
            named.append(mention.target)
    if len(named) > 1:
        return Ambiguous(tuple(_ordered(named)), REASON_MENTION)
    if named:
        return _decide_one(world, named[0])

    candidates = list(world.labelled)
    qualified = False
    room_ids = {mention.target for mention in match_room(text, _room_vocabulary(world.owner))}
    if room_ids:
        qualified = True
        candidates = [node for node in candidates if node.room_id in room_ids]
        if not candidates:
            return Refused(NO_NODE_IN_ROOM)
    for table, attribute in ((ROLE_WORDS, "role"), (PLATFORM_WORDS, "platform")):
        values = {mention.target for mention in find_mentions(text, _word_vocabulary(table))}
        if not values:
            continue
        narrowed = [node for node in candidates if getattr(node, attribute) in values]
        if narrowed:
            qualified = True
            candidates = narrowed
    if not qualified:
        return None
    if len(candidates) == 1:
        return _decide_one(world, candidates[0])
    eligible = [node for node in candidates if world.eligible(node)]
    if len(eligible) == 1:
        return Resolved(eligible[0], REASON_MENTION)
    if not eligible:
        return Refused(NOT_AVAILABLE)
    return Ambiguous(tuple(_ordered(eligible)), REASON_MENTION)


def _by_default(world, origin_node_id):
    """Rules 2-4: the speaking node, the only or last-active candidate, ask."""
    eligible = [node for node in world.usable if world.eligible(node)]
    if origin_node_id is not None:
        origin = next((node for node in eligible if str(node.pk) == str(origin_node_id)), None)
        if origin is not None:
            return Resolved(origin, REASON_ORIGIN)
    if len(eligible) == 1:
        return Resolved(eligible[0], REASON_ONLY_CANDIDATE)
    if not eligible:
        if len(world.usable) == 1:
            # The owner's one PC: name it (its own chain row says why).
            return Refused(world.why_not(world.usable[0]), world.usable[0])
        return Refused(NOT_AVAILABLE)
    window = timedelta(seconds=int(settings.NODE_LAST_ACTIVE_WINDOW_SECONDS))
    recent = sorted(
        (node for node in eligible if node.last_active_at is not None),
        key=lambda node: node.last_active_at,
        reverse=True,
    )
    if recent and world.now - recent[0].last_active_at <= window:
        if len(recent) == 1 or recent[0].last_active_at > recent[1].last_active_at:
            return Resolved(recent[0], REASON_LAST_ACTIVE)
    return Ambiguous(tuple(_ordered(eligible)), REASON_ASK)


def _requested(world, requested_node_id, requested_label):
    """The labelled node the model named, or a `Refused(node_unknown)`."""
    wanted = []
    if requested_node_id is not None:
        node = next((n for n in world.labelled if str(n.pk) == str(requested_node_id)), None)
        if node is None:
            return Refused(NODE_UNKNOWN)
        wanted.append(node)
    if requested_label is not None:
        key = phrase_key(requested_label) if isinstance(requested_label, str) else ""
        matches = [
            node
            for node in world.labelled
            if key and key in {node.name_key, *(phrase_key(alias) for alias in _aliases(node))}
        ]
        if len(matches) != 1:
            return Refused(NODE_UNKNOWN)
        wanted.append(matches[0])
    if len({node.pk for node in wanted}) > 1:
        return Refused(TARGET_MISMATCH)
    return wanted[0] if wanted else None


def resolve_target(
    owner,
    *,
    capabilities,
    source_text="",
    origin_node_id=None,
    requested_node_id=None,
    requested_label=None,
    now=None,
):
    """Resolve the one node a call goes to (see the module docstring)."""
    if owner is None or getattr(owner, "pk", None) is None:
        return Refused(NOT_AVAILABLE)
    world = _world(owner, capabilities, now or timezone.now())
    requested = _requested(world, requested_node_id, requested_label)
    if isinstance(requested, Refused):
        return requested
    target = _by_mention(world, source_text if isinstance(source_text, str) else "")
    if target is None:
        target = _by_default(world, origin_node_id)
    decided = getattr(target, "node", None)
    if requested is not None and decided is not None and decided.pk != requested.pk:
        return Refused(TARGET_MISMATCH, decided)
    return target


def eligible_nodes(owner, *, capabilities, now=None) -> list:
    """The owner's eligible nodes for `capabilities` (D5 availability: a
    read tool is offered when at least one exists)."""
    if owner is None or getattr(owner, "pk", None) is None:
        return []
    world = _world(owner, capabilities, now or timezone.now())
    return [node for node in world.usable if world.eligible(node)]


# --- Friday-authored replies (Turkish; never node or model text) ------------------


def _question_particle(name) -> str:
    """Turkish vowel harmony of the question particle after `name`."""
    for char in reversed(name.lower()):
        if char in "aı":
            return "mı"
        if char in "ei":
            return "mi"
        if char in "ou":
            return "mu"
        if char in "öü":
            return "mü"
    return "mi"


def _label(node) -> str:
    name = " ".join(str(node.name).split())[:60]
    return name or "Adsız düğüm"


def ask_text(candidates) -> str:
    """The ask template: "Hangi bilgisayar: MacBook mu, Masaüstü mü?"."""
    names = [_label(node) for node in list(candidates)[:MAX_ASK_NAMES]]
    listed = ", ".join(f"{name} {_question_particle(name)}" for name in names)
    return (
        f"Hangi bilgisayar: {listed}? Bilgisayarın adını söyleyerek isteğini yeniden "
        "iletir misin? (Hiçbir bilgisayarda bir şey çalıştırmadım.)"
    )


REFUSAL_TEXTS = {
    NODE_OFFLINE: "{name} şu an çevrimdışı; başka bir bilgisayarda çalıştırmadım.",
    NODE_LACKS_CAPABILITY: "{name} bu işlemi sunmuyor; başka bir bilgisayarda çalıştırmadım.",
    NODE_UNUSABLE: (
        "{name} şu an kullanılamıyor (bağlantısı kesilmiş ya da kapatılmış); başka bir "
        "bilgisayarda çalıştırmadım."
    ),
    TARGET_MISMATCH: (
        "Söylediğin bilgisayarla önerilen bilgisayar uyuşmuyor; hiçbir bilgisayarda bir şey "
        "çalıştırmadım. Bilgisayarın adını söyleyerek yeniden iste."
    ),
    NODE_UNKNOWN: (
        "Bu adla eşleşen bir bilgisayarın yok; hiçbir bilgisayarda bir şey çalıştırmadım."
    ),
    NO_NODE_IN_ROOM: (
        "O odada bağlı bir bilgisayarın yok; hiçbir bilgisayarda bir şey çalıştırmadım."
    ),
    NOT_AVAILABLE: "Bunu yapabilecek çevrimiçi bir bilgisayarın yok.",
}


def reply_text(target) -> str:
    """The fixed reply of a target that is not `Resolved`."""
    if isinstance(target, Ambiguous):
        return ask_text(target.candidates)
    if isinstance(target, Refused):
        template = REFUSAL_TEXTS.get(target.code, REFUSAL_TEXTS[NOT_AVAILABLE])
        name = _label(target.node) if target.node is not None else "Bu bilgisayar"
        return template.format(name=name)
    return ""
