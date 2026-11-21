"""The protocol v1 message registry and validation (brief D5).

The message types are a closed, frozen set per direction. The server ->
node set is exactly `{hello.ack, heartbeat.ack, invoke, event.ack, bye}`:
there is no policy-mutation message type (ADR-013 decision 5), and a test
pins both the set and a policy-shaped-name regex over every type name and
schema property.

Schemas live in `schemas/v1/<type>.schema.json` (JSON Schema 2020-12,
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
"""

import json
import re
from functools import cache
from pathlib import Path
from types import MappingProxyType

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError

from nodes.protocol.canonical import CanonicalizationError, parse_strict, sha256_hex

PROTOCOL_VERSION = 1
SUBPROTOCOL = "friday-node.v1"
GATEWAY_PATH = "/ws/node/v1"

MAX_FRAME_BYTES = 256 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_EVENT_BATCH = 50
HEARTBEAT_SECONDS = 15
CLOCK_SKEW_SECONDS = 5
# The protocol ceiling for a read invoke's `expires_at - issued_at`; the
# server's NODE_READ_INVOKE_TTL_SECONDS must not exceed it. ADR-013's 120 s
# ceiling is for Phase 10 grants.
MAX_READ_INVOKE_TTL_SECONDS = 30
NONCE_CACHE_SIZE = 10_000

NODE_TO_SERVER = frozenset({"hello", "heartbeat", "result", "event"})
SERVER_TO_NODE = frozenset({"hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"})
DIRECTIONS = ("node_to_server", "server_to_node")
MESSAGE_TYPES = MappingProxyType(
    {"node_to_server": NODE_TO_SERVER, "server_to_node": SERVER_TO_NODE}
)

# Node-side verification order (D5), which is also the refusal-code order.
# Codes 1-4 are frame errors (an `error` result, counted towards the
# three-in-60-s close); the rest refuse one invoke.
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

CAPABILITY_CLASSES = ("read", "confirm", "gate")
AVAILABILITY = ("granted", "not_granted", "unsupported")
EVENT_KINDS = ("claude_code.hook", "grant.availability_changed")
RESULT_STATUSES = ("ok", "refused", "error", "timeout")
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
# Phase 10 approval-bound grant, null in v1 and refused by a v1 node.
POLICY_SHAPED = re.compile(
    r"(?i)policy|allowlist|grant(?!_id)|catalogue_set|config|exec_policy|settings"
)
RESERVED_POLICY_SHAPED_PROPERTIES = frozenset({("invoke", "grant")})

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "v1"
OPAQUE_KEYWORD = "x-friday-opaque"


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


StrictValidator = validators.extend(Draft202012Validator, {"pattern": _full_match_pattern})


def schema_path(message_type) -> Path:
    return SCHEMA_DIR / f"{message_type}.schema.json"


@cache
def _schema_text(message_type) -> str:
    return schema_path(message_type).read_text(encoding="utf-8")


def load_schema(message_type) -> dict:
    """A fresh copy of the schema of one message type."""
    if message_type not in NODE_TO_SERVER | SERVER_TO_NODE:
        raise ProtocolError("unknown_type")
    return json.loads(_schema_text(message_type))


@cache
def _validator_for_type(message_type):
    return StrictValidator(load_schema(message_type))


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
    strict semantics (full-match patterns, integers only). Raises
    ProtocolError(code) without echoing the instance."""
    if _has_float(instance):
        raise ProtocolError(code, "non-integer number")
    try:
        StrictValidator(schema).validate(instance)
    except ValidationError as exc:
        raise ProtocolError(code, "instance does not match the schema") from exc


def validate(message, direction) -> dict:
    """Validate one parsed message for `direction` (`node_to_server` or
    `server_to_node`). Returns the message; raises ProtocolError with
    `unknown_type` (D5 check 3) or `schema` (check 4)."""
    if direction not in MESSAGE_TYPES:
        raise ValueError(f"unknown direction {direction!r}")
    if not isinstance(message, dict):
        raise ProtocolError("schema", "a message is a JSON object")
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in MESSAGE_TYPES[direction]:
        raise ProtocolError("unknown_type")
    if _has_float(message):
        raise ProtocolError("schema", "non-integer number")
    try:
        _validator_for_type(message_type).validate(message)
    except ValidationError as exc:
        raise ProtocolError("schema", f"{message_type} does not match its schema") from exc
    return message


def decode_frame(raw, direction) -> dict:
    """D5 checks 1-4 for one received frame: size (`too_large`), strict
    JSON (`malformed`), known type (`unknown_type`), schema (`schema`)."""
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > MAX_FRAME_BYTES:
        raise ProtocolError("too_large")
    try:
        message = parse_strict(raw)
    except CanonicalizationError as exc:
        raise ProtocolError("malformed") from exc
    return validate(message, direction)


def catalogue_sha256(catalogue) -> str:
    """`hello.catalogue_sha256`: sha256 of the canonical JSON of the
    catalogue array, in the order the node sent it."""
    return sha256_hex(list(catalogue))
