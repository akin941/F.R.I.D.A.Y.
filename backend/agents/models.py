"""Agent runtime: goal -> multi-step plan -> execution (MVP 7 §1, brief).

`agents` is a new one-way dependency root: it may import `tools`, `approvals`,
`tasks`, `memory`, `knowledge` (to call their existing public service
functions from the execution engine, branch 3+), but none of those apps may
import `agents` -- enforced by import-linter contracts (pyproject.toml).
`agents` does not import `assistant` and is not imported by it: the router
(MVP 6) and the agent runtime (this MVP) are parallel front doors onto the
same gated services, not layered on each other.

This branch ships the model layer and the plan-creation bound only -- no
dispatch. Creating an `Agent`/its `AgentStep` rows here never calls
`approvals.services.create_request`, `tasks.services.create_task`, or any
tool; that's the execution engine (branch 3).

`AgentStep` is intentionally *not* workspace-scoped on its own row (no
denormalized `workspace` field) -- unlike `KnowledgeChunk`, it is never
queried independently of its `Agent`; every access scopes through
`agent__workspace__members__user=actor`, so there is no divergence risk to
guard against. `AgentStepQuerySet.for_actor` (below) is the one sanctioned
way to do that scoping, mirroring `core.db.WorkspaceScopedQuerySet.for_actor`
one join deeper -- future direct-step endpoints (branch 3's confirm
endpoint, branch 5's UI) must go through it rather than hand-rolling the
join each time (Codex round-1 LOW fix)."""

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class Agent(WorkspaceScopedModel, TimestampedModel):
    class Status(models.TextChoices):
        PLANNING = "planning"
        RUNNING = "running"
        BLOCKED_ON_APPROVAL = "blocked_on_approval"
        BLOCKED_ON_CONFIRMATION = "blocked_on_confirmation"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class FailureReason(models.TextChoices):
        """Closed vocabulary (Codex round-1 MEDIUM fix): a `failed` Agent
        with no recorded reason is indistinguishable from any other failure
        later, contrary to brief §1's "failed with a user-visible reason".
        Set only alongside `status=FAILED`; blank otherwise."""

        PLAN_TOO_LARGE = "plan_too_large"
        PLAN_EMPTY = "plan_empty"
        # Set by the planner orchestration (branch 2, agents/planner_
        # service.py) when the `purpose="agent.plan"` gateway call itself
        # fails, its output doesn't parse, or it names an unregistered
        # tool -- distinct from PLAN_TOO_LARGE/PLAN_EMPTY, which both mean
        # "the planner returned a well-shaped step list, but its size was
        # rejected." This one means "no usable step list was ever produced."
        PLANNER_ERROR = "planner_error"
        # Consecutive-failure brake (branch 3, brief §3 brake #2b): the
        # execution engine halted after AGENT_MAX_CONSECUTIVE_FAILURES
        # steps failed in a row, zero internal retries.
        CONSECUTIVE_FAILURES = "consecutive_failures"
        # Resource/token budget brake (branch 3, brief §3 brake #2c): a new
        # read step would start at/over AGENT_MAX_GATEWAY_CALLS.
        GATEWAY_BUDGET_EXCEEDED = "gateway_budget_exceeded"

    goal = models.TextField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PLANNING)
    failure_reason = models.CharField(
        max_length=30, choices=FailureReason.choices, blank=True, default=""
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agents_created",
    )
    # Resource/token budget brake (brief §3): a per-Agent running total of
    # gateway calls the execution engine's read steps have made. Local to
    # this row (not derived from ModelUsage) so concurrent agents' usage can
    # never cross-contaminate.
    gateway_calls_used = models.PositiveIntegerField(default=0)
    # Consecutive-failure brake (branch 3, brief §3 brake #2b): reset to 0
    # on any step that resolves cleanly (succeeded or skipped), incremented
    # on any step that fails (including a timed-out one). Zero internal
    # retries -- a step fails once and counts toward this, full stop.
    consecutive_failures = models.PositiveIntegerField(default=0)
    # Skill-selection audit trail (MVP 8 §6, brief): set only when this
    # Agent's plan was produced by a matched skill template rather than a
    # freeform `agent.plan` gateway call -- both blank for every agent
    # created before this MVP shipped (no backfill needed) and for any
    # agent that fell through to freeform planning. `skill_content_hash` is
    # the `SkillSpec.content_hash` AT THE MOMENT this agent used it, copied
    # onto the row at plan-creation time rather than looked up from the
    # live catalog later -- mirrors a common `prompt_key`+`prompt_version`
    # usage-record pattern: "why did this agent behave this way" stays
    # answerable from the `Agent` row itself even after a later deploy
    # changes the skill's own template (and therefore its content_hash).
    skill_name = models.CharField(max_length=100, blank=True, default="")
    skill_content_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"agent {self.id} ({self.status})"


class AgentStepQuerySet(models.QuerySet):
    """Mirrors `core.db.WorkspaceScopedQuerySet.for_actor` one join deeper,
    since `AgentStep` has no `workspace` field of its own (see the module
    docstring)."""

    def for_actor(self, actor, workspace=None):
        if actor is None or not getattr(actor, "is_authenticated", False):
            return self.none()
        qs = self.filter(agent__workspace__members__user=actor)
        if workspace is not None:
            qs = qs.filter(agent__workspace=workspace)
        return qs.distinct()


class AgentStep(TimestampedModel):
    class Kind(models.TextChoices):
        READ = "read"
        TOOL_REQUEST = "tool_request"
        TASK_DRAFT = "task_draft"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        BLOCKED_ON_APPROVAL = "blocked_on_approval"
        BLOCKED_ON_CONFIRMATION = "blocked_on_confirmation"
        SKIPPED = "skipped"

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    # The planner's own short natural-language description of what this
    # step does (MVP 7 §2) -- e.g. what a `read` step's summarization call
    # is summarizing *against* (agents/planner_service.py::run_read_step,
    # branch 3). Set once at plan-creation time, same immutability rule as
    # `kind`/`payload` above.
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    # Opaque, kind-specific arguments (mirrors ApprovalRequest.payload) --
    # never used to resolve scope; that always comes from `agent.workspace`.
    # Set once at plan-creation time (§2) and never mutated by the execution
    # engine (§3, §4's self-modification guarantee) -- only `status`/
    # `result`/`approval_request` change during execution.
    payload = models.JSONField(default=dict, blank=True)
    # Set only for tool_request-kind steps once the engine creates one
    # (branch 3). String label: agents may import approvals directly (the
    # one-way direction this MVP establishes), but every cross-app FK in
    # this codebase uses a string label regardless of direction, avoiding
    # app-loading-order coupling -- mirrors tools.VaultExportRun's
    # "approvals.ApprovalRequest" reference.
    approval_request = models.OneToOneField(
        "approvals.ApprovalRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agent_step",
    )
    # Small summary, not a full transcript (e.g. a read step's retrieved
    # summary, or a rejection reason) -- written by the execution engine.
    result = models.JSONField(default=dict, blank=True)

    objects = AgentStepQuerySet.as_manager()

    class Meta:
        ordering = ["agent", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "order"], name="agents_agentstep_unique_order"
            ),
        ]
        indexes = [
            models.Index(fields=["agent", "status"]),
        ]

    def __str__(self):
        return f"agent step {self.id} ({self.kind}, {self.status})"
