"""One driver run of an implementation job (Phase 10 brief D10).

`DriverSession.run` performs the handshake, runs one turn with the job's
goal and drives the event loop: every escalation goes through
`approval_mapping` (one `harness_agent_action` approval, a row-polling wait,
one `accept`/`decline` answer for that request only), every completed turn
records one `ModelUsage` row through `ai_core.gateway.record_external_usage`
(purpose `harness.implementation`), and a turn -- or a run -- that ends
without a usage report records a `success=False`, zero-token row so it is
never invisible. It never raises: the outcome, the failure code and the
per-turn usage come back in a `DriverRunReport`, and the caller tears the
container down on anything but `completed`.
"""

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

from ai_core.gateway import record_external_usage
from harness.drivers import approval_mapping
from harness.drivers.base import (
    CODE_DEADLINE,
    CODE_STOPPED,
    CODE_TURN_FAILED,
    DriverError,
    TurnUsage,
)

logger = logging.getLogger(__name__)

PURPOSE = "harness.implementation"

OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_STOPPED = "stopped"


@dataclass
class DriverRunReport:
    outcome: str = OUTCOME_FAILED
    code: str = ""
    turns: list = field(default_factory=list)
    transcript: str = ""
    escalations: int = 0
    accepted: int = 0
    declined: int = 0

    @property
    def usage_complete(self):
        return bool(self.turns) and all(turn.reported for turn in self.turns)

    @property
    def input_tokens(self):
        return sum(turn.input_tokens for turn in self.turns)

    @property
    def output_tokens(self):
        return sum(turn.output_tokens for turn in self.turns)

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens

    def context_report(self, driver_name):
        return {
            "usage_source": "driver_reported" if self.usage_complete else "unavailable",
            "driver": driver_name,
            "turns": len(self.turns),
            "cost_source": "not_reported",
            "escalations": self.escalations,
            "escalations_accepted": self.accepted,
            "escalations_declined": self.declined,
        }


class DriverSession:
    def __init__(
        self,
        *,
        job,
        driver,
        deadline,
        should_stop,
        clock=time.monotonic,
        now=None,
        wait=None,
        poll_seconds=None,
        max_escalations=None,
    ):
        self.job = job
        self.driver = driver
        self.deadline = deadline
        self.should_stop = should_stop
        self.clock = clock
        self._now = now
        self._wait = wait
        self._poll_seconds = poll_seconds
        self._max_escalations = (
            max_escalations
            if max_escalations is not None
            else settings.HARNESS_DRIVER_MAX_ESCALATIONS
        )
        self.report = DriverRunReport()
        self._turn_started = {}
        self._recorded = set()

    @property
    def backend(self):
        return f"harness_driver:{self.driver.agent}:{self.driver.name}"

    def run(self, container, goal):
        report = self.report
        try:
            self.driver.start(container)
            turn_id = self.driver.run_turn(goal)
            self._turn_started[turn_id] = self.clock()
            for event in self.driver.events():
                if self.should_stop():
                    raise DriverError(CODE_STOPPED, "job cancelled")
                if event.kind == "escalation":
                    self._escalate(event.payload)
                elif event.kind == "turn_completed":
                    turn = event.payload
                    self._record_turn(turn, error_type="" if turn.reported else "usage_unreported")
                    if turn.status != "completed":
                        raise DriverError(CODE_TURN_FAILED, f"turn ended {turn.status!r}")
            report.outcome = OUTCOME_COMPLETED
        except DriverError as exc:
            report.code = exc.code
            report.outcome = OUTCOME_STOPPED if exc.code == CODE_STOPPED else OUTCOME_FAILED
            logger.warning("harness driver run for job %s failed closed: %s", self.job.id, exc)
        finally:
            try:
                self.driver.close()
            finally:
                report.turns = self.driver.usage()
                report.transcript = self.driver.transcript.text()
                self._record_missing_usage()
        return report

    # --- escalations ------------------------------------------------------

    def _escalate(self, escalation):
        report = self.report
        report.escalations += 1
        approval = None
        if report.escalations > self._max_escalations:
            accept, reason = False, approval_mapping.ESCALATION_LIMIT
        else:
            approval = approval_mapping.request_agent_action(self.job, escalation)
            if approval is None:
                accept, reason = False, approval_mapping.REQUEST_REFUSED
            else:
                accept, reason = approval_mapping.wait_for_decision(
                    self.job,
                    approval.pk,
                    deadline=self.deadline,
                    should_stop=self.should_stop,
                    clock=self.clock,
                    now=self._now,
                    wait=self._wait,
                    poll_seconds=self._poll_seconds,
                )
        accept = accept is True and reason == approval_mapping.APPROVED
        try:
            self.driver.answer_approval(escalation.request_id, accept)
        finally:
            if accept:
                report.accepted += 1
            else:
                report.declined += 1
            approval_mapping.record_answer(self.job, approval, accept=accept, reason=reason)
        if reason == approval_mapping.JOB_CANCELLED:
            raise DriverError(CODE_STOPPED, "job cancelled while an escalation was pending")
        if reason == approval_mapping.RUNTIME_BRAKE:
            raise DriverError(CODE_DEADLINE, "runtime brake while an escalation was pending")

    # --- usage --------------------------------------------------------------

    def _record_turn(self, turn, *, error_type):
        if turn.turn_id in self._recorded:
            return
        self._recorded.add(turn.turn_id)
        started = self._turn_started.get(turn.turn_id, self.clock())
        latency_ms = max(0, int((self.clock() - started) * 1000))
        job = self.job
        record_external_usage(
            workspace=job.workspace,
            purpose=PURPOSE,
            backend=self.backend,
            routing_reason=job.agent_routing_reason,
            input_tokens=turn.input_tokens if turn.reported else 0,
            output_tokens=turn.output_tokens if turn.reported else 0,
            estimated_cost=Decimal("0"),
            latency_ms=latency_ms,
            success=turn.reported and not error_type,
            actor=job.created_by,
            error_type=error_type,
            context_report={
                "usage_source": "driver_reported" if turn.reported else "unavailable",
                "driver": self.driver.name,
                "turn_id": turn.turn_id,
                "turn_status": turn.status,
                "cached_input_tokens": turn.cached_input_tokens if turn.reported else 0,
                "reasoning_output_tokens": turn.reasoning_output_tokens if turn.reported else 0,
                "cost_source": "not_reported",
            },
        )

    def _record_missing_usage(self):
        error_type = self.report.code or "usage_unreported"
        turns = self.report.turns
        if not turns:
            # The run never reached a turn: still one row, so it is visible.
            self._record_turn(TurnUsage(turn_id=""), error_type=error_type)
            return
        for turn in turns:
            if turn.turn_id not in self._recorded:
                self._record_turn(turn, error_type=error_type)
