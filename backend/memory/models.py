"""Curated memory store (ADR-005).

Postgres is the source of truth; memories are workspace-scoped, auditable,
editable, forgettable and exportable. AI-suggested memories are born
`pending_approval` and only reach model context after human approval. The
one deliberately narrow exception (ADR-008 k.4, Phase 1 p1-work-log):
`source=system_generated` rows are born `approved` — machine-authored
activity summaries, not conversation-derived content a human hasn't
reviewed. See `Source.SYSTEM_GENERATED` and
`memory.services.record_work_log()`, its only creation path.
Scope (personal vs company) is carried by the workspace row itself — a
personal workspace's memories are personal memories; there is no separate
scope field.
"""

import hashlib
import re

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from pgvector.django import VectorField

from core.db import WorkspaceScopedModel, WorkspaceScopedQuerySet
from core.models import TimestampedModel

_WHITESPACE = re.compile(r"\s+")


def compute_fingerprint(content: str) -> str:
    """Cheap textual dedup key: lowercase, collapsed whitespace, sha256.

    No embeddings — semantic dedup is MVP 3. The fingerprint is unique per
    workspace across *all* rows (including rejected ones, so a rejected
    suggestion cannot immediately resurface); the slot frees only on hard
    delete.
    """
    normalized = _WHITESPACE.sub(" ", content.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()


class MemoryQuerySet(WorkspaceScopedQuerySet):
    def cloud_eligible(self):
        """The only sanctioned path by which Memory rows may enter a gateway
        payload (ADR-005: `private` records never reach cloud models).
        Private title/content must never be read out of any other queryset
        into model context, extraction prompts, context reports or
        `memories_used`.
        """
        return self.filter(status=Memory.Status.APPROVED, sensitivity=Memory.Sensitivity.NORMAL)


class MemoryManager(models.Manager.from_queryset(MemoryQuerySet)):
    pass


class Memory(WorkspaceScopedModel, TimestampedModel):
    class Type(models.TextChoices):
        SEMANTIC = "semantic"
        EPISODIC = "episodic"
        PREFERENCE = "preference"
        PROCEDURAL = "procedural"
        # System-generated activity summaries (ADR-008 k.4, Phase 1
        # p1-work-log) — auto-approved ONLY when paired with
        # `source=system_generated` (see `Source` below and
        # `memory.services.record_work_log()`, the sole creation path for
        # that pairing). A conversation-derived work_log candidate carries
        # this same type but `source=ai_suggested`, and stays
        # `pending_approval` like any other extraction candidate.
        WORK_LOG = "work_log"

    class Sensitivity(models.TextChoices):
        NORMAL = "normal"
        PRIVATE = "private"

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval"
        APPROVED = "approved"
        REJECTED = "rejected"
        ARCHIVED = "archived"

    class Source(models.TextChoices):
        AI_SUGGESTED = "ai_suggested"
        USER_CREATED = "user_created"
        # Machine-provenance rows (Phase 1 p1-work-log, ADR-008 k.4) — only
        # `memory.services.record_work_log()` ever writes this value, and it
        # always pairs it with `type=work_log` and `status=approved`. This
        # is the ONLY auto-approved provenance; no other Source value may
        # ever be born approved.
        SYSTEM_GENERATED = "system_generated"

    memory_type = models.CharField(max_length=20, choices=Type.choices)
    sensitivity = models.CharField(
        max_length=20, choices=Sensitivity.choices, default=Sensitivity.NORMAL
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    source = models.CharField(max_length=20, choices=Source.choices)
    title = models.CharField(max_length=255)
    content = models.TextField()
    fingerprint = models.CharField(max_length=64, editable=False)
    # Provenance, not an ACL owner. It feeds exactly one authorization
    # predicate (members manage `source=user_created AND created_by=actor`
    # rows); AI-suggested rows are admin-managed at every stage.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories_created",
    )
    # String label: no Python import of assistant (dependency direction is
    # assistant → memory once context injection lands).
    source_conversation = models.ForeignKey(
        "assistant.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories_extracted",
    )
    # Durable marker (Phase 1 p1-work-log, Codex round-3 P2): set once,
    # permanently, by memory.services.privatize_conversation_memories()
    # when it archives an approved memory as part of the private-
    # conversation cascade. `restore_memory()` checks THIS, not
    # `source_conversation.private` — the FK above is nullable
    # (on_delete=SET_NULL), so deleting the source conversation would
    # silently defeat an FK-based check and let the row be restored back
    # into cloud_eligible(), undoing the privacy decision. Never cleared
    # once set; there is no path back from it.
    privacy_locked = models.BooleanField(default=False)
    # Learned-memory provenance (Phase 8A, ADR-012). `ingestion_source` is
    # how forget-source finds every memory derived from a source, even after
    # its episodes have expired (TTL, d10) -- hence `source_episode_id` is a
    # plain UUID, not a foreign key. `provenance_quote` is the verbatim
    # owner-authored text the proposal was checked against (D9), bounded.
    ingestion_source = models.ForeignKey(
        "memory.IngestionSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="derived_memories",
    )
    source_episode_id = models.UUIDField(null=True, blank=True)
    provenance_quote = models.CharField(max_length=300, blank=True, default="")
    # Forget-source cascade marker (Phase 8A branch 5, D11): the forget
    # operation that archived/rejected this row and the status it had
    # before, so `restore_source` undoes exactly that operation's rows.
    forget_operation_id = models.UUIDField(null=True, blank=True)
    forget_prior_status = models.CharField(max_length=20, blank=True, default="")
    # Semantic retrieval (Phase 1 branch p1-memory-embeddings). No fixed
    # `dimensions` kwarg (MVP 3 §1 vector dimension policy, mirrored from
    # `knowledge.KnowledgeChunk.embedding`) — comparison operators only
    # require the two operand vectors to match each other's dimension at
    # query time, not the column's declared dimension. Eligibility is
    # `cloud_eligible()` only (status=approved, sensitivity=normal); a
    # private or unapproved row's embedding is always `None` — see
    # `memory.services`'s embedding-lifecycle reconciliation, the only
    # sanctioned writer of these five fields.
    embedding = VectorField(null=True, blank=True)
    embedding_provider = models.ForeignKey(
        "ai_core.ModelProvider", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Lease-fencing: one embedding attempt in flight at a time per row (not
    # embed-exactly-once — a row can be legitimately re-embedded many times
    # over its life, see memory.services). Same field shape as
    # `knowledge.KnowledgeDocument`'s (document-level attempt, not a
    # separate per-range job model like `MemoryExtractionJob` — a memory
    # row's embedding target is always "this row's current title+content").
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Retryable embedding failures increment this; once it reaches
    # memory.jobs.MAX_EMBEDDING_RETRIES the row simply stays unembedded
    # (retrieval already falls back to deterministic ordering for a row
    # with no embedding — there is no separate terminal "failed" state to
    # reach, unlike KnowledgeDocument.status).
    retry_count = models.PositiveIntegerField(default=0)

    objects = MemoryManager()

    def save(self, *args, **kwargs):
        # Same-workspace provenance is an invariant, not a per-caller courtesy:
        # a memory may only reference a conversation in its own workspace, or a
        # cross-workspace conversation UUID would leak to this workspace's
        # readers. Enforced on every write path (user API today, extraction
        # pipeline in later branches).
        if (
            self.source_conversation_id is not None
            and self.source_conversation.workspace_id != self.workspace_id
        ):
            raise ValueError("source_conversation must belong to the memory's workspace.")
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "fingerprint"], name="unique_memory_fingerprint"
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "memory_type"]),
        ]
        verbose_name_plural = "memories"

    def __str__(self):
        return f"{self.memory_type} memory {self.id} ({self.status})"


class MemoryExtractionJob(WorkspaceScopedModel, TimestampedModel):
    """One distillation attempt over a contiguous, non-overlapping message
    range of a conversation (MVP 2 §3-4).

    Range allocation (branch 3, this model) and claiming/execution (branch 4)
    are deliberately split: this branch creates jobs as inert `pending` rows
    with no dispatch wired up yet, so merged `main` never publishes an
    unregistered Celery task. `claim_token`/`lease_expires_at` exist now so
    the branch-4 migration doesn't need a second schema change, but nothing
    in this branch reads or writes them.

    String FK labels (`assistant.Conversation`/`assistant.Message`): memory
    must not import assistant (one-way dependency, see pyproject contract).
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    conversation = models.ForeignKey(
        "assistant.Conversation", on_delete=models.CASCADE, related_name="extraction_jobs"
    )
    # Exclusive lower bound of the range (the prior cursor position); null
    # for a conversation's first job. on_delete=RESTRICT (see
    # Conversation.last_extracted_message for the full rationale): extraction
    # job history is meant to be as immutable as the audit log — a standalone
    # message delete must never silently widen or corrupt an
    # already-allocated range — but deleting the whole Conversation (which
    # cascades to this job via `conversation`) must still work.
    after_message = models.ForeignKey(
        "assistant.Message",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="+",
    )
    # Inclusive upper bound AND the job's target — there is no separate
    # "target_message" field, the range end IS the target.
    through_message = models.ForeignKey(
        "assistant.Message", on_delete=models.RESTRICT, related_name="+"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    # Lease fencing fields (branch 4): a per-attempt token + expiry so a
    # worker that outlives its lease can be safely superseded rather than
    # allowed to write stale results.
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=50, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "through_message"],
                name="unique_extraction_job_range_end",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"extraction job {self.id} ({self.status})"


class CoreMemoryBlock(WorkspaceScopedModel, TimestampedModel):
    """Always-in-prompt core memory (Phase 8A D7).

    One row per `(workspace, label)`. `self` lives in the owner's personal
    workspace and is written only by `memory.services.
    set_assembled_self_block` (the deterministic assembler, D8);
    `developer`/`preferences` are the owner's personal blocks (personal
    workspace only); `current_focus`/`active_projects` exist per workspace.
    Every content change appends a `CoreMemoryBlockVersion`; char budgets
    live in `memory.core_blocks`, the who-writes-what rules in
    `memory.services`.
    """

    class Label(models.TextChoices):
        SELF = "self"
        DEVELOPER = "developer"
        CURRENT_FOCUS = "current_focus"
        ACTIVE_PROJECTS = "active_projects"
        PREFERENCES = "preferences"

    class WriterKind(models.TextChoices):
        OWNER = "owner"
        ASSEMBLER = "assembler"
        APPROVED_PROPOSAL = "approved_proposal"

    label = models.CharField(max_length=32, choices=Label.choices)
    content = models.TextField(blank=True, default="")
    # sha256 of `content`: the assembler writes a new version only when it
    # changes (D8).
    content_hash = models.CharField(max_length=64, blank=True, default="")
    version = models.PositiveIntegerField(default=0)
    sensitivity = models.CharField(
        max_length=20, choices=Memory.Sensitivity.choices, default=Memory.Sensitivity.NORMAL
    )
    updated_by_kind = models.CharField(max_length=24, choices=WriterKind.choices)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "label"], name="unique_core_block_label")
        ]

    def __str__(self):
        return f"core block {self.label} v{self.version}"


class CoreMemoryBlockVersion(WorkspaceScopedModel, TimestampedModel):
    """Immutable history: one row per content version of a block (the
    current one included), so the owner can view and roll back (D7)."""

    block = models.ForeignKey(CoreMemoryBlock, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    content = models.TextField(blank=True, default="")
    content_hash = models.CharField(max_length=64)
    sensitivity = models.CharField(max_length=20, choices=Memory.Sensitivity.choices)
    updated_by_kind = models.CharField(max_length=24, choices=CoreMemoryBlock.WriterKind.choices)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["block", "version"], name="unique_core_block_version")
        ]

    def __str__(self):
        return f"core block {self.block_id} v{self.version}"


class CoreBlockProposal(WorkspaceScopedModel, TimestampedModel):
    """A model-made (`inferred=True`) proposed new content for a core block
    (D7, D9). Never applied without a per-item owner approval over the web
    channel; a proposal whose `base_version` is stale is refused at
    approval. The producer (nightly consolidation) lands in Phase 8A branch
    5, which also adds the `ingestion_source` FK (the `IngestionSource`
    table comes from branch 1); this branch ships the model and services.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    label = models.CharField(max_length=32, choices=CoreMemoryBlock.Label.choices)
    proposed_content = models.TextField()
    # Rendered unified line diff of base content -> proposed content.
    diff = models.TextField(blank=True, default="")
    base_version = models.PositiveIntegerField(default=0)
    provenance_quote = models.CharField(max_length=300)
    # Plain UUID, not an FK: the episode may expire (D2, d10).
    source_episode_id = models.UUIDField(null=True, blank=True)
    # The learned proposal's source (branch 5): forget-source finds and
    # rejects its pending proposals through this FK.
    ingestion_source = models.ForeignKey(
        "memory.IngestionSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="core_block_proposals",
    )
    forget_operation_id = models.UUIDField(null=True, blank=True)
    forget_prior_status = models.CharField(max_length=20, blank=True, default="")
    inferred = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["workspace", "status"])]

    def __str__(self):
        return f"core block proposal {self.label} ({self.status})"


# --- Continuous-learning ingestion (Phase 8A branch 1, ADR-012) -------------


def default_daily_token_budget():
    return settings.MEMORY_INGESTION_DEFAULT_DAILY_TOKEN_BUDGET


class IngestionSource(WorkspaceScopedModel, TimestampedModel):
    """A registered ingestion source (ADR-012 decision 1, brief D2). Scoped
    to the owner's personal workspace: one row per kind in 8A, created
    disabled by `seed_ingestion_sources`; workspace mapping rules route its
    items (D4). Nothing is ingested unless `enabled` and not paused."""

    class Kind(models.TextChoices):
        GIT = "git"
        HARNESS = "harness"
        CLAUDE_CODE_HOOKS = "claude_code_hooks"
        CALENDAR = "calendar"

    kind = models.CharField(max_length=40, choices=Kind.choices)
    enabled = models.BooleanField(default=False)
    paused_at = models.DateTimeField(null=True, blank=True)
    daily_token_budget = models.PositiveIntegerField(default=default_daily_token_budget)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # The last forget-source operation (D11): set by `forget_source`,
    # cleared by `restore_source`. Rows trashed by that operation carry the
    # same id, so restore undoes exactly them.
    forgotten_at = models.DateTimeField(null=True, blank=True)
    forget_operation_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "kind"], name="memory_ingestion_source_unique_kind"
            )
        ]

    def __str__(self):
        return f"{self.kind} ingestion source {self.id}"

    @property
    def is_accepting(self):
        return self.enabled and self.paused_at is None

    @property
    def is_personal_class(self):
        """Personal-class data (ADR-012 decision 11): extraction of its
        episodes runs with `personal_data=True` (branch 5)."""
        return self.kind == self.Kind.CALENDAR


class WorkspaceMappingRule(WorkspaceScopedModel, TimestampedModel):
    """Routes a source's items to exactly one workspace (ADR-012 decision 2,
    brief D4). Lives in the source's (personal) workspace; its target must
    be a workspace the owner owns (validated by the service and re-checked
    at resolve time). No rule matches -> the item is dropped, never
    defaulted."""

    class RuleType(models.TextChoices):
        REPO = "repo"  # exact HarnessRepo id string
        PATH_PREFIX = "path_prefix"  # longest normalised prefix wins
        CALENDAR_BINDING = "calendar_binding"  # exact binding id

    source = models.ForeignKey(
        IngestionSource, on_delete=models.CASCADE, related_name="mapping_rules"
    )
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    pattern = models.CharField(max_length=500)
    target_workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.CASCADE, related_name="+"
    )
    enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["rule_type", "pattern"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "rule_type", "pattern"],
                name="memory_mapping_rule_unique_pattern",
            )
        ]

    def __str__(self):
        return f"{self.rule_type} rule {self.id}"


class EpisodeQuerySet(WorkspaceScopedQuerySet):
    def live(self):
        return self.filter(deleted_at__isnull=True, expires_at__gt=timezone.now())


class LiveEpisodeManager(models.Manager.from_queryset(EpisodeQuerySet)):
    """Default manager: hides trashed (ADR-009) and expired (TTL, d10)
    episodes from every `for_actor()` consumer. The trash view, restore,
    TTL purge and `purge_trash` use `all_objects`."""

    def get_queryset(self):
        return super().get_queryset().live()


class AllEpisodesManager(models.Manager.from_queryset(EpisodeQuerySet)):
    pass


class Episode(WorkspaceScopedModel, TimestampedModel):
    """One scrubbed, short-lived raw item (brief D2), in the **target**
    workspace its mapping rule chose. Written only by
    `memory.ingestion.ports.record_episode`. `owner_text` is the only field
    extraction may quote (ADR-012 decision 7); `context_text` and `metadata`
    are fenced context. Texts are stored scrubbed and bounded; `scrub_report`
    holds counts per placeholder type, never the matched text."""

    MAX_TEXT_CHARS = 4000

    class Sensitivity(models.TextChoices):
        NORMAL = "normal"
        PRIVATE = "private"

    source = models.ForeignKey(IngestionSource, on_delete=models.CASCADE, related_name="episodes")
    kind = models.CharField(max_length=40)
    external_id = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    owner_text = models.TextField(blank=True, default="")
    context_text = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    sensitivity = models.CharField(
        max_length=20, choices=Sensitivity.choices, default=Sensitivity.NORMAL
    )
    scrub_report = models.JSONField(default=dict, blank=True)
    token_estimate = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True, blank=True)
    forget_operation_id = models.UUIDField(null=True, blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)

    objects = LiveEpisodeManager()
    all_objects = AllEpisodesManager()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        base_manager_name = "all_objects"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"], name="memory_episode_unique_external_id"
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "extracted_at"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["source", "-occurred_at"]),
        ]

    def __str__(self):
        return f"{self.kind} episode {self.id}"


class ScrubTerm(WorkspaceScopedModel, TimestampedModel):
    """An owner-maintained literal (customer name, project code name)
    replaced with `[TERM:<id>]` before storage in this workspace (D3)."""

    term = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["term"]
        constraints = [
            models.UniqueConstraint(Lower("term"), "workspace", name="memory_scrub_term_unique_ci")
        ]

    def __str__(self):
        return f"scrub term {self.id}"


class IngestionCounter(WorkspaceScopedModel, TimestampedModel):
    """Per source per day audit counters (D2, D11): counts, never content.
    Kept in the source's (personal) workspace -- a dropped item has no
    target workspace. `unmapped_keys` holds at most `UNMAPPED_KEYS_CAP`
    entries of `{hash, short}` (a sha256 of the mapping key plus a scrubbed
    short form), so the owner can add a rule."""

    UNMAPPED_KEYS_CAP = 20

    source = models.ForeignKey(IngestionSource, on_delete=models.CASCADE, related_name="counters")
    day = models.DateField()
    stored = models.PositiveIntegerField(default=0)
    duplicate = models.PositiveIntegerField(default=0)
    dropped_unmapped = models.PositiveIntegerField(default=0)
    dropped_paused = models.PositiveIntegerField(default=0)
    scrubber_hits = models.JSONField(default=dict, blank=True)
    unmapped_keys = models.JSONField(default=list, blank=True)
    tokens_spent = models.PositiveIntegerField(default=0)
    proposals_created = models.PositiveIntegerField(default=0)
    proposals_accepted = models.PositiveIntegerField(default=0)
    proposals_rejected = models.PositiveIntegerField(default=0)
    # Branch 5 (D9, D10): candidates dropped because their quote is not
    # verbatim owner text of the cited episode, and every other learning
    # drop/defer reason (`self_target`, `nightly_cap`, `weekly_cap`,
    # `duplicate`, `triage_short`, `deferred_budget`, ...) -> count.
    proposals_dropped_no_provenance = models.PositiveIntegerField(default=0)
    learning_drops = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-day"]
        constraints = [
            models.UniqueConstraint(fields=["source", "day"], name="memory_ingestion_counter_day")
        ]

    def __str__(self):
        return f"counter {self.source_id} {self.day}"


class CodingSession(WorkspaceScopedModel, TimestampedModel):
    """Read model of one coding-agent session (Phase 8A brief D12), upserted
    by the hook-event ingest (`memory.ingestion.hooks`) in the workspace the
    event's `cwd` mapped to (D4) -- never defaulted. Holds only scrubbed,
    bounded fields: the session id, a project label (the mapped directory's
    name), the status, timestamps and the last notification (tool/model
    text, <= 200 chars, fenced as untrusted when shown to a model).

    A session that moves between workspaces (a `cd` into another mapped
    tree) gets one row per workspace: firm events never update a personal
    row and vice versa. "Stale" (no event for
    `MEMORY_CODING_SESSION_STALE_HOURS`) is computed at read time. Rows of a
    forgotten source are hidden from the sessions API until it is restored;
    the TTL beat deletes rows idle past their workspace's episode TTL."""

    MAX_NOTIFICATION_CHARS = 200
    MAX_PROJECT_CHARS = 100

    class Tool(models.TextChoices):
        CLAUDE_CODE = "claude_code"

    class Status(models.TextChoices):
        RUNNING = "running"
        WAITING_FOR_OWNER = "waiting_for_owner"
        STOPPED = "stopped"

    source = models.ForeignKey(
        IngestionSource, on_delete=models.CASCADE, related_name="coding_sessions"
    )
    tool = models.CharField(max_length=20, choices=Tool.choices, default=Tool.CLAUDE_CODE)
    session_id = models.CharField(max_length=128)
    project = models.CharField(max_length=MAX_PROJECT_CHARS, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField()
    last_event_at = models.DateTimeField()
    last_event = models.CharField(max_length=40, blank=True, default="")
    last_notification = models.CharField(max_length=MAX_NOTIFICATION_CHARS, blank=True, default="")
    event_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-last_event_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "tool", "session_id"],
                name="memory_coding_session_unique",
            )
        ]
        indexes = [models.Index(fields=["workspace", "-last_event_at"])]

    def __str__(self):
        return f"{self.tool} session {self.id}"


class EpisodeExtractionJob(WorkspaceScopedModel, TimestampedModel):
    """One owner-text extraction attempt over a fixed set of episodes of one
    workspace (Phase 8A brief D9), lease-fenced like `MemoryExtractionJob`.

    Planned nightly (`memory.ingestion.extraction.plan_nightly_extraction`):
    at most one open job per workspace and data class; `personal_data` jobs
    hold only personal-class (calendar) or `private` episodes and are sent
    with `personal_data=True` -- never batched with cloud-eligible ones.
    `candidates` holds the provenance-checked survivors until the
    workspace's consolidation turns them into proposals (D10); they are
    derived from scrubbed owner text, so forget-source strips a source's
    entries and the TTL beat deletes old jobs.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        # The per-source daily budget or a gateway budget refused the call:
        # the episodes stay un-extracted for the next night (D13).
        DEFERRED = "deferred"

    night = models.DateField()
    personal_data = models.BooleanField(default=False)
    episode_ids = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=50, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    candidates = models.JSONField(default=list, blank=True)
    consolidation_token = models.UUIDField(null=True, blank=True)
    consolidation_lease_expires_at = models.DateTimeField(null=True, blank=True)
    consolidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "consolidated_at"]),
        ]

    def __str__(self):
        return f"episode extraction job {self.id} ({self.status})"
