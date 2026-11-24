"""The unattended-denial recorder (Phase 12 D2, "Who writes the audit row").

`core.unattended.deny_if_unattended` calls the run's `on_denied` callback
before it raises. `DenialRecorder` is that callback: calling it only
appends to an in-memory buffer -- no database write at raise time, so a
denial raised inside a caller's `transaction.atomic()` that then rolls
back cannot take the audit row with it. `flush()` writes one
`proactive.unattended_denied` row per buffered denial and empties the
buffer; runners call it after every step's own `atomic()` block has
ended, and again in a `finally`, so each denial is audited exactly once.

Imports only `core` and `audit` itself.
"""

from contextlib import contextmanager

from audit.models import AuditLog
from audit.services import record_audit
from core.unattended import UnattendedDenial, unattended_run

DENIAL_ACTION = "proactive.unattended_denied"


class DenialRecorder:
    """`workspace` and `source` (e.g. `proactive.schedule`, `skills.curator`)
    are stamped on every row; the denial itself carries effect, run id and
    step name -- never content."""

    def __init__(self, *, workspace=None, source=""):
        self.workspace = workspace
        self.source = source
        self._buffer: list[UnattendedDenial] = []

    def __call__(self, denial: UnattendedDenial) -> None:
        self._buffer.append(denial)

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def flush(self) -> int:
        """Write the buffered denials; returns how many were written.

        Denials leave the buffer one at a time, only after their row is
        written: if a write raises, that denial and the unwritten tail stay
        buffered for the next flush (exactly one row per denial)."""
        written = 0
        while self._buffer:
            denial = self._buffer[0]
            record_audit(
                action=DENIAL_ACTION,
                actor=None,
                actor_type=AuditLog.ActorType.SYSTEM,
                workspace=self.workspace,
                metadata={
                    "effect": denial.effect,
                    "run_id": denial.run_id,
                    "step": denial.step,
                    "source": self.source,
                },
            )
            self._buffer.pop(0)
            written += 1
        return written


@contextmanager
def recorded_unattended_run(run_id, *, allowed_purposes=frozenset(), workspace=None, source=""):
    """Open an unattended context with a fresh recorder and flush it on
    exit (for tests and one-shot callers that call a service directly)."""
    recorder = DenialRecorder(workspace=workspace, source=source)
    try:
        with unattended_run(run_id, allowed_purposes=allowed_purposes, on_denied=recorder):
            yield recorder
    finally:
        recorder.flush()
