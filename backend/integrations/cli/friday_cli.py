"""Minimal CLI client (MVP 9 §2) -- a raw HTTP client of the exact same
`/api/v1/ai/conversations/.../messages` endpoint the web app calls,
authenticated with a PersonalAccessToken instead of a JWT. Deliberately
thin: proves the CLI channel works end-to-end, not a polished terminal UI
(rich REPL/history/multi-turn UX is out of this MVP's scope).

Standalone stdlib script -- no Django import, no ORM access, so it never
needs DJANGO_SETTINGS_MODULE. Run it with:

    uv run python -m integrations.cli.friday_cli "hello there"
    echo "hello there" | uv run python -m integrations.cli.friday_cli

Config, in precedence order:
    - FRIDAY_CLI_TOKEN / FRIDAY_CLI_URL env vars (deprecated fallback:
      JARVIS_CLI_TOKEN / JARVIS_CLI_URL)
    - ~/.config/friday/cli.toml (fallback: the pre-rename
      ~/.config/jarvis/cli.toml):
        [cli]
        token = "jpat_..."
        base_url = "http://localhost:8001"
        conversation_id = "..."   # optional; a fresh conversation is
                                   # created (and its id printed to
                                   # stderr) if omitted.
"""

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from core import legacy_env

CONFIG_PATH = Path.home() / ".config" / "friday" / "cli.toml"
# Pre-rename location (Jarvis -> Friday, 2026-11-20): read when the new file
# does not exist yet, so an existing CLI setup keeps working.
LEGACY_CONFIG_PATH = Path.home() / ".config" / "jarvis" / "cli.toml"


class CliConfigError(Exception):
    """Missing/invalid token or base URL -- never a network or server error."""


def load_config():
    file_config = {}
    config_path = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
    if config_path.exists():
        with config_path.open("rb") as f:
            file_config = tomllib.load(f).get("cli", {})

    # FRIDAY_CLI_* first, then the deprecated JARVIS_CLI_* spelling.
    token = legacy_env.getenv("FRIDAY_CLI_TOKEN") or file_config.get("token")
    base_url = legacy_env.getenv("FRIDAY_CLI_URL") or file_config.get("base_url")

    if not token:
        raise CliConfigError(
            f"No token configured -- set FRIDAY_CLI_TOKEN or add [cli] token to {CONFIG_PATH}."
        )
    if not base_url:
        raise CliConfigError(
            f"No base URL configured -- set FRIDAY_CLI_URL or add [cli] base_url to {CONFIG_PATH}."
        )
    return {
        "token": token,
        "base_url": base_url.rstrip("/"),
        "conversation_id": file_config.get("conversation_id"),
    }


def _request(base_url, token, path, *, method, body=None):
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req)


def create_conversation(base_url, token):
    with _request(base_url, token, "/api/v1/ai/conversations", method="POST", body={}) as resp:
        return json.loads(resp.read())["id"]


def _iter_sse_events(response):
    """Yields parsed `{"type": ..., ...}` dicts as they arrive on the wire
    -- the receiving mirror of `assistant.services.sse_event`."""
    buffer = b""
    for line in response:
        if line in (b"\n", b"\r\n"):
            if buffer:
                text = buffer.decode()
                if text.startswith("data: "):
                    yield json.loads(text[len("data: ") :])
                buffer = b""
        else:
            buffer += line


def send_message(base_url, token, conversation_id, content, *, out=None):
    """Streams chunk text to `out` as it arrives; returns the terminal
    event (`done` or `error`). A mid-stream gateway failure is the normal
    `error` terminal event, not an exception -- only a genuine HTTP/network
    failure (bad token, unreachable server) raises.

    `out` defaults to whatever `sys.stdout` is AT CALL TIME, not import
    time -- a bare `out=sys.stdout` default parameter binds the stream
    object present when this module was first imported, which silently
    breaks under anything that reassigns `sys.stdout` later (pytest's
    `capsys`, output redirection, etc.).

    Sends `client_local_date` (this machine's local calendar date) the
    same way the web client does -- task-draft extraction only resolves
    relative due dates ("tomorrow") when it's supplied; omitting it would
    silently drop due dates from CLI-originated draft tasks (Codex review,
    branch 2 round 2)."""
    if out is None:
        out = sys.stdout
    with _request(
        base_url,
        token,
        f"/api/v1/ai/conversations/{conversation_id}/messages",
        method="POST",
        body={"content": content, "client_local_date": date.today().isoformat()},
    ) as resp:
        for event in _iter_sse_events(resp):
            if event["type"] == "chunk":
                out.write(event["text"])
                out.flush()
            elif event["type"] in ("done", "error"):
                return event
    return {"type": "error", "error_type": "stream_ended_without_terminal_event"}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Send one chat message to Friday over the CLI channel."
    )
    parser.add_argument("message", nargs="?", help="Message text; reads stdin if omitted.")
    args = parser.parse_args(argv)

    content = args.message
    if content is None:
        stdin_content = sys.stdin.read()
        # Trim only the single trailing newline a shell pipe adds (`echo
        # "..." | friday_cli`) -- never the surrounding whitespace itself.
        # MessageCreateSerializer declares trim_whitespace=False for
        # exactly this reason: meaningful leading/trailing whitespace (an
        # indented code snippet) must reach the API unmodified (Codex
        # review, branch 2 round 2).
        content = stdin_content[:-1] if stdin_content.endswith("\n") else stdin_content
    if not content.strip():
        print("No message given.", file=sys.stderr)
        return 1

    try:
        config = load_config()
    except CliConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        conversation_id = config["conversation_id"]
        if not conversation_id:
            conversation_id = create_conversation(config["base_url"], config["token"])
            print(
                f"Created conversation {conversation_id} -- add conversation_id = "
                f'"{conversation_id}" under [cli] in {CONFIG_PATH} to reuse it.',
                file=sys.stderr,
            )

        terminal_event = send_message(config["base_url"], config["token"], conversation_id, content)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Connection failed: {exc.reason}", file=sys.stderr)
        return 1

    print()  # newline after the streamed reply
    if terminal_event["type"] == "error":
        print(f"Gateway error: {terminal_event['error_type']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
