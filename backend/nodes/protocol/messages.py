"""The protocol message registry and validation (Phase 9 brief D5; Phase 10
brief D1 for v2).

The message types are a closed, frozen set per version and direction. The
server -> node set is exactly `{hello.ack, heartbeat.ack, invoke,
event.ack, bye}` in **both** v1 and v2: there is no policy-mutation message
type (ADR-013 decision 5) and no server-side cancel (Phase 10 D1). v2 adds
exactly two node -> server types, `confirmation` and `output`. A test pins
the sets and a policy-shaped-name regex over every type name and schema
property of both versions.

`MESSAGE_TYPES[version][direction]` is the registry; `validate(message,
direction, version=1)` checks one message against the schema of that
version. A session negotiates its version once (`negotiate_subprotocol`)
and every frame of the session must carry that `v` (each schema pins it).

Schemas live in `schemas/v<N>/<type>.schema.json` (JSON Schema 2020-12,
`additionalProperties: false` everywhere, every string, array and integer
bounded; a meta-test walks them). They are the single source of truth and
are loaded from disk here.

Two deliberate deviations from plain JSON Schema semantics, both stricter,
which the Swift port must mirror:
- `pattern` is a **full match** of the whole string (a trailing newline
  never slips past a `$` anchor);
- numbers must be integers: a float is refused even where JSON Schema
  would call `1.0` an integer.

A sub-schema carrying `"x-friday-opaque"` is a typed hole: its content is
validated elsewhere (a capability's own `args_schema` / `result_schema`,
or an event sink), and the frame-size limit bounds it.

v2 adds one keyword, `"x-friday-max-bytes": N`: the UTF-8 encoding of the
string is at most N bytes (`maxLength` counts code points, so a 4 096
character tail could otherwise be 16 KiB). The Swift port mirrors it.

Phase 11 (D15) uses three more standard keywords in exactly one place, the
v2 `event` schema: a local `$ref` into its own `$defs` and one
`allOf: [{if: {kind == "voice.state"}, then: {batch: one voice item}}]`.
The opaque per-kind hole stays for the other kinds; the Swift port must
apply the conditional (its validator supports `if`/`then` and `$ref`, or
it validates `$defs.voice_state_item` itself before sending).
"""

import json
import re
from functools import cache
from pathlib import Path
from types import MappingProxyType

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError

from nodes.protocol.canonical import CanonicalizationError, parse_strict, sha256_hex

# The pairing baseline (the pairing response's `protocol_version`) stays
# v1: the session version is negotiated per WebSocket (Phase 10 D1), not
# at pairing.
PROTOCOL_VERSION = 1
PROTOCOL_VERSIONS = (1, 2)
LATEST_PROTOCOL_VERSION = 2
SUBPROTOCOL = "friday-node.v1"
SUBPROTOCOLS = MappingProxyType({1: "friday-node.v1", 2: "friday-node.v2"})
# The path keeps its v1 name in v2: renaming it would strand paired nodes.
GATEWAY_PATH = "/ws/node/v1"

MAX_FRAME_BYTES = 256 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_EVENT_BATCH = 50
HEARTBEAT_SECONDS = 15
CLOCK_SKEW_SECONDS = 5
# The protocol ceiling for a read invoke's `expires_at - issued_at`; the
# server's NODE_READ_INVOKE_TTL_SECONDS must not exceed it.
MAX_READ_INVOKE_TTL_SECONDS = 30
# Phase 10 (ADR-013 decision 4, brief D2): the protocol ceiling of a grant's
# (and so of a gated envelope's) `expires_at - issued_at`. The server's
# NODE_GRANT_TTL_SECONDS (default 90) is refused at boot above it.
MAX_GRANT_TTL_SECONDS = 120
# A confirm-class envelope (D8, 90 s by default) has the same ceiling: it
# must outlive the 60 s local confirmation window plus delivery.
MAX_CONFIRM_INVOKE_TTL_SECONDS = 120
# D5 check 10 in v2: the TTL ceiling depends on the envelope's class.
MAX_INVOKE_TTL_SECONDS_V2 = MappingProxyType(
    {
        "read": MAX_READ_INVOKE_TTL_SECONDS,
        "confirm": MAX_CONFIRM_INVOKE_TTL_SECONDS,
        "gate": MAX_GRANT_TTL_SECONDS,
    }
)
CONFIRM_WINDOW_SECONDS = 60
NONCE_CACHE_SIZE = 10_000
# v2 bounds (D1): a result's scrubbed tail, one output chunk, the seq range.
MAX_TAIL_BYTES = 4096
MAX_OUTPUT_CHUNK_BYTES = 8192
MAX_OUTPUT_SEQ = 65_535

NODE_TO_SERVER = frozenset({"hello", "heartbeat", "result", "event"})
SERVER_TO_NODE = frozenset({"hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"})
# v2 adds exactly two node -> server types; the server -> node set is the
# same frozenset object as v1 (D1: byte-identical, no cancel).
NODE_TO_SERVER_V2 = frozenset(NODE_TO_SERVER | {"confirmation", "output"})
SERVER_TO_NODE_V2 = SERVER_TO_NODE
DIRECTIONS = ("node_to_server", "server_to_node")
MESSAGE_TYPES = MappingProxyType(
    {
        1: MappingProxyType({"node_to_server": NODE_TO_SERVER, "server_to_node": SERVER_TO_NODE}),
        2: MappingProxyType(
            {"node_to_server": NODE_TO_SERVER_V2, "server_to_node": SERVER_TO_NODE_V2}
        ),
    }
)

# Node-side verification order v1 (Phase 9 D5), which is also the
# refusal-code order. Codes 1-4 are frame errors (an `error` result,
# counted towards the three-in-60-s close); the rest refuse one invoke.
NODE_REFUSAL_CODES = (
    "too_large",
    "malformed",
    "unknown_type",
    "schema",
    "unknown_key",
    "bad_signature",
    "wrong_node",
    "not_yet_valid",
    "expired",
    "ttl_too_long",
    "replay",
    "nonce_cache_full",
    "unknown_capability",
    "class_not_allowed",
    "args_hash_mismatch",
    "args_schema",
    "not_granted",
)
FRAME_ERROR_CODES = NODE_REFUSAL_CODES[:4]

# The v2 node verification order (Phase 10 brief D5): v1 checks 1-12 kept
# verbatim, then 13-24. Each entry is (check number, codes). Checks 15 and
# 18 reuse the v1 codes `unknown_key` and `ttl_too_long` for the grant.
# Checks 1-21 are protocol-level (`nodes.protocol.verify`); 22-24 need the
# node's local policy and its confirmation window (the reference node,
# Phase 10 branch 2, and the Swift app, Mac session M1). A lost socket at
# check 24 sends nothing at all.
NODE_CHECKS_V2 = (
    (1, ("too_large",)),
    (2, ("malformed",)),
    (3, ("unknown_type",)),
    (4, ("schema",)),
    (5, ("unknown_key",)),
    (6, ("bad_signature",)),
    (7, ("wrong_node",)),
    (8, ("not_yet_valid",)),
    (9, ("expired",)),
    (10, ("ttl_too_long",)),
    (11, ("replay", "nonce_cache_full")),
    (12, ("unknown_capability",)),
    (13, ("class_mismatch",)),
    (14, ("grant_unexpected", "grant_missing")),
    (15, ("unknown_key",)),
    (16, ("bad_grant_signature",)),
    (17, ("grant_mismatch",)),
    (18, ("ttl_too_long",)),
    (19, ("args_hash_mismatch",)),
    (20, ("args_schema",)),
    (21, ("not_granted",)),
    (22, ("not_configured",)),
    (23, ("busy_confirming", "owner_denied", "confirm_timeout")),
    (24, ("expired_after_confirm",)),
)
# Every v2 refusal code once, in first-appearance order (the v2 `result`
# schema's enum). `class_not_allowed` is v1 only: a v2 node answers
# `class_mismatch` / `grant_unexpected` / `grant_missing` instead.
NODE_REFUSAL_CODES_V2 = tuple(dict.fromkeys(code for _, codes in NODE_CHECKS_V2 for code in codes))
NODE_REFUSAL_CODES_BY_VERSION = MappingProxyType({1: NODE_REFUSAL_CODES, 2: NODE_REFUSAL_CODES_V2})

CAPABILITY_CLASSES = ("read", "confirm", "gate")
AVAILABILITY = ("granted", "not_granted", "unsupported")
EVENT_KINDS = ("claude_code.hook", "grant.availability_changed")
# Phase 11 D15: the node -> server `voice.state` kind, **v2 only** (the v1
# event schema keeps its two kinds, so a v1 `voice.state` is a schema error,
# a frame error). One item per frame, every field bounded and enumerated:
# the mic state the voice child reports (`friday-voice/v1` `state`), the
# node's local mode, the session flag, three daily counters and `at` (the
# node clock, Unix seconds). No audio, no text. The v2 event schema carries
# the per-kind item schema (`$defs.voice_state_item`, applied by an
# `allOf`/`if`/`then` on `kind`), so a bad item is a schema error.
VOICE_STATE_KIND = "voice.state"
EVENT_KINDS_BY_VERSION = MappingProxyType({1: EVENT_KINDS, 2: (*EVENT_KINDS, VOICE_STATE_KIND)})
VOICE_STATES = (
    "off",
    "armed_ptt",
    "armed_wake",
    "listening",
    "thinking",
    "speaking",
    "muted",
    "storm_muted",
    "error",
)
VOICE_MODES = ("off", "push_to_talk", "always_on")
VOICE_STATE_COUNTERS = ("wake_accepts_today", "wake_discards_today", "storm_mutes_today")
MAX_VOICE_COUNTER = 100_000
MAX_VOICE_STATE_AT = 4_102_444_800  # 2100-01-01T00:00:00Z
VOICE_STATE_ITEM_KEYS = frozenset({"state", "mode", "session_open", *VOICE_STATE_COUNTERS, "at"})
# The node relays at most one `voice.state` per second (coalesced: the
# latest state wins) and re-sends its current state with every heartbeat;
# the server writes at most one per node per second. A stored state older
# than two heartbeats means the node stopped reporting (`unknown`).
VOICE_STATE_MIN_INTERVAL_SECONDS = 1
VOICE_STATE_STALE_SECONDS = 2 * HEARTBEAT_SECONDS
RESULT_STATUSES = ("ok", "refused", "error", "timeout")
# v2 (D1): the states a `confirmation` frame reports, and a result's
# `confirm_state` (which adds `not_required` for read invokes).
CONFIRMATION_STATES = ("shown", "approved", "denied", "timed_out", "closed")
RESULT_CONFIRM_STATES = ("approved", "denied", "timed_out", "closed", "not_required")
OUTPUT_STREAMS = ("stdout", "stderr")
BYE_REASONS = (
    "revoked",
    "kill_switch",
    "credential_expired",
    "replaced",
    "protocol_error",
    "server_shutdown",
)

# The policy-mutation guard (D5): no type name or schema property may look
# like a policy surface. `invoke.grant` is the one reserved exception: the
# Phase 10 approval-bound grant, null in v1 and refused by a v1 node. The
# brief's `catalogue_set` is widened to `catalogue[._]set` so the dotted
# type name of the conformance case (`catalogue.set`) is caught too. The
# grant object's own properties (`grant_id`, `approval_id`, ...) pass the
# regex as written (`grant(?!_id)`).
POLICY_SHAPED = re.compile(
    r"(?i)policy|allowlist|grant(?!_id)|catalogue[._]set|config|exec_policy|settings"
)
RESERVED_POLICY_SHAPED_PROPERTIES = frozenset({("invoke", "grant")})

SCHEMAS_ROOT = Path(__file__).resolve().parent / "schemas"
SCHEMA_DIR = SCHEMAS_ROOT / "v1"
SCHEMA_DIRS = MappingProxyType({version: SCHEMAS_ROOT / f"v{version}" for version in MESSAGE_TYPES})
OPAQUE_KEYWORD = "x-friday-opaque"
MAX_BYTES_KEYWORD = "x-friday-max-bytes"
# Sub-schema files that are not message types. v2: the grant object, which
# `invoke.grant` embeds verbatim (a test pins the equality).
SUB_SCHEMAS = MappingProxyType({1: frozenset(), 2: frozenset({"grant"})})


class ProtocolError(ValueError):
    """A frame or value failed a protocol check. `code` is a D5 refusal
    code (`too_large`, `malformed`, `unknown_type`, `schema`, ...). The
    message never contains the offending payload."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _full_match_pattern(validator, pattern, instance, schema):
    if validator.is_type(instance, "string") and re.fullmatch(pattern, instance) is None:
        yield ValidationError("string does not match the pattern")


def _max_bytes(validator, limit, instance, schema):
    if validator.is_type(instance, "string"):
        try:
            size = len(instance.encode("utf-8"))
        except UnicodeEncodeError:  # a lone surrogate has no UTF-8 encoding
            yield ValidationError("string is not UTF-8 encodable")
            return
        if size > limit:
            yield ValidationError("string is too long in UTF-8 bytes")


StrictValidator = validators.extend(
    Draft202012Validator,
    {"pattern": _full_match_pattern, MAX_BYTES_KEYWORD: _max_bytes},
)


def _check_version(version):
    if version not in MESSAGE_TYPES:
        raise ValueError(f"unknown protocol version {version!r}")


def all_types(version=1) -> frozenset:
    """Every message type of one protocol version, both directions."""
    _check_version(version)
    return MESSAGE_TYPES[version]["node_to_server"] | MESSAGE_TYPES[version]["server_to_node"]


def direction_of(message_type, version=1) -> str:
    """The direction a message type travels in (`node_to_server` /
    `server_to_node`); raises ProtocolError("unknown_type")."""
    _check_version(version)
    for direction in DIRECTIONS:
        if message_type in MESSAGE_TYPES[version][direction]:
            return direction
    raise ProtocolError("unknown_type")


def schema_path(message_type, version=1) -> Path:
    _check_version(version)
    return SCHEMA_DIRS[version] / f"{message_type}.schema.json"


@cache
def _schema_text(message_type, version=1) -> str:
    return schema_path(message_type, version).read_text(encoding="utf-8")


def load_schema(message_type, version=1) -> dict:
    """A fresh copy of the schema of one message type (or v2 sub-schema,
    `grant`) of one protocol version."""
    _check_version(version)
    if message_type not in all_types(version) | SUB_SCHEMAS[version]:
        raise ProtocolError("unknown_type")
    return json.loads(_schema_text(message_type, version))


@cache
def _validator_for_type(message_type, version=1):
    return StrictValidator(load_schema(message_type, version))


def _has_float(value) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, list):
        return any(_has_float(item) for item in value)
    if isinstance(value, dict):
        return any(_has_float(item) for item in value.values())
    return False


def validate_instance(schema, instance, *, code="schema") -> None:
    """Validate `instance` against an arbitrary schema with the protocol's
    strict semantics (full-match patterns, integers only, byte bounds).
    Raises ProtocolError(code) without echoing the instance."""
    if _has_float(instance):
        raise ProtocolError(code, "non-integer number")
    try:
        StrictValidator(schema).validate(instance)
    except ValidationError as exc:
        raise ProtocolError(code, "instance does not match the schema") from exc


def validate_sub_schema(name, instance, version=2, *, code="schema") -> None:
    """Validate a sub-schema instance (v2: `grant`)."""
    _check_version(version)
    if name not in SUB_SCHEMAS[version]:
        raise ValueError(f"unknown sub-schema {name!r}")
    if _has_float(instance):
        raise ProtocolError(code, "non-integer number")
    try:
        _validator_for_type(name, version).validate(instance)
    except ValidationError as exc:
        raise ProtocolError(code, f"{name} does not match its schema") from exc


def validate(message, direction, version=1) -> dict:
    """Validate one parsed message for `direction` (`node_to_server` or
    `server_to_node`) in protocol `version` (the session's negotiated
    version; every schema pins `v` to it). Returns the message; raises
    ProtocolError with `unknown_type` (D5 check 3) or `schema` (check 4)."""
    _check_version(version)
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    if not isinstance(message, dict):
        raise ProtocolError("schema", "a message is a JSON object")
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in MESSAGE_TYPES[version][direction]:
        raise ProtocolError("unknown_type")
    if _has_float(message):
        raise ProtocolError("schema", "non-integer number")
    try:
        _validator_for_type(message_type, version).validate(message)
    except ValidationError as exc:
        raise ProtocolError("schema", f"{message_type} does not match its schema") from exc
    return message


def decode_frame(raw, direction, version=1) -> dict:
    """D5 checks 1-4 for one received frame: size (`too_large`), strict
    JSON (`malformed`), known type (`unknown_type`), schema (`schema`)."""
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > MAX_FRAME_BYTES:
        raise ProtocolError("too_large")
    try:
        message = parse_strict(raw)
    except CanonicalizationError as exc:
        raise ProtocolError("malformed") from exc
    return validate(message, direction, version)


def negotiate_subprotocol(offered, supported=PROTOCOL_VERSIONS):
    """D1 negotiation: the node offers WebSocket subprotocols (for example
    `["friday-node.v2", "friday-node.v1"]`); the gateway picks the highest
    version both sides support. Returns `(version, subprotocol)`, or None
    when nothing matches (the gateway closes with 4400). Unknown offers are
    ignored."""
    by_name = {name: version for version, name in SUBPROTOCOLS.items()}
    versions = {
        by_name[name]
        for name in offered or ()
        if isinstance(name, str) and name in by_name and by_name[name] in supported
    }
    if not versions:
        return None
    version = max(versions)
    return version, SUBPROTOCOLS[version]


def catalogue_sha256(catalogue) -> str:
    """`hello.catalogue_sha256`: sha256 of the canonical JSON of the
    catalogue array, in the order the node sent it."""
    return sha256_hex(list(catalogue))
