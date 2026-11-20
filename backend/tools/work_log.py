"""`work_log` for gated-tool outcomes (ADR-008 decision 4; Phase 5 brief
D13).

`record_tool_outcome` wraps `memory.services.record_work_log` -- still the
only creation path for `work_log` rows -- for a gated tool's terminal
execution: vault export today, connector writes and deploy runs from
Phase 5 branches 4 and 6. The content is Jarvis-authored bookkeeping
only: the tool name, the outcome token, the date, the approval request id
and a summary the calling code composes from its own closed-vocabulary
fields (counts, error types, ids). Never connector output, never payload
free text beyond an owner-authored title -- a work_log memory reaches chat
context like any other approved memory, so nothing attacker-authored may
ride into it.

Best-effort by design: the outcome itself (the terminal status, its audit
row) is already decided when this runs, so a work_log failure is logged
and swallowed inside its own savepoint, never rolled into the caller's
transaction. A `DuplicateMemory` is silent. The content carries the date
and the approval request id, so the same outcome of a different execution
is never swallowed as a duplicate.
"""

import logging
import re

from django.db import transaction
from django.utils import timezone

from memory.services import DuplicateMemory, record_work_log
from tools.registry import get_tool

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 200
SUMMARY_MAX_CHARS = 2000

# An outcome is a code-supplied status token (`succeeded`, `failed`,
# `rolled_back`, ...), never text from a payload or a remote system.
_OUTCOME_RE = re.compile(r"^[a-z][a-z_]{0,39}$")


def record_tool_outcome(approval_request, *, outcome, summary, sensitivity):
    """Records one `work_log` memory for a terminal execution of
    `approval_request`'s tool, in the request's workspace. Returns the
    `Memory` row, or `None` when nothing was recorded: the tool opted out
    (`ToolSpec.records_work_log=False`), the row already exists, or the
    write failed (logged).

    `sensitivity` is required, not defaulted: a producer handling personal
    or sensitive-repo data must say `private` itself (ADR-005), and one
    that has nothing sensitive says `normal` explicitly. `outcome` must be
    a lowercase status token; anything else is a programming error and
    raises `ValueError` before any write."""
    if not isinstance(outcome, str) or not _OUTCOME_RE.match(outcome):
        raise ValueError(f"work_log outcome must be a status token, got {outcome!r}.")
    spec = get_tool(approval_request.tool_name)
    if spec is not None and not spec.records_work_log:
        return None
    day = timezone.localdate().isoformat()
    tool_name = approval_request.tool_name
    title = f"{tool_name}: {outcome} ({day})"[:TITLE_MAX_CHARS]
    lines = [
        f"Gated tool {tool_name} {outcome} on {day}.",
        f"Approval request: {approval_request.id}.",
    ]
    if summary:
        lines.append(summary[:SUMMARY_MAX_CHARS])
    try:
        with transaction.atomic():
            return record_work_log(
                workspace=approval_request.workspace,
                title=title,
                content="\n".join(lines),
                sensitivity=sensitivity,
            )
    except DuplicateMemory:
        return None
    except Exception:
        logger.exception(
            "work_log for %s request %s (%s) failed", tool_name, approval_request.id, outcome
        )
        return None
