"""`friday-voice/v1` message sets, schemas and refusal codes (brief D6).

The two direction sets are **frozen**: adding a type is a protocol version
bump reviewed like a node protocol change. Nothing the child can send
changes the host's mode, hotkey, mute, device, credential or node policy,
opens a URL, runs anything or sends anything on the node WebSocket other
than the `voice.state` event the host derives from `state`; a meta-test
applies the node protocol's `POLICY_SHAPED` regex to every name.

Schemas live next to this module in `schemas/v1/<direction>/<type>.schema.json`
(the files the Swift host's test target loads). Validation is strict:
`pattern` is a full match, numbers are integers only, unknown properties are
refused, and a refusal never echoes the offending value.
"""

import json
import re
from functools import cache
from pathlib import Path
from types import MappingProxyType

from jsonschema import Draft202012Validator, ValidationError, validators

PROTOCOL = "friday-voice/v1"
SAMPLE_RATE = 16000

HOST_TO_CHILD = "host_to_child"
CHILD_TO_HOST = "child_to_host"
DIRECTIONS = (HOST_TO_CHILD, CHILD_TO_HOST)

HOST_TO_CHILD_TYPES = frozenset(
    {"hello", "credential", "audio", "playback", "mode", "mute", "ptt", "link", "shutdown"}
)
CHILD_TO_HOST_TYPES = frozenset(
    {"ready", "state", "play", "stop_playback", "earcon", "release_mic", "log"}
)
MESSAGE_TYPES = MappingProxyType(
    {HOST_TO_CHILD: HOST_TO_CHILD_TYPES, CHILD_TO_HOST: CHILD_TO_HOST_TYPES}
)
# Frames that carry a PCM payload after the header line, and their direction.
AUDIO_TYPES = MappingProxyType({"audio": HOST_TO_CHILD, "play": CHILD_TO_HOST})

MODES = ("off", "push_to_talk", "always_on")
STATES = (
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
RELEASE_REASONS = (
    "link_down",
    "muted",
    "mute_unreadable",
    "mode_off",
    "credential_missing",
    "credential_expired",
    "credential_refused",
    "protocol_error",
    "stdin_closed",
    "storm",
    "shutdown",
    "error",
)
EARCONS = ("wake", "end", "error")

# Byte limits (D6).
MAX_FRAME_BYTES = 1024 * 1024
MAX_PCM_BYTES = 64 * 1024
MAX_HEADER_BYTES = 16 * 1024

# Refusal codes. Framing and schema codes are shared by both ends; the
# session codes are raised by the host or child state machines.
FRAMING_ERRORS = frozenset(
    {
        "frame_empty",
        "frame_too_large",
        "short_read",
        "header_too_large",
        "header_unterminated",
        "invalid_utf8",
        "invalid_json",
        "duplicate_key",
        "number_not_integer",
        "header_not_object",
        "payload_length_mismatch",
        "payload_unexpected",
        "pcm_odd_length",
        "pcm_too_large",
    }
)
SCHEMA_ERRORS = frozenset({"unknown_type", "schema_invalid"})
SESSION_ERRORS = frozenset(
    {
        "hello_expected",
        "ready_expected",
        "duplicate_hello",
        "duplicate_ready",
        "version_unsupported",
    }
)
ERROR_CODES = FRAMING_ERRORS | SCHEMA_ERRORS | SESSION_ERRORS

# Copied verbatim from backend/nodes/protocol/messages.py (this package may
# import nothing from backend/); tests/test_schemas.py asserts the two
# literals are identical. No reserved exception in friday-voice/v1.
POLICY_SHAPED = re.compile(
    r"(?i)policy|allowlist|grant(?!_id)|catalogue[._]set|config|exec_policy|settings"
)

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "v1"
VECTORS_PATH = Path(__file__).resolve().parent / "vectors" / "frames.json"


class ProtocolError(ValueError):
    """A frame or message failed a protocol check. `code` is one of
    ERROR_CODES; the message never contains the offending bytes."""

    def __init__(self, code, message=""):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown protocol error code {code!r}")
        super().__init__(message or code)
        self.code = code


def _full_match_pattern(validator, pattern, instance, schema):
    if validator.is_type(instance, "string") and re.fullmatch(pattern, instance) is None:
        yield ValidationError("string does not match the pattern")


StrictValidator = validators.extend(Draft202012Validator, {"pattern": _full_match_pattern})


def direction_of(message_type) -> str:
    for direction in DIRECTIONS:
        if message_type in MESSAGE_TYPES[direction]:
            return direction
    raise ProtocolError("unknown_type")


def schema_path(message_type) -> Path:
    return SCHEMA_DIR / direction_of(message_type) / f"{message_type}.schema.json"


@cache
def _schema_text(message_type) -> str:
    return schema_path(message_type).read_text(encoding="utf-8")


def load_schema(message_type) -> dict:
    """A fresh copy of one message type's schema."""
    return json.loads(_schema_text(message_type))


@cache
def _validator(message_type):
    return StrictValidator(load_schema(message_type))


def _has_float(value) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_float(item) for item in value)
    return False


def validate(header, direction) -> dict:
    """Validate one decoded header for the direction it travelled in.
    Raises ProtocolError(`unknown_type` | `schema_invalid`)."""
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    if not isinstance(header, dict):
        raise ProtocolError("header_not_object")
    message_type = header.get("type")
    if not isinstance(message_type, str) or message_type not in MESSAGE_TYPES[direction]:
        raise ProtocolError("unknown_type")
    if message_type in ("hello", "ready") and header.get("protocol") != PROTOCOL:
        raise ProtocolError("version_unsupported")
    if _has_float(header):
        raise ProtocolError("number_not_integer")
    try:
        _validator(message_type).validate(header)
    except ValidationError as exc:
        raise ProtocolError("schema_invalid", f"{message_type} does not match its schema") from exc
    return header


def message(message_type, **fields) -> dict:
    """Build a header dict (no validation; `encode_frame` validates)."""
    return {"type": message_type, **fields}
