"""The transcript memory port (Phase 8B branch 8, brief D13).

The pull job (`tools.transcript_ingest`, the only app that may reach both
the node layer and memory) hands this port the owner-turn rows one node
invoke returned for one workspace:

    ingest_owner_turns(*, user, tool, workspace, sessions, now)
        -> TranscriptIngestResult

Per turn, in order:

1. **Shape.** `session_id`/`turn_id` in the contract charset, `cwd` a valid
   `normalise_path` form, `at` an RFC 3339 instant no later than `now` plus
   the clock-skew margin, `text` a string. Anything else is `invalid`
   (counted in the result, never stored).
2. **The server marker re-check.** A turn whose text starts with a
   harness-injected marker (`INJECTED_MARKERS`, the union of the Claude
   Code and Codex reader rules) is dropped and counted `dropped_not_owner`,
   so a reader bug or a hostile node cannot turn injected text into owner
   text.
3. **The hostile-node rule.** The row's `cwd` must resolve (longest prefix
   wins, through the shared hooks rules) to the invoke's `workspace`; a row
   that resolves elsewhere is dropped and counted `dropped_foreign`. A row
   that resolves nowhere goes on to `record_episode`, which drops it as
   `dropped_unmapped` on the **transcript** source.
4. `record_episode` with `kind` `claude_code.owner_turn`/`codex.owner_turn`,
   `external_id` `cc:<session_id>:<turn_id>`/`cx:<thread_id>:<turn_id>` (a
   re-read dedupes), `mapping_key` `"path:" + cwd`, and the turn text as
   `owner_text`: the port scrubs it a second time, with the target
   workspace's `ScrubTerm`s, before the row is written.

The port imports no node code (`memory.ingestion` may import neither the
node layer nor `tools`, an import-linter contract).
"""

import posixpath
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.db import transaction

from memory.ingestion import ports, services
from memory.ingestion.mapping import InvalidMappingKey, normalise_path, resolve_workspace
from memory.models import IngestionSource
from workspaces.models import Workspace

TOOL_CLAUDE = "claude"
TOOL_CODEX = "codex"

SOURCE_KINDS = {
    TOOL_CLAUDE: IngestionSource.Kind.CLAUDE_CODE_TRANSCRIPTS,
    TOOL_CODEX: IngestionSource.Kind.CODEX_TRANSCRIPTS,
}
EPISODE_KINDS = {TOOL_CLAUDE: "claude_code.owner_turn", TOOL_CODEX: "codex.owner_turn"}
EXTERNAL_ID_PREFIXES = {TOOL_CLAUDE: "cc", TOOL_CODEX: "cx"}

# The D13 reader rules: a returned text never starts with one of these.
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
    # The 2026-11-27 additions (brief D13): the rest of the family that
    # Claude Code's own "not a human prompt" checks name. Shell-mode input
    # (`<bash-input>`) and the `#` memory note are commands, not prompts.
    "<local-command-caveat>",
    "<bash-input>",
    "<bash-stdout",
    "<bash-stderr",
    "<bash-exit-code>",
    "<task-notification>",
    "<tick>",
    "<user-memory-input>",
    "<fork-boilerplate>",
    "<teammate-message",
    "<cross-session-message",
    "<agent-message",
    '<channel source="',
    "<mcp-resource-update",
    "<mcp-polling-update",
    "Another Claude session sent a message",
)
CODEX_MARKERS = (
    "<environment_context>",
    "<user_instructions>",
    "<user_shell_command>",
    "# AGENTS.md instructions",
    "<skill>",
    "<turn_aborted>",
    "<subagent_notification>",
)
# The server re-applies both tools' markers to every turn, whichever tool
# returned it: a marker is never owner text.
INJECTED_MARKERS = CLAUDE_MARKERS + CODEX_MARKERS

MAX_TURN_CHARS = 4000
# Clock skew tolerated between the server and the node (D13 clamp margin).
CLOCK_SKEW = timedelta(minutes=5)
_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RFC3339 = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})\Z")


class TranscriptPortError(ValueError):
    """A caller bug (unknown tool, a user who does not own the source)."""


def is_injected(text) -> bool:
    """Whether `text` starts (after leading whitespace) with a marker."""
    return str(text).lstrip().startswith(INJECTED_MARKERS)


def parse_instant(value) -> datetime | None:
    """An aware UTC datetime from an RFC 3339 string, or None."""
    if not isinstance(value, str) or not _RFC3339.match(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def format_instant(value: datetime) -> str:
    """RFC 3339, UTC, millisecond precision (the D13 `since` form)."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def transcript_source(user, tool) -> IngestionSource | None:
    """`user`'s transcript source for `tool`, in their personal workspace."""
    kind = SOURCE_KINDS.get(tool)
    if kind is None:
        raise TranscriptPortError(f"unknown transcript tool {tool!r}")
    return (
        IngestionSource.objects.filter(
            kind=kind, workspace__owner=user, workspace__type=Workspace.Type.PERSONAL
        )
        .select_related("workspace")
        .order_by("created_at")
        .first()
    )


@dataclass
class TranscriptIngestResult:
    stored: int = 0
    duplicate: int = 0
    dropped_unmapped: int = 0
    dropped_paused: int = 0
    dropped_not_owner: int = 0
    dropped_foreign: int = 0
    invalid: int = 0
    episode_ids: list = field(default_factory=list)

    def as_counts(self) -> dict:
        return {
            "stored": self.stored,
            "duplicate": self.duplicate,
            "dropped_unmapped": self.dropped_unmapped,
            "dropped_paused": self.dropped_paused,
            "dropped_not_owner": self.dropped_not_owner,
            "dropped_foreign": self.dropped_foreign,
            "invalid": self.invalid,
        }


def _short_cwd(cwd):
    return posixpath.basename(cwd.rstrip("/")) or cwd


def _turns(session):
    turns = session.get("turns") if isinstance(session, dict) else None
    return turns if isinstance(turns, list) else []


def ingest_owner_turns(*, user, tool, workspace, sessions, now) -> TranscriptIngestResult:
    """Store the owner turns of one invoke for `workspace` (see the module
    docstring). Runs in one transaction: the pull job advances its cursor
    only after this returns (the episodes are committed)."""
    source = transcript_source(user, tool)
    if source is None or source.workspace.owner_id != user.pk:
        raise TranscriptPortError("no transcript source of this user")
    result = TranscriptIngestResult()
    if not isinstance(sessions, list):
        return result
    latest = now + CLOCK_SKEW
    with transaction.atomic():
        for session in sessions:
            if not isinstance(session, dict):
                result.invalid += 1
                continue
            session_id = session.get("session_id")
            try:
                cwd = normalise_path(session.get("cwd"))
            except (InvalidMappingKey, TypeError, AttributeError):
                cwd = None
            if not isinstance(session_id, str) or not _ID.match(session_id) or cwd is None:
                result.invalid += len(_turns(session)) or 1
                continue
            mapping_key = f"path:{cwd}"
            target = resolve_workspace(source, mapping_key)
            for turn in _turns(session):
                _ingest_turn(
                    result,
                    source=source,
                    tool=tool,
                    workspace=workspace,
                    target=target,
                    session_id=session_id,
                    cwd=cwd,
                    mapping_key=mapping_key,
                    turn=turn,
                    latest=latest,
                )
    return result


def _ingest_turn(
    result, *, source, tool, workspace, target, session_id, cwd, mapping_key, turn, latest
):
    if not isinstance(turn, dict):
        result.invalid += 1
        return
    turn_id = turn.get("turn_id")
    text = turn.get("text")
    at = parse_instant(turn.get("at"))
    if (
        not isinstance(turn_id, str)
        or not _ID.match(turn_id)
        or not isinstance(text, str)
        or at is None
        or at > latest
    ):
        result.invalid += 1
        return
    if is_injected(text):
        result.dropped_not_owner += 1
        services.bump_counter(source, "dropped_not_owner")
        return
    if target is not None and target.pk != workspace.pk:
        result.dropped_foreign += 1
        services.bump_counter(source, "dropped_foreign")
        return
    outcome = ports.record_episode(
        source=source,
        kind=EPISODE_KINDS[tool],
        external_id=f"{EXTERNAL_ID_PREFIXES[tool]}:{session_id}:{turn_id}",
        occurred_at=at,
        mapping_key=mapping_key,
        owner_text=text[:MAX_TURN_CHARS],
        metadata={
            "tool": tool,
            "session_id": session_id,
            "cwd": _short_cwd(cwd),
            "at": format_instant(at),
        },
    )
    if outcome.status == ports.STORED:
        result.stored += 1
        result.episode_ids.append(outcome.episode_id)
    elif outcome.status == ports.DUPLICATE:
        result.duplicate += 1
    elif outcome.status == ports.DROPPED_UNMAPPED:
        result.dropped_unmapped += 1
    else:
        result.dropped_paused += 1
