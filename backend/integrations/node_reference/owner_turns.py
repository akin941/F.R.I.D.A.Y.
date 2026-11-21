"""Reference owner-turn readers (Phase 8B branch 8, brief D13 and D17).

The executable spec of the node side of `claude.transcripts.owner_turns`
and `codex.transcripts.owner_turns`: what the Swift `friday-node` readers
(Mac session M1) must return for the same files. Standard library only and
no project import except the vendored scrubber (an import-linter contract),
so it runs anywhere the reference node runs.

**Staging note.** Phase 9 branches 3-5 (the reference node, the gateway and
`invoke_read`) are not merged yet. Until they are, this package holds the
two capability contracts (`contracts/v1/`, in the Phase 9 contract format),
the readers, the fixtures and the conformance cases; when they merge, the
contracts move into `nodes/capabilities/v1/` and the reference node's
`readers.py` dispatches to `read_owner_turns` (brief owner steps).

    read_owner_turns(capability, args, *, root, granted, now) -> dict

returns `{"status": "ok", "output": {...}}` or `{"status": "refused",
"code": <code>}` with `code` one of `unknown_capability`, `not_granted`,
`args_contract`.

Rules (normative, re-checked by the server in `memory.ingestion.
transcripts`):

- **Claude Code JSONL** (`<root>/claude/projects/<dir>/<session>.jsonl`): a
  line is a turn only if `type == "user"`, `isMeta`, `isSidechain` and
  `isCompactSummary` are not true, `message.content` is a string or text
  blocks only (any `tool_result` block skips the whole line), and the text
  does not start with a harness-injected marker. `turn_id` is the line's
  own `uuid`.
- **Codex rollout JSONL** (`<root>/codex/sessions/**/*.jsonl`): the
  `session_meta` line names the thread (`payload.id`) and its `cwd`; a turn
  is a `response_item` whose payload is a `message` with `role == "user"`
  and `input_text` parts only, whose text does not start with
  `<environment_context>`, `<user_instructions>` or `<user_shell_command>`.
  `turn_id` is a stable hash of the file's relative path and the line's
  byte offset.
- Unknown line kinds are skipped (fail closed). Rows whose `cwd` is outside
  `cwd_prefixes` are skipped before their turns are parsed.
- Every text is scrubbed with the vendored 8A scrubber, then cut to 4000
  characters.
- Turns are returned oldest first by `(at, turn_id)`, with `at >= since`
  (inclusive: the server dedupes ties on `external_id`). The first turn a
  cap (`max_turns`, `max_sessions`, the result byte budget) cannot take
  ends the result with `truncated: true`, so nothing older than
  `next_since` is ever skipped. `next_since` is the `at` of the last
  returned turn (`since` when nothing is returned).
- Nothing is cached, copied or logged: every call re-reads the files.
"""

import hashlib
import json
import posixpath
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from integrations.cli import _scrubber_vendored as scrubber

CLAUDE = "claude.transcripts.owner_turns"
CODEX = "codex.transcripts.owner_turns"
CAPABILITIES = (CLAUDE, CODEX)
CONTRACT_DIR = Path(__file__).resolve().parent / "contracts" / "v1"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CLAUDE_MARKERS = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<system-reminder>",
    "<user-prompt-submit-hook>",
    "Caveat: The messages below were generated",
    "[Request interrupted",
)
CODEX_MARKERS = ("<environment_context>", "<user_instructions>", "<user_shell_command>")

MAX_TEXT_CHARS = 4000
MAX_SINCE_AGE = timedelta(days=7)
MAX_SESSIONS = 50
MAX_TURNS = 200
MAX_PREFIXES = 20
MAX_PATH_CHARS = 500
MAX_RESULT_BYTES = 1_048_576
MAX_LINE_BYTES = 1_048_576
_ARG_KEYS = frozenset({"since", "cwd_prefixes", "max_sessions", "max_turns"})
_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RFC3339 = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})\Z"
)


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def load_contract(name):
    return json.loads((CONTRACT_DIR / f"{name}.json").read_text(encoding="utf-8"))


# --- time and paths ------------------------------------------------------------


def parse_instant(value):
    if not isinstance(value, str) or not _RFC3339.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def format_instant(value):
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def normalise_path(path):
    """The server's `normalise_path` (memory.ingestion.mapping), stdlib
    copy: `~` kept literal, absolute otherwise, no trailing slash. None for
    a path the server would refuse."""
    if not isinstance(path, str):
        return None
    path = path.strip()
    if not path or "\x00" in path or len(path) > MAX_PATH_CHARS:
        return None
    if path == "~" or path.startswith("~/"):
        if len(path) == 1:
            return "~"
        relative = posixpath.normpath(path[1:].lstrip("/") or ".")
        if relative == ".." or relative.startswith("../"):
            return None
        return "~" if relative == "." else "~/" + relative
    if not path.startswith("/"):
        return None
    normalised = posixpath.normpath(path)
    if normalised.startswith("//"):
        normalised = "/" + normalised.lstrip("/")
    return normalised


def under_prefixes(cwd, prefixes):
    for prefix in prefixes:
        if prefix in ("/", "~"):
            if cwd == prefix or cwd.startswith(prefix.rstrip("/") + "/"):
                return True
        elif cwd == prefix or cwd.startswith(prefix + "/"):
            return True
    return False


# --- argument contract -----------------------------------------------------------


def _int_arg(args, key, maximum):
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise _Refused("args_contract")
    return value


def validate_args(args, *, now):
    """The contract's `args_schema` plus the `since >= now - 7 days` bound
    a schema cannot express. Returns `(since, prefixes, max_sessions,
    max_turns)` or raises `_Refused("args_contract")`."""
    if not isinstance(args, dict) or set(args) != _ARG_KEYS:
        raise _Refused("args_contract")
    since = parse_instant(args.get("since"))
    if since is None or since < now - MAX_SINCE_AGE or since > now + timedelta(minutes=5):
        raise _Refused("args_contract")
    raw = args.get("cwd_prefixes")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PREFIXES:
        raise _Refused("args_contract")
    if len(set(map(str, raw))) != len(raw):
        raise _Refused("args_contract")
    prefixes = []
    for item in raw:
        normalised = normalise_path(item)
        if normalised is None:
            raise _Refused("args_contract")
        prefixes.append(normalised)
    return (
        since,
        prefixes,
        _int_arg(args, "max_sessions", MAX_SESSIONS),
        _int_arg(args, "max_turns", MAX_TURNS),
    )


# --- readers ---------------------------------------------------------------------


def _jsonl(path):
    """`(byte_offset, object)` for each parseable JSON-object line; a line
    that is too long or not an object is skipped (fail closed)."""
    offset = 0
    with path.open("rb") as handle:
        for raw in handle:
            start = offset
            offset += len(raw)
            if len(raw) > MAX_LINE_BYTES:
                continue
            try:
                line = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(line, dict):
                yield start, line


def _clean(text):
    return scrubber.scrub_text(text).text[:MAX_TEXT_CHARS]


def _claude_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return None
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            return None  # tool_result, image or anything else: skip the line
        text = block.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "\n".join(parts)


def claude_line_turn(line):
    """`(session_id, cwd, turn_id, at, raw_text)` or None (not an owner turn)."""
    if line.get("type") != "user":
        return None
    if line.get("isMeta") is True or line.get("isSidechain") is True:
        return None
    if line.get("isCompactSummary") is True:
        return None
    message = line.get("message")
    if not isinstance(message, dict) or message.get("role", "user") != "user":
        return None
    text = _claude_text(message.get("content"))
    if text is None or not text.strip() or text.lstrip().startswith(CLAUDE_MARKERS):
        return None
    session_id, turn_id = line.get("sessionId"), line.get("uuid")
    at = parse_instant(line.get("timestamp"))
    cwd = normalise_path(line.get("cwd"))
    if not (isinstance(session_id, str) and _ID.match(session_id)):
        return None
    if not (isinstance(turn_id, str) and _ID.match(turn_id)) or at is None or cwd is None:
        return None
    return session_id, cwd, turn_id, at, text


def _claude_turns(root, prefixes):
    base = root / "claude" / "projects"
    if not base.is_dir():
        return
    for path in sorted(base.glob("*/*.jsonl")):
        for _offset, line in _jsonl(path):
            # The prefix check runs on the row's cwd before anything else of
            # the line is parsed.
            cwd = normalise_path(line.get("cwd"))
            if cwd is None or not under_prefixes(cwd, prefixes):
                continue
            turn = claude_line_turn(line)
            if turn is not None:
                yield turn


def codex_line_text(line):
    """The owner text of one Codex rollout line, or None."""
    if line.get("type") != "response_item":
        return None
    payload = line.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    if payload.get("role") != "user":
        return None
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        return None
    parts = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "input_text":
            return None
        text = part.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    text = "\n".join(parts)
    if not text.strip() or text.lstrip().startswith(CODEX_MARKERS):
        return None
    return text


def _codex_turns(root, prefixes):
    base = root / "codex" / "sessions"
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*.jsonl")):
        lines = _jsonl(path)
        meta = next(lines, None)
        if meta is None or meta[1].get("type") != "session_meta":
            continue
        payload = meta[1].get("payload")
        if not isinstance(payload, dict):
            continue
        thread_id, cwd = payload.get("id"), normalise_path(payload.get("cwd"))
        if not (isinstance(thread_id, str) and _ID.match(thread_id)) or cwd is None:
            continue
        if not under_prefixes(cwd, prefixes):
            continue  # skipped before any turn of the file is parsed
        relative = path.relative_to(root).as_posix()
        for offset, line in lines:
            text = codex_line_text(line)
            at = parse_instant(line.get("timestamp"))
            if text is None or at is None:
                continue
            turn_id = hashlib.sha256(f"{relative}:{offset}".encode()).hexdigest()[:32]
            yield thread_id, cwd, turn_id, at, text


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def collect(capability, root, prefixes, since, max_sessions, max_turns):
    reader = _claude_turns if capability == CLAUDE else _codex_turns
    candidates = [
        (at, turn_id, session_id, cwd, text)
        for session_id, cwd, turn_id, at, text in reader(Path(root), prefixes)
        if at >= since
    ]
    candidates.sort(key=lambda row: (row[0], row[1]))
    sessions = {}
    order = []
    taken = 0
    size = _size({"sessions": [], "next_since": format_instant(since), "truncated": True})
    truncated = False
    last_at = since
    for at, turn_id, session_id, cwd, text in candidates:
        key = (session_id, cwd)
        turn = {"turn_id": turn_id, "at": format_instant(at), "text": _clean(text)}
        added = _size(turn) + 1
        if key not in sessions:
            added += _size({"session_id": session_id, "cwd": cwd, "turns": []}) + 1
        if (
            taken >= max_turns
            or (key not in sessions and len(order) >= max_sessions)
            or size + added > MAX_RESULT_BYTES
        ):
            truncated = True
            break
        if key not in sessions:
            sessions[key] = {"session_id": session_id, "cwd": cwd, "turns": []}
            order.append(key)
        sessions[key]["turns"].append(turn)
        taken += 1
        size += added
        last_at = at
    return {
        "sessions": [sessions[key] for key in order],
        "next_since": format_instant(last_at),
        "truncated": truncated,
    }


def read_owner_turns(capability, args, *, root, granted, now=None):
    """One invoke of an owner-turn capability (see the module docstring).
    `granted` is the per-directory Touch ID grant (ADR-013 decision 11):
    without it the node answers `not_granted` and reads nothing."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if capability not in CAPABILITIES:
        return {"status": "refused", "code": "unknown_capability"}
    if not granted:
        return {"status": "refused", "code": "not_granted"}
    try:
        since, prefixes, max_sessions, max_turns = validate_args(args, now=now)
    except _Refused as refused:
        return {"status": "refused", "code": refused.code}
    output = collect(capability, root, prefixes, since, max_sessions, max_turns)
    return {"status": "ok", "output": output}
