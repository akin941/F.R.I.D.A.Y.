"""Claude Agent SDK driver: the bridge protocol schema, the SDK surface
snapshot and the pin (Phase 10 brief D10 "Pins and contract tests", branch 7).

- `claude_bridge_schema/v1.json`: Friday's own `friday-agent-bridge/v1`
  line protocol (host driver <-> `harness/docker/claude_agent_bridge.py`),
  JSON Schemas per frame type and direction. Friday owns it, so it is never
  provisional.
- `claude_sdk_surface/<version>/sdk_surface.json`: the names (classes,
  their fields and methods, functions) of the pinned `claude-agent-sdk`
  that the bridge may use, generated from the installed package by
  `harness/docker/generate_driver_schemas.sh claude <image>`. This branch
  commits a hand-written subset marked `"provisional": true`; the closeout
  preflight fails while it backs an enabled driver.

The pin `HARNESS_CLAUDE_AGENT_SDK_VERSION` must equal the Dockerfile
`ARG CLAUDE_AGENT_SDK_VERSION` and the surface snapshot's `version`
(`claude_pin_errors`, system check `harness.E004`); the bridge also reports
the installed SDK version in its `ready` frame and the driver refuses a
mismatch.
"""

import functools
import json
import re
from pathlib import Path

from django.conf import settings
from jsonschema import Draft7Validator

from harness.drivers.schema import dockerfile_arg

BRIDGE_PROTOCOL = "friday-agent-bridge/v1"
BRIDGE_SCHEMA_FORMAT = "friday-agent-bridge-schema/v1"
SURFACE_FORMAT = "friday-claude-agent-sdk-surface/v1"

_DRIVERS_DIR = Path(__file__).resolve().parent
BRIDGE_SCHEMA_PATH = _DRIVERS_DIR / "claude_bridge_schema" / "v1.json"
CLAUDE_SURFACE_ROOT = _DRIVERS_DIR / "claude_sdk_surface"
CLAUDE_DOCKERFILE = _DRIVERS_DIR.parent / "docker" / "claude_code.Dockerfile"
CLAUDE_SDK_VERSION_ARG = "CLAUDE_AGENT_SDK_VERSION"

HOST_TO_BRIDGE = "host_to_bridge"
BRIDGE_TO_HOST = "bridge_to_host"
DIRECTIONS = (HOST_TO_BRIDGE, BRIDGE_TO_HOST)

_VERSION_RE = re.compile(r"\A[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")


class BridgeSchema:
    """The loaded bridge protocol: per direction, frame type -> schema."""

    def __init__(self, document):
        if not isinstance(document, dict) or document.get("schema_format") != BRIDGE_SCHEMA_FORMAT:
            raise ValueError("not a bridge protocol schema")
        if document.get("protocol") != BRIDGE_PROTOCOL:
            raise ValueError("bridge schema for another protocol")
        self.document = document
        self.protocol = document["protocol"]
        self._definitions = document.get("definitions") or {}
        self._validators = {}

    def types(self, direction):
        return frozenset((self.document.get(direction) or {}).keys())

    def has(self, direction, frame_type):
        return frame_type in self.types(direction)

    def validator(self, direction, frame_type):
        key = (direction, frame_type)
        if key not in self._validators:
            schema = (self.document.get(direction) or {}).get(frame_type)
            self._validators[key] = (
                Draft7Validator({**schema, "definitions": self._definitions})
                if isinstance(schema, dict)
                else None
            )
        return self._validators[key]

    def validate(self, direction, frame):
        """True when `frame` is a known type of `direction` and matches its
        schema."""
        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            return False
        validator = self.validator(direction, frame["type"])
        return validator is not None and validator.is_valid(frame)


class SdkSurface:
    """A loaded SDK surface snapshot."""

    def __init__(self, document):
        if not isinstance(document, dict) or document.get("schema_format") != SURFACE_FORMAT:
            raise ValueError("not an SDK surface snapshot")
        self.document = document
        self.version = str(document.get("version", ""))
        self.provisional = document.get("provisional") is not False
        self.classes = document.get("classes") or {}
        self.functions = frozenset(document.get("functions") or ())

    def missing(self, used):
        """The names in `used` (`{class: {"fields": [...], "methods": [...]}}`)
        that the snapshot does not have, as "Class" / "Class.member"."""
        problems = []
        for name, members in used.items():
            entry = self.classes.get(name)
            if entry is None:
                problems.append(name)
                continue
            for part in ("fields", "methods"):
                known = set(entry.get(part) or ())
                problems.extend(
                    f"{name}.{member}" for member in members.get(part, ()) if member not in known
                )
        return problems


@functools.lru_cache(maxsize=1)
def load_bridge_schema():
    with open(BRIDGE_SCHEMA_PATH, encoding="utf-8") as handle:
        return BridgeSchema(json.load(handle))


def _surface_path(version):
    version = str(version or "")
    if not _VERSION_RE.match(version):
        raise ValueError(f"invalid driver pin {version!r}")
    return CLAUDE_SURFACE_ROOT / version / "sdk_surface.json"


@functools.lru_cache(maxsize=8)
def load_claude_surface(version):
    """The SDK surface snapshot for `version`. Raises OSError (no such
    snapshot) or ValueError (malformed)."""
    with open(_surface_path(version), encoding="utf-8") as handle:
        return SdkSurface(json.load(handle))


def claude_pin_errors(*, pin=None, dockerfile=CLAUDE_DOCKERFILE):
    """Problems with the Agent SDK pin (empty = the pin, the Dockerfile ARG
    and the snapshot version agree)."""
    pin = str(pin if pin is not None else settings.HARNESS_CLAUDE_AGENT_SDK_VERSION)
    errors = []
    arg = dockerfile_arg(dockerfile, CLAUDE_SDK_VERSION_ARG)
    if arg is None:
        errors.append(f"{Path(dockerfile).name} declares no ARG {CLAUDE_SDK_VERSION_ARG}.")
    elif arg != pin:
        errors.append(
            f"HARNESS_CLAUDE_AGENT_SDK_VERSION={pin!r} but {Path(dockerfile).name} pins "
            f"{CLAUDE_SDK_VERSION_ARG}={arg!r}."
        )
    try:
        surface = load_claude_surface(pin)
    except (OSError, ValueError):
        errors.append(f"No Claude Agent SDK surface snapshot for {pin!r} under claude_sdk_surface/.")
    else:
        if surface.version != pin:
            errors.append(
                f"claude_sdk_surface/{pin}/sdk_surface.json records version "
                f"{surface.version!r}, not {pin!r}."
            )
    return errors
