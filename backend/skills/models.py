"""SKILL.md instruction skills (Phase 12 D13/D14, ADR-016 decision 4).

MVP 8's plan templates (`skills.catalog`) stay a code-only registry: they
name tools and steps (capability). Instruction skills are rows because
they grant nothing -- they cannot name a tool to run, add an agent step or
change a permission; what they change (the model's instructions) sits
behind the owner's per-version approval, by content hash.

- `InstructionSkill`: one per (workspace, name). `status` is the
  lifecycle (`proposed` -> `active` | `rejected`; `active` <-> `stale`;
  `active`/`stale` -> `archived` -> `active`); `current_version` is the
  approved version in use. Never deleted.
- `InstructionSkillVersion`: **immutable after insert** -- front matter,
  body, references and `content_sha256` never change; the decision fields
  are written once (`pending` -> `approved` | `rejected` | `superseded`)
  through `save(update_fields=...)`. Any other `save()` of an existing
  row, and every `delete()`, raises. An edit is a new version.
"""

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class VersionImmutable(Exception):
    """An approved (or any stored) version's content cannot change."""


class InstructionSkill(WorkspaceScopedModel, TimestampedModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed"
        ACTIVE = "active"
        STALE = "stale"
        REJECTED = "rejected"
        ARCHIVED = "archived"

    class Origin(models.TextChoices):
        OWNER = "owner"
        UPLOAD = "upload"
        CURATOR = "curator"
        PLAYBOOK = "playbook"

    name = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    origin = models.CharField(max_length=16, choices=Origin.choices)
    current_version = models.ForeignKey(
        "skills.InstructionSkillVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="skills_instruction_skill_unique_name"
            )
        ]
        indexes = [models.Index(fields=["workspace", "status"])]

    def delete(self, *args, **kwargs):
        raise VersionImmutable("Instruction skills are archived, never deleted.")

    def __str__(self):
        return self.name


class InstructionSkillVersion(WorkspaceScopedModel, TimestampedModel):
    class ProposedBy(models.TextChoices):
        OWNER = "owner"
        CURATOR = "curator"
        PLAYBOOK = "playbook"

    class Decision(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        SUPERSEDED = "superseded"

    # The only fields a stored row may change, once, from `pending`.
    DECISION_FIELDS = frozenset({"decision", "decided_by", "decided_at", "updated_at"})

    skill = models.ForeignKey(InstructionSkill, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    front_matter = models.JSONField(default=dict)
    body = models.TextField()
    references = models.JSONField(default=dict, blank=True)
    content_sha256 = models.CharField(max_length=64)
    proposed_by = models.CharField(max_length=16, choices=ProposedBy.choices)
    provenance = models.JSONField(default=dict, blank=True)
    decision = models.CharField(max_length=16, choices=Decision.choices, default=Decision.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        ordering = ["skill", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "version"], name="skills_instruction_version_unique"
            )
        ]
        indexes = [models.Index(fields=["workspace", "decision"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.DECISION_FIELDS:
                raise VersionImmutable("A stored skill version never changes; propose a new one.")
            stored = (
                type(self).objects.filter(pk=self.pk).values_list("decision", flat=True).first()
            )
            if stored != self.Decision.PENDING:
                raise VersionImmutable("A version is decided once.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VersionImmutable("Skill versions are never deleted.")

    @property
    def name(self):
        return self.front_matter.get("name", "")

    @property
    def description(self):
        return self.front_matter.get("description", "")

    def __str__(self):
        return f"{self.skill_id} v{self.version}"


class SkillCuratorRun(WorkspaceScopedModel, TimestampedModel):
    """One weekly curator pass over a workspace (Phase 12 D16): at most one
    per workspace per ISO week (`week`, e.g. `2026-W47`).

    Bookkeeping only: counts, the outcome, the one `skills.curate`
    `ModelUsage` id, and the archive **suggestions** (never applied -- the
    owner clicks Archive on `/skills`, or dismisses the suggestion). Each
    suggestion is `{"skill_id", "reason", "dismissed"}` (+ `"into_skill_id"`
    for a merge), where `reason` is a closed token (`curator.ARCHIVE_REASONS`);
    no model text is stored here. Edit and merge proposals are
    `InstructionSkillVersion` rows (`proposed_by=curator`) whose
    `provenance` names this run."""

    class Outcome(models.TextChoices):
        RUNNING = "running"
        PROPOSALS = "proposals"
        QUIET = "quiet"
        NO_SKILLS = "no_skills"
        SKIPPED_CAP = "skipped_cap"
        SKIPPED_BUDGET = "skipped_budget"
        NO_PROMPT = "no_prompt"
        NO_PROVIDER = "no_provider"
        GATEWAY_ERROR = "gateway_error"
        PARSE_ERROR = "parse_error"
        DENIED = "denied"
        FAILED = "failed"

    week = models.CharField(max_length=10)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.RUNNING)
    stale_marked = models.PositiveIntegerField(default=0)
    reactivated = models.PositiveIntegerField(default=0)
    skills_considered = models.PositiveIntegerField(default=0)
    proposals_created = models.PositiveIntegerField(default=0)
    proposals_dropped = models.PositiveIntegerField(default=0)
    drop_reasons = models.JSONField(default=dict, blank=True)
    proposal_version_ids = models.JSONField(default=list, blank=True)
    archive_suggestions = models.JSONField(default=list, blank=True)
    denials = models.PositiveIntegerField(default=0)
    model_usage_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "week"], name="skills_curator_run_once_per_week"
            )
        ]

    def __str__(self):
        return f"curator {self.workspace_id} {self.week} {self.outcome}"

    def open_suggestions(self):
        return [s for s in (self.archive_suggestions or []) if not s.get("dismissed")]


class PlaybookDistillRun(WorkspaceScopedModel, TimestampedModel):
    """One distillation attempt for one repo of one workspace (Phase 12
    D17): what happened and why, counts only (never content). A dropped
    draft is `dropped` with its reason; a skipped repo (weekly cap, pending
    cap, budget, no provider) is recorded too, so "dropped and counted" is
    visible."""

    class Outcome(models.TextChoices):
        PROPOSED = "proposed"
        DROPPED = "dropped"
        SKIPPED_CAP = "skipped_cap"
        SKIPPED_PENDING = "skipped_pending"
        BUDGET_REFUSED = "budget_refused"
        NO_PROVIDER = "no_provider"
        NO_PROMPT = "no_prompt"
        MODEL_ERROR = "model_error"
        DENIED = "denied"
        FAILED = "failed"

    repo_key = models.CharField(max_length=64)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    reason = models.CharField(max_length=40, blank=True, default="")
    counts = models.JSONField(default=dict, blank=True)
    model_usage_id = models.UUIDField(null=True, blank=True)
    version = models.ForeignKey(
        InstructionSkillVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["workspace", "outcome", "created_at"])]

    def __str__(self):
        return f"{self.repo_key} {self.outcome}"


class PlaybookSource(WorkspaceScopedModel, TimestampedModel):
    """One successful, reviewed harness implementation job, recorded as
    distillation input (Phase 12 D17, ADR-012 note).

    Recorded by `skills.ports.record_playbook_source` from `harness` only
    when the job succeeded, its review approved it, its tests passed and
    the owner typed its goal; a sensitive repo records nothing. Not an
    episode: it never reaches memory.

    Quotable fields (a playbook step's verified `quote`) are only `goal`
    (the owner's own words) and `test_command` (the job's snapshot of the
    owner-configured `HarnessRepo.test_command`). `changed_paths` are
    agent-chosen strings (hints, shown fenced, never a verified quote);
    `context_text` is model-produced (diff and review summaries, fenced as
    untrusted, never a quote). No file content is ever stored.

    `distill_run` marks the source used: a distillation that got an answer
    from the model consumes its input sources, whatever the outcome.
    """

    job_id = models.UUIDField()
    repo_key = models.CharField(max_length=64)
    repo_label = models.CharField(max_length=200)
    goal = models.TextField()
    test_command = models.TextField()
    changed_paths = models.JSONField(default=list, blank=True)
    test_summary = models.JSONField(default=dict, blank=True)
    context_text = models.TextField(blank=True, default="")
    distill_run = models.ForeignKey(
        PlaybookDistillRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sources",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "job_id"], name="skills_playbook_source_unique_job"
            )
        ]
        indexes = [models.Index(fields=["workspace", "repo_key", "distill_run"])]

    def __str__(self):
        return f"{self.repo_label} {self.job_id}"
