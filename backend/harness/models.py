"""Harness repo allowlist (Phase 2 branch 1, brief §1).

`harness` is a new kernel app (ADR-002/ADR-008 decision 1): it may import
`core`, `workspaces`, `ai_core`, `audit`, `memory`; nothing imports `harness`
in this phase.

`HarnessRepo` is a deliberately separate allowlist from
`Workspace.profile.repos[]` (the firm-dossier context-injection metadata,
Phase 0/1) -- a repo only becomes harness-actionable through this explicit,
curator-gated registration, never derived from or wired to the dossier's
free-text JSON in either direction (brief §1).
"""

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel
from memory.models import Memory


class HarnessRepo(WorkspaceScopedModel, TimestampedModel):
    class Runner(models.TextChoices):
        # "First runner is the Mac" (ADR-008 decision 7): the owner's Mac
        # running its own Docker daemon, which branch 2's per-job container
        # relies on. A real dynamic Runner registry is a later-phase
        # concern (brief, out of scope) -- not invented speculatively here.
        MAC_LOCAL = "mac_local"

    name = models.CharField(max_length=255)
    git_url = models.CharField(max_length=2000)
    default_branch = models.CharField(max_length=255, default="main")
    runner = models.CharField(max_length=20, choices=Runner.choices, default=Runner.MAC_LOCAL)
    # A repo is inert until explicitly enabled -- never auto-enabled from
    # the dossier or any other source (brief §1).
    enabled = models.BooleanField(default=False)
    # Drives HarnessJob.sensitivity (branch 2/3): a repo covering sensitive
    # client work is flagged once, here, at registration time -- not
    # re-decided per job. No credentials field: clone auth (SSH key/PAT) is
    # resolved from env at run time (CLAUDE.md "secrets only via env"),
    # never stored on this row.
    sensitive = models.BooleanField(default=False)
    # Phase 3 branch 2 (brief §boundary): one opaque, curator-authored shell
    # command the write-mode wrapper runs as its OWN subprocess and derives
    # `passed` from its real exit code -- never a general-purpose test-runner
    # abstraction. Blank means "not configured for implementation jobs" --
    # `create_harness_implementation_job` refuses to create a job at all
    # for a repo with none set (fails closed, brief: "never silently skips
    # verification and proceeds anyway"). Discovery (`HarnessJob`) never
    # reads this field -- it doesn't run tests.
    test_command = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "enabled"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.workspace_id})"


class HarnessJob(WorkspaceScopedModel, TimestampedModel):
    """A single read-only discovery run against a `HarnessRepo` (Phase 2
    branch 2, brief §2). No write, no branch push, no deploy -- only
    `record_external_usage()` (ai_core) and `record_work_log()` (memory,
    branch 3) are ever called as a result of a job.
    """

    class Agent(models.TextChoices):
        # O-2 (brief, resolved): Claude Code's headless mode is documented
        # and supported -- the first and default agent. CODEX_CLI is only
        # ever selected once this branch's own O-2 vendor-terms
        # verification clears it (brief, R6).
        CLAUDE_CODE = "claude_code", "Claude Code"
        CODEX_CLI = "codex_cli", "Codex CLI"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class FailureReason(models.TextChoices):
        TIMEOUT = "timeout"
        RUNNER_ERROR = "runner_error"
        BUDGET_EXCEEDED = "budget_exceeded"
        KILLED = "killed"

    repo = models.ForeignKey(HarnessRepo, on_delete=models.PROTECT, related_name="jobs")
    goal = models.TextField()
    agent = models.CharField(max_length=20, choices=Agent.choices, default=Agent.CLAUDE_CODE)
    # Snapshotted from `repo.sensitive` at creation time via a fixed
    # mapping (branch 3: True -> private, False -> normal) and never
    # re-derived afterward -- a job carries the sensitivity that was true
    # of its repo *when it ran*, immutable from then on (brief branch 3).
    # `Memory.Sensitivity`'s exact string vocabulary, not a bool, so
    # `record_work_log(..., sensitivity=job.sensitivity)` can pass it
    # straight through.
    sensitivity = models.CharField(
        max_length=20, choices=Memory.Sensitivity.choices, default=Memory.Sensitivity.NORMAL
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(
        max_length=20, choices=FailureReason.choices, blank=True, default=""
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="harness_jobs_created",
    )
    # Runtime brake (brief §2, brake #1): `started_at` backs
    # HARNESS_MAX_JOB_SECONDS and the recovery scan's staleness window;
    # `elapsed_seconds` is recorded at finalization for observability
    # (how long the job actually ran), not itself read by the brake.
    started_at = models.DateTimeField(null=True, blank=True)
    elapsed_seconds = models.PositiveIntegerField(default=0)
    # The launched Docker container's id (brief, Codex round-3/4 fixes: the
    # durable, killable, recoverable unit is the container, not a bare
    # subprocess pid -- the containment mechanism and the cancellation
    # mechanism must agree on what this is). Empty until the container is
    # actually launched.
    container_id = models.CharField(max_length=128, blank=True, default="")
    # Kill switch (brief §2, brake #3): a fast, fenced flag flip only --
    # the web request that cancels a job never itself touches Docker; the
    # Celery task that owns the container polls this and performs the
    # actual teardown (Codex round-3/4 fixes pin this precisely).
    cancel_requested = models.BooleanField(default=False)
    # Branch 3 (brief §3): `summary`/`findings` are produced by an
    # in-process gateway call over the fenced `raw_transcript` -- never
    # the CLI agent's own unfenced self-summary. `raw_transcript` (the
    # CLI's actual output, verbatim) is bounded to a fixed character cap
    # at write time (harness.execution's own truncation, mirroring the
    # firm-dossier block's bounded-line discipline, Phase 1 branch 3) and
    # readable only by the same curator roles gating the rest of this
    # app -- never a wider audience, and never itself sent to a future
    # gateway call without the same fencing applied fresh each time.
    # Defaults to `{}` (a job that never reached the briefing step, e.g.
    # cancelled/failed before summarization was attempted, has none).
    briefing = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def save(self, *args, **kwargs):
        # Same-workspace provenance invariant, the exact shape
        # `Memory.save()` already uses for its own analogous cross-FK
        # invariant (`memory/models.py:159`, Codex code-review HIGH,
        # branch 2 round 1/2): a job may only reference a repo in its own
        # workspace, or a cross-workspace repo id would let one workspace's
        # actor act against another workspace's registered repo.
        if self.repo_id is not None and self.repo.workspace_id != self.workspace_id:
            raise ValueError("HarnessJob.repo must belong to the job's own workspace.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"harness job {self.id} ({self.status})"


class HarnessImplementationJob(WorkspaceScopedModel, TimestampedModel):
    """A single write-mode implementation run against a `HarnessRepo`
    (Phase 3 branch 2, brief §boundary): Gate A (plan approval) -> isolated
    writable worktree -> CLI agent + wrapper-owned test run (branch 1's
    runner primitives) -> cross-vendor review (branch 3) -> Gate B (push
    approval) -> host-side push from a separate clean clone. A new model,
    not an extension of `HarnessJob` -- that model's own docstring is
    explicit that it is read-only-only, and its state machine/fields were
    designed for exactly that shape (brief §boundary).
    """

    class Agent(models.TextChoices):
        CLAUDE_CODE = "claude_code", "Claude Code"
        CODEX_CLI = "codex_cli", "Codex CLI"

    class Status(models.TextChoices):
        PENDING_GATE_A = "pending_gate_a"
        IMPLEMENTING = "implementing"
        PENDING_REVIEW = "pending_review"
        PENDING_GATE_B = "pending_gate_b"
        PUSHING = "pushing"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class FailureReason(models.TextChoices):
        TIMEOUT = "timeout"
        RUNNER_ERROR = "runner_error"
        BUDGET_EXCEEDED = "budget_exceeded"
        KILLED = "killed"
        GATE_A_REJECTED = "gate_a_rejected"
        GATE_B_REJECTED = "gate_b_rejected"
        GATE_EXPIRED = "gate_expired"
        # Test-outcome contract (brief §boundary) -- each a distinct,
        # deterministic reason a job never reaches Gate B, never a silent
        # "proceed anyway": a repo with no `test_command` configured, a
        # wrapper result the host couldn't trust (missing/malformed/absent
        # result.json), or one it trusted and it reported real failure.
        TEST_COMMAND_NOT_CONFIGURED = "test_command_not_configured"
        TEST_RESULT_UNAVAILABLE = "test_result_unavailable"
        TESTS_FAILED = "tests_failed"
        REVIEW_CHANGES_REQUESTED = "review_changes_requested"
        # Phase 3 branch 3: distinct from `review_changes_requested` --
        # that means review ran and produced a real `changes_requested`
        # verdict; this means the review container/gateway call itself
        # never produced a trustworthy verdict at all (launch failure,
        # unparseable gateway response, timeout). Same fail-closed
        # precedent as `test_result_unavailable`: no Gate B request for
        # a job whose review outcome the host couldn't actually confirm.
        REVIEW_UNAVAILABLE = "review_unavailable"
        WORKTREE_EXPIRED = "worktree_expired"
        PUSH_FAILED = "push_failed"
        # Phase 3 branch 5 (2026-09-27 audit): the produced tree equals
        # the base tree -- nothing to review or push, tests never run.
        NO_CHANGES = "no_changes"
        # Phase 3 branch 5: the produced diff contains the literal value of
        # a CLI API key the container had in its environment -- the job
        # fails before review, so the key is never bundled or pushed.
        SECRET_LEAK = "secret_leak"

    repo = models.ForeignKey(
        HarnessRepo, on_delete=models.PROTECT, related_name="implementation_jobs"
    )
    # Optional (brief §boundary, O-2 resolved by plan-review): Gate A's own
    # approval payload always carries the real goal/plan regardless of
    # whether a discovery run preceded it, so requiring the link is policy
    # friction, not a security boundary.
    discovery_job = models.ForeignKey(
        HarnessJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="implementation_jobs",
    )
    goal = models.TextField()
    agent = models.CharField(max_length=20, choices=Agent.choices, default=Agent.CLAUDE_CODE)
    # Same snapshot-at-creation discipline as `HarnessJob.sensitivity`.
    sensitivity = models.CharField(
        max_length=20, choices=Memory.Sensitivity.choices, default=Memory.Sensitivity.NORMAL
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_GATE_A)
    failure_reason = models.CharField(
        max_length=30, choices=FailureReason.choices, blank=True, default=""
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="harness_implementation_jobs_created",
    )
    cancel_requested = models.BooleanField(default=False)
    # Brake shape mirroring `HarnessJob` -- covers whichever phase currently
    # has a live container (the CLI+test run; branch 3's own review
    # container is tracked on `HarnessReview` instead, not here).
    started_at = models.DateTimeField(null=True, blank=True)
    elapsed_seconds = models.PositiveIntegerField(default=0)
    container_id = models.CharField(max_length=128, blank=True, default="")
    # OneToOneField, string label (mirrors `AgentStep.approval_request` --
    # `agents/models.py:163` -- exactly): two distinct gates, two distinct
    # `ApprovalRequest` rows, never conflated or reused (brief §boundary --
    # "an approved Gate A does not imply Gate B"). Nullable until each is
    # actually requested.
    gate_a_request = models.OneToOneField(
        "approvals.ApprovalRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="harness_implementation_job_gate_a",
    )
    gate_b_request = models.OneToOneField(
        "approvals.ApprovalRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="harness_implementation_job_gate_b",
    )
    # Snapshotted from `repo.git_url`/`repo.default_branch` at creation
    # time, in `save()` -- NEVER re-read live from `self.repo.git_url`
    # anywhere execution code needs the remote (Codex code-review P1,
    # branch 2 round 4). `create_harness_implementation_job`'s
    # self-modification hard-block (`is_self_repo`) only ever runs ONCE,
    # at creation. `HarnessRepo.git_url` is an editable field
    # (`edit_repo`) -- a job's own FK is live, so reading `job.repo.
    # git_url` at clone/push time (potentially hours later, across the
    # whole Gate A/implementation/review/Gate B wait) would silently pick
    # up whatever the row says NOW, letting a curator edit an
    # already-approved job's target repo to Jarvis's own remote after the
    # one-time guard already passed, with no second check anywhere to
    # catch it. Snapshotting makes the job permanently bound to what was
    # actually validated, the same immutability principle already applied
    # to `base_commit_sha` (a resolved SHA, never a mutable ref) one field
    # below.
    repo_git_url = models.CharField(max_length=2000, blank=True, default="")
    repo_default_branch = models.CharField(max_length=255, blank=True, default="")
    # Same snapshot rationale, one field over (Codex code-review P2,
    # branch 2 round 6): `create_harness_implementation_job`'s
    # non-empty/whitespace validation of `HarnessRepo.test_command` also
    # only ever runs once, at creation. `test_command` is likewise
    # editable (`edit_repo`) -- a curator editing it to blank/whitespace
    # AFTER a job is already approved and running would otherwise reach
    # the wrapper as `--test-command "   "`, which a shell runs as a
    # no-op that exits 0, letting the job advance to review without
    # actually verifying anything.
    repo_test_command = models.TextField(blank=True, default="")
    # `harness/p3-<job-id>` (brief §boundary, O-3 resolved: job-id-keyed
    # uniqueness is sufficient for this phase). Set once, in `save()`,
    # from `self.id` -- already generated client-side by `TimestampedModel`'s
    # `default=uuid.uuid4` before the first save, so this needs no separate
    # two-phase creation step.
    branch_name = models.CharField(max_length=255, blank=True, default="")
    base_commit_sha = models.CharField(max_length=64, blank=True, default="")
    produced_commit_sha = models.CharField(max_length=64, blank=True, default="")
    # Gateway-produced (brief §boundary: "the identical gateway-summarization
    # call" `HarnessReview`'s own `summary` uses) -- the CLI's own account of
    # what it changed is exactly as much low-trust content as its own
    # account of what it found.
    diff_summary = models.TextField(blank=True, default="")
    changed_files = models.JSONField(default=list, blank=True)
    # Wrapper-owned result (brief §boundary): `{"ran": bool, "exit_code":
    # int, "passed": bool, "command": str}` -- the wrapper's own dedicated
    # result file, never the CLI agent's self-report.
    test_result = models.JSONField(default=dict, blank=True)
    # Worktree lifetime across the Gate-B wait (brief §boundary, round-1
    # HIGH finding): must survive from implementation completion through
    # however long the owner takes to decide Gate B, potentially across a
    # worker restart -- persisted here, not held in a Celery task's local
    # variables. `worktree_expires_at` bounds how long an unresolved
    # worktree is kept; the recovery scan (branch 4) sweeps past it.
    worktree_path = models.CharField(max_length=1000, blank=True, default="")
    worktree_expires_at = models.DateTimeField(null=True, blank=True)
    push_result = models.JSONField(default=dict, blank=True)
    # The implementing CLI's own output (Phase 3 branch 6): read only from
    # the wrapper's post-quiescence frame, bounded at write time, stored
    # unmutated for audit/debugging. LOW-TRUST content -- the UI renders it
    # fenced behind a disclosure, never as Jarvis's own words (R2).
    raw_transcript = models.TextField(blank=True, default="")
    # The claim fence for `run_harness_push_job` (Codex code-review P1,
    # branch 2 round 3): a Celery redelivery, or `dispatch()` firing
    # twice while the row is still `pushing`, would otherwise let two
    # workers both pass an unfenced `status=pushing` read and race
    # through the irreversible `git push` together. Set unconditionally
    # by the claim itself and never reset, so it is only ever null for a
    # row nothing has claimed yet -- mirrors `started_at`'s identical
    # claim-fencing role for the implementation phase, one field over
    # (kept separate rather than reused, since `started_at`/
    # `elapsed_seconds` already belong to that earlier phase's own
    # timing and must not be overwritten by this later one).
    push_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def save(self, *args, **kwargs):
        # Same-workspace provenance invariant as `HarnessJob.save()` above,
        # for every FK this model carries.
        if self.repo_id is not None and self.repo.workspace_id != self.workspace_id:
            raise ValueError(
                "HarnessImplementationJob.repo must belong to the job's own workspace."
            )
        if (
            self.discovery_job_id is not None
            and self.discovery_job.workspace_id != self.workspace_id
        ):
            raise ValueError(
                "HarnessImplementationJob.discovery_job must belong to the job's own workspace."
            )
        # Snapshot once, if blank (Codex code-review P1, branch 2 round
        # 4) -- see `repo_git_url`'s own field comment for the full
        # rationale. Same once-if-blank shape as `branch_name` below.
        if not self.repo_git_url and self.repo_id is not None:
            self.repo_git_url = self.repo.git_url
            self.repo_default_branch = self.repo.default_branch
            self.repo_test_command = self.repo.test_command
        if not self.branch_name:
            self.branch_name = f"harness/p3-{self.id}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"harness implementation job {self.id} ({self.status})"


class HarnessReview(WorkspaceScopedModel, TimestampedModel):
    """One cross-vendor review pass over a `HarnessImplementationJob`'s
    produced commit (Phase 3 branch 2 model; branch 3 wires the real
    container-launched review call -- this branch's own dispatch call site
    is stubbed/mocked in tests, brief §boundary). Not a repurposed
    `HarnessJob` -- there is no existing review-verdict model anywhere in
    the codebase to reuse (confirmed during the brief's research pass).

    `agent` must differ from the reviewed job's own `agent` -- the
    mechanical cross-vendor-review enforcement ADR-008 decision 3
    requires. Enforced at the service layer (wherever a row here is
    created), deliberately NOT in `save()` -- brief §boundary is explicit
    that this is a service-layer rule, not a model-level invariant.
    """

    class Agent(models.TextChoices):
        CLAUDE_CODE = "claude_code", "Claude Code"
        CODEX_CLI = "codex_cli", "Codex CLI"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    class Verdict(models.TextChoices):
        APPROVED = "approved"
        CHANGES_REQUESTED = "changes_requested"

    implementation_job = models.ForeignKey(
        HarnessImplementationJob, on_delete=models.CASCADE, related_name="reviews"
    )
    agent = models.CharField(max_length=20, choices=Agent.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    verdict = models.CharField(max_length=20, choices=Verdict.choices, blank=True, default="")
    # Same fencing/gateway-summarization discipline as `HarnessJob.briefing`
    # (brief §boundary) -- content/fencing is what's reused from Phase 2
    # branch 3, not its single-JSONField DB shape; separate typed fields
    # read more naturally for a model whose whole purpose is these three
    # pieces of content (brief §boundary, round-1 LOW correction).
    summary = models.TextField(blank=True, default="")
    findings = models.JSONField(default=list, blank=True)
    raw_transcript = models.TextField(blank=True, default="")
    container_id = models.CharField(max_length=128, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    elapsed_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def save(self, *args, **kwargs):
        if (
            self.implementation_job_id is not None
            and self.implementation_job.workspace_id != self.workspace_id
        ):
            raise ValueError(
                "HarnessReview.implementation_job must belong to the review's own workspace."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"harness review {self.id} ({self.status})"
