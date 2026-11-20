"""`friday-hook` -- the Claude Code hook relay (Phase 8A brief D6).

Claude Code runs this as a **command hook** for `SessionStart`,
`Notification`, `Stop` and `SessionEnd`, with the hook's JSON on stdin. It
keeps an allow-listed subset of fields, scrubs the free text with the same
rules the server uses (a vendored copy of `memory/ingestion/scrubber.py`,
checked identical in CI), and posts one event to Friday's append-only
ingest endpoint with the `hook` PAT. Setup: docs/runbooks/claude-code-hooks.md.

It **never blocks or disturbs Claude Code**:

- it always exits 0 and prints nothing to stdout or stderr (a `SessionStart`
  hook's stdout would be added to Claude's context);
- the whole run is bounded by a ~3 s deadline (Keychain read + POST);
- on any error it appends one line to a local log (mode 0600) naming the
  error class only -- never the token, never the payload -- and exits 0.

No spool in 8A (the node's bounded spool is Phase 9, ADR-013): a lost event
only makes the sessions panel stale.

Standalone, standard library only -- no Django import (a test asserts it).
Run it from a checkout (`python3 -m integrations.cli.friday_hook`) or copy
this file and `_scrubber_vendored.py` side by side and run
`python3 friday_hook.py`.

Config:
    - base URL: FRIDAY_HOOK_URL, else `[hook] base_url` in
      ~/.config/friday/hook.toml;
    - token: the macOS Keychain generic password with service
      `friday-hook` (`security find-generic-password -s friday-hook -w`);
      FRIDAY_HOOK_TOKEN only where no Keychain exists.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

INGEST_PATH = "/api/v1/memory/ingest/hook-events"
ALLOWED_EVENTS = frozenset({"SessionStart", "Notification", "Stop", "SessionEnd"})
# The only fields that ever leave the machine. `transcript_path`, prompts,
# tool inputs and everything else Claude Code sends are dropped here.
STRING_FIELDS = ("hook_event_name", "session_id", "cwd", "notification_type", "source", "reason")
MESSAGE_FIELD = "message"
MAX_FIELD_CHARS = 1000
MAX_MESSAGE_CHARS = 200
MAX_STDIN_BYTES = 256 * 1024

KEYCHAIN_SERVICE = "friday-hook"
DEADLINE_SECONDS = 3.0
KEYCHAIN_TIMEOUT_SECONDS = 1.0
MIN_POST_TIMEOUT_SECONDS = 0.5

CONFIG_PATH = Path.home() / ".config" / "friday" / "hook.toml"
LOG_PATH = Path.home() / ".local" / "state" / "friday" / "friday-hook.log"


class HookError(Exception):
    """A configuration problem (no URL, no token): logged, never raised out."""


def _load_scrubber():
    try:
        from integrations.cli import _scrubber_vendored

        return _scrubber_vendored
    except ImportError:
        path = Path(__file__).resolve().with_name("_scrubber_vendored.py")
        spec = importlib.util.spec_from_file_location("_friday_hook_scrubber", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def scrub(text):
    return _load_scrubber().scrub_text(text).text


def build_event(payload, *, now=None, event_id=None):
    """The allow-listed, scrubbed event to send, or None when the hook event
    is not one Friday ingests (or the payload is not a JSON object)."""
    if not isinstance(payload, dict):
        return None
    if payload.get("hook_event_name") not in ALLOWED_EVENTS:
        return None
    event = {}
    for key in STRING_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            event[key] = value[:MAX_FIELD_CHARS]
    message = payload.get(MESSAGE_FIELD)
    if isinstance(message, str) and message:
        # Scrub first, then cut: a cut never leaves half a secret behind.
        event[MESSAGE_FIELD] = scrub(message[: MAX_FIELD_CHARS * 4])[:MAX_MESSAGE_CHARS]
    event["occurred_at"] = (now or datetime.now(UTC)).isoformat()
    event["event_id"] = event_id or uuid.uuid4().hex
    return event


def base_url():
    url = os.environ.get("FRIDAY_HOOK_URL")
    if not url and CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            url = tomllib.load(f).get("hook", {}).get("base_url")
    if not url:
        raise HookError("no base URL configured")
    return url.rstrip("/")


def read_token(*, timeout=KEYCHAIN_TIMEOUT_SECONDS, run=subprocess.run):
    token = os.environ.get("FRIDAY_HOOK_TOKEN")
    if token:
        return token.strip()
    try:
        result = run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HookError("keychain unavailable") from exc
    token = (result.stdout or "").strip()
    if result.returncode != 0 or not token:
        raise HookError("no token in keychain")
    return token


def post(url, token, body, *, timeout, opener=urllib.request.urlopen):
    request = urllib.request.Request(
        url + INGEST_PATH,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with opener(request, timeout=timeout) as response:
        return response.status


def log(line):
    """One line to the local log (0600). Best effort: a logging failure is
    swallowed too -- nothing may disturb Claude Code."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            os.write(fd, f"{stamp} {line}\n".encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 -- best effort by design
        pass


def run(stdin, *, opener=urllib.request.urlopen, keychain=subprocess.run, clock=time.monotonic):
    started = clock()
    raw = stdin.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        log("skipped: hook input too large")
        return
    try:
        payload = json.loads(raw or b"null")
    except ValueError:
        log("skipped: hook input is not JSON")
        return
    event = build_event(payload)
    if event is None:
        return  # not an event Friday ingests: nothing to do, nothing to log
    url = base_url()
    token = read_token(run=keychain)
    remaining = DEADLINE_SECONDS - (clock() - started)
    status = post(
        url,
        token,
        {"events": [event]},
        timeout=max(MIN_POST_TIMEOUT_SECONDS, remaining),
        opener=opener,
    )
    if status != 202:
        log(f"unexpected status {status}")


def main(stdin=None, **kwargs):
    """Always returns 0. Errors are logged by class (and HTTP status) only."""
    stdin = stdin if stdin is not None else sys.stdin.buffer
    try:
        run(stdin, **kwargs)
    except urllib.error.HTTPError as exc:
        log(f"error: HTTP {exc.code}")
    except HookError as exc:
        log(f"error: {exc}")
    except Exception as exc:  # noqa: BLE001 -- never disturb Claude Code
        log(f"error: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
