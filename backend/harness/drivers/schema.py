"""Driver protocol snapshots and pins (Phase 10 brief D10 "Pins and contract
tests").

Method names and payload shapes come from a schema snapshot of the pinned
version, never from memory: `codex_schema/<version>/protocol.json`, produced
from the installed binary by `harness/docker/generate_driver_schemas.sh`.
This branch commits a hand-written subset marked `"provisional": true`
(the container cannot run codex here); the closeout preflight fails while
a provisional snapshot backs an enabled driver.

The pin `HARNESS_CODEX_APP_SERVER_VERSION` must equal the Dockerfile
`ARG CODEX_CLI_VERSION` and the snapshot's recorded `version`
(`codex_pin_errors`, system check `harness.E004`).
"""

import functools
import json
import re
from pathlib import Path

from django.conf import settings
from jsonschema import Draft7Validator

SNAPSHOT_FORMAT = "friday-codex-app-server-subset/v1"
CODEX_SCHEMA_ROOT = Path(__file__).resolve().parent / "codex_schema"
CODEX_DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "codex_cli.Dockerfile"
CODEX_VERSION_ARG = "CODEX_CLI_VERSION"

# The four message classes of a snapshot.
CLIENT_REQUESTS = "client_requests"
CLIENT_NOTIFICATIONS = "client_notifications"
SERVER_REQUESTS = "server_requests"
SERVER_NOTIFICATIONS = "server_notifications"
MESSAGE_CLASSES = (CLIENT_REQUESTS, CLIENT_NOTIFICATIONS, SERVER_REQUESTS, SERVER_NOTIFICATIONS)

_VERSION_RE = re.compile(r"\A[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")


class ProtocolSnapshot:
    """A loaded snapshot: per message class, method -> {params, result}
    JSON Schemas, plus shared `definitions`."""

    def __init__(self, document):
        if not isinstance(document, dict) or document.get("schema_format") != SNAPSHOT_FORMAT:
            raise ValueError("not a driver protocol snapshot")
        self.document = document
        self.version = str(document.get("version", ""))
        self.provisional = document.get("provisional") is not False
        self._definitions = document.get("definitions") or {}
        self._validators = {}

    def methods(self, message_class):
        return frozenset((self.document.get(message_class) or {}).keys())

    def has(self, message_class, method):
        return method in self.methods(message_class)

    def validator(self, message_class, method, part):
        """A validator for `part` ("params" or "result") of `method`, or
        None when the snapshot states no schema for it."""
        key = (message_class, method, part)
        if key not in self._validators:
            entry = (self.document.get(message_class) or {}).get(method) or {}
            schema = entry.get(part)
            self._validators[key] = (
                Draft7Validator({**schema, "definitions": self._definitions})
                if isinstance(schema, dict)
                else None
            )
        return self._validators[key]

    def validate(self, message_class, method, part, value):
        """True when `value` matches the stated schema (or none is stated)."""
        validator = self.validator(message_class, method, part)
        return validator is None or validator.is_valid(value)


def _snapshot_path(version):
    version = str(version or "")
    if not _VERSION_RE.match(version):
        raise ValueError(f"invalid driver pin {version!r}")
    return CODEX_SCHEMA_ROOT / version / "protocol.json"


@functools.lru_cache(maxsize=8)
def load_codex_snapshot(version):
    """The Codex app-server snapshot for `version`. Raises OSError (no such
    snapshot) or ValueError (malformed)."""
    with open(_snapshot_path(version), encoding="utf-8") as handle:
        return ProtocolSnapshot(json.load(handle))


def dockerfile_arg(path, name):
    """The default value of `ARG <name>=<value>` in a Dockerfile, or None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(rf"^\s*ARG\s+{re.escape(name)}=(\S+)\s*$", text, re.MULTILINE)
    return match.group(1).strip("\"'") if match else None


def codex_pin_errors(*, pin=None, dockerfile=CODEX_DOCKERFILE):
    """Problems with the Codex app-server pin (empty = the pin, the
    Dockerfile ARG and the snapshot version agree)."""
    pin = str(pin if pin is not None else settings.HARNESS_CODEX_APP_SERVER_VERSION)
    errors = []
    arg = dockerfile_arg(dockerfile, CODEX_VERSION_ARG)
    if arg is None:
        errors.append(f"{Path(dockerfile).name} declares no ARG {CODEX_VERSION_ARG}.")
    elif arg != pin:
        errors.append(
            f"HARNESS_CODEX_APP_SERVER_VERSION={pin!r} but {Path(dockerfile).name} pins "
            f"{CODEX_VERSION_ARG}={arg!r}."
        )
    try:
        snapshot = load_codex_snapshot(pin)
    except (OSError, ValueError):
        errors.append(f"No Codex app-server schema snapshot for {pin!r} under codex_schema/.")
    else:
        if snapshot.version != pin:
            errors.append(
                f"codex_schema/{pin}/protocol.json records version {snapshot.version!r}, "
                f"not {pin!r}."
            )
    return errors
