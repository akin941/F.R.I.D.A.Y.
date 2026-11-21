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
        # Phase 8B branch 7 (brief D12): the owner's `00_Inbox/` vault
        # notes, imported once inside an approved `vault_export` run. Not
        # personal-class (ADR-012 decision 11 lists calendar, mail and
        # transcripts only): only a `sensitivity: private` note is routed
        # to local providers.
        OBSIDIAN_INBOX = "obsidian_inbox"

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
        # Vault-relative folder under `00_Inbox` (brief D12); longest
        # segment-boundary prefix wins, like `path_prefix`. Only valid on
        # an `obsidian_inbox` source.
        VAULT_PREFIX = "vault_prefix"

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


# --- Temporal knowledge graph (Phase 8B branch 1, brief D2/D3/D7) ----------
#
# Entities and facts live in Postgres next to every other learned row; the
# graph is walked with recursive CTEs (`memory.graph.selectors`), no graph
# database. Every row is workspace-scoped and an edge never crosses
# workspaces: a fact's subject, object entity and `supersedes` target are in
# the fact's own workspace (checked in `save()` and by the
# `memory_memoryfact_same_workspace` trigger, migration 0010).


def live_normal_fact_q(prefix=""):
    """A live (current or pending, not trashed or archived) non-`private`
    fact (brief D4's entity-privacy rule). `prefix` lets a related lookup
    reuse it."""
    p = prefix
    current = models.Q(**{f"{p}status__in": ["approved", "system"], f"{p}valid_to__isnull": True})
    pending = models.Q(**{f"{p}status": "pending_approval"})
    return (current | pending) & models.Q(
        **{f"{p}deleted_at__isnull": True, f"{p}sensitivity": "normal"}
    )


class MemoryEntityQuerySet(WorkspaceScopedQuerySet):
    def with_privacy(self):
        """Annotate `is_private` (brief D4): an entity is private iff it has
        no live (current or pending, not trashed or archived) non-`private`
        fact as subject or object. The one queryset form of the rule;
        `memory.graph.selectors.entity_is_private` uses the same predicate."""
        live_normal = (
            MemoryFact.all_objects.filter(live_normal_fact_q())
            .filter(
                models.Q(subject=models.OuterRef("pk"))
                | models.Q(object_entity=models.OuterRef("pk"))
            )
            .values("pk")
        )
        return self.annotate(is_private=~models.Exists(live_normal))


class LiveMemoryEntityManager(models.Manager.from_queryset(MemoryEntityQuerySet)):
    """Default manager: hides trashed entities (ADR-009). Trash, restore
    and purge use `all_objects`."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class AllMemoryEntitiesManager(models.Manager.from_queryset(MemoryEntityQuerySet)):
    pass


class MemoryEntity(WorkspaceScopedModel, TimestampedModel):
    """A node of the temporal graph (brief D2): a repo, service, firm, a
    person by role, ... in one workspace. Learned entities are resolved in
    code (`memory.graph.services.resolve_entity`), never by a model; the
    `code_*` kinds come only from the Graphify import (branch 5). An entity
    carries no sensitivity: privacy is derived from its facts
    (`with_privacy()`)."""

    MAX_NAME_CHARS = 200
    MAX_ALIASES = 10

    class Kind(models.TextChoices):
        REPO = "repo"
        SERVICE = "service"
        FIRM = "firm"
        PERSON_ROLE = "person_role"
        PROJECT = "project"
        TOOL = "tool"
        CONCEPT = "concept"
        CODE_MODULE = "code_module"
        CODE_CLASS = "code_class"
        CODE_FUNCTION = "code_function"

    class Origin(models.TextChoices):
        LEARNED = "learned"
        GRAPHIFY = "graphify"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    name = models.CharField(max_length=MAX_NAME_CHARS)
    # Lowercased, NFKC, single spaces (`memory.graph.candidates.normalise_name`).
    normalised_name = models.CharField(max_length=MAX_NAME_CHARS)
    # Normalised alternative names, at most MAX_ALIASES.
    aliases = models.JSONField(default=list, blank=True)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.LEARNED)
    # Graphify: the node id plus the repo id (branch 5).
    external_ref = models.CharField(max_length=255, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)
    forget_operation_id = models.UUIDField(null=True, blank=True)

    objects = LiveMemoryEntityManager()
    all_objects = AllMemoryEntitiesManager()

    class Meta:
        ordering = ["kind", "normalised_name"]
        base_manager_name = "all_objects"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "kind", "normalised_name", "origin"],
                name="memory_entity_unique_name",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind__startswith="code_") | models.Q(origin="graphify"),
                name="memory_entity_code_kinds_graphify_only",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "kind"])]
        verbose_name_plural = "memory entities"

    def __str__(self):
        return f"{self.kind} entity {self.id}"


class MemoryFactQuerySet(WorkspaceScopedQuerySet):
    def current(self):
        """Current facts (D2): `approved` or `system`, `valid_to IS NULL`,
        not trashed."""
        return self.filter(
            status__in=[MemoryFact.Status.APPROVED, MemoryFact.Status.SYSTEM],
            valid_to__isnull=True,
            deleted_at__isnull=True,
        )

    def model_eligible(self):
        """The only sanctioned path by which MemoryFact rows may enter a
        gateway payload (D2 "retrievable facts", D8; the
        `Memory.objects.cloud_eligible()` parity): current, `approved`,
        `learned`, non-`private`. `system` (Graphify) facts never qualify."""
        return self.current().filter(
            status=MemoryFact.Status.APPROVED,
            origin=MemoryFact.Origin.LEARNED,
            sensitivity=MemoryFact.Sensitivity.NORMAL,
        )

    def as_of(self, when):
        """Facts as of `when` (D2): `valid_from <= when < coalesce(valid_to,
        inf)`, approved or system, not trashed. Superseded rows stay
        `approved` with a closed window, so history reads back."""
        return self.filter(
            models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=when),
            status__in=[MemoryFact.Status.APPROVED, MemoryFact.Status.SYSTEM],
            valid_from__lte=when,
            deleted_at__isnull=True,
        )


class LiveMemoryFactManager(models.Manager.from_queryset(MemoryFactQuerySet)):
    """Default manager: hides trashed facts (ADR-009)."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class AllMemoryFactsManager(models.Manager.from_queryset(MemoryFactQuerySet)):
    pass


class MemoryFact(WorkspaceScopedModel, TimestampedModel):
    """An edge of the temporal graph with a validity window (brief D2).

    Writers (D3): `memory.graph.ports.propose_fact` (owner-text learning,
    born `pending_approval`), the owner fact API (born `approved`, the
    owner's own words) and `memory.graph.ports.import_code_graph` (Graphify,
    `system`, branch 5). Approved facts are never edited in place: an edit
    is a new version that supersedes the old one
    (`memory.graph.services.edit_fact`)."""

    MAX_STATEMENT_CHARS = 500
    MAX_OBJECT_TEXT_CHARS = 500
    MAX_PROPOSED_CONFIDENCE = 90

    class Predicate(models.TextChoices):
        # learned
        DECIDED = "decided"
        USES = "uses"
        DEPENDS_ON = "depends_on"
        DEPLOYED_TO = "deployed_to"
        OWNED_BY = "owned_by"
        WORKS_ON = "works_on"
        PREFERS = "prefers"
        BLOCKED_BY = "blocked_by"
        REPLACED_BY = "replaced_by"
        HAS_STATUS = "has_status"
        # Graphify only
        CONTAINS = "contains"
        IMPORTS = "imports"
        CALLS = "calls"
        INHERITS = "inherits"

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval"
        APPROVED = "approved"
        REJECTED = "rejected"
        ARCHIVED = "archived"
        SYSTEM = "system"

    class Origin(models.TextChoices):
        LEARNED = "learned"
        GRAPHIFY = "graphify"

    Sensitivity = Memory.Sensitivity

    subject = models.ForeignKey(
        MemoryEntity, on_delete=models.PROTECT, related_name="facts_as_subject"
    )
    predicate = models.CharField(max_length=20, choices=Predicate.choices)
    object_entity = models.ForeignKey(
        MemoryEntity,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="facts_as_object",
    )
    object_text = models.CharField(max_length=MAX_OBJECT_TEXT_CHARS, blank=True, default="")
    statement = models.CharField(max_length=MAX_STATEMENT_CHARS)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    # Set on the OLD row when a supersede is applied.
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Set on a PENDING proposal only: the current fact it would replace if
    # approved (reconciliation's `supersede`, D4). Cleared on approve (the
    # link then lives in the old row's `superseded_by`), reject or forget.
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pending_supersessions",
    )
    confidence = models.PositiveSmallIntegerField(default=MAX_PROPOSED_CONFIDENCE)
    status = models.CharField(max_length=20, choices=Status.choices)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.LEARNED)
    ingestion_source = models.ForeignKey(
        "memory.IngestionSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="derived_facts",
    )
    # Plain UUID: the episode expires (d10).
    source_episode_id = models.UUIDField(null=True, blank=True)
    provenance_quote = models.CharField(max_length=300, blank=True, default="")
    sensitivity = models.CharField(
        max_length=20, choices=Memory.Sensitivity.choices, default=Memory.Sensitivity.NORMAL
    )
    # Retrieval (branch 4 embeds only `model_eligible()` rows; a `system`
    # fact keeps NULL for good). No fixed dimension (the MVP 3 policy).
    embedding = VectorField(null=True, blank=True)
    embedding_provider = models.ForeignKey(
        "ai_core.ModelProvider", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # The `Memory` embedding lease pattern (branch 4, `memory.jobs`): one
    # attempt in flight per row, a bounded retry budget. The generated
    # `search` tsvector column (D8) lives in the database only (migration
    # 0014, raw SQL) and is read through `memory.retrieval`.
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    # Provenance of an owner-UI write (the audit row names the writer too).
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    forget_operation_id = models.UUIDField(null=True, blank=True)
    forget_prior_status = models.CharField(max_length=20, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = LiveMemoryFactManager()
    all_objects = AllMemoryFactsManager()

    def save(self, *args, **kwargs):
        # Same-workspace edges are an invariant (the `Memory.save`
        # precedent); the DB trigger is the backstop for raw SQL.
        for label, entity_id in (
            ("subject", self.subject_id),
            ("object_entity", self.object_entity_id),
        ):
            if entity_id is None:
                continue
            workspace_ids = list(
                MemoryEntity.all_objects.filter(pk=entity_id).values_list("workspace_id", flat=True)
            )
            if workspace_ids != [self.workspace_id]:
                raise ValueError(f"{label} must belong to the fact's workspace.")
        if self.supersedes_id is not None:
            if self.pk is not None and self.supersedes_id == self.pk:
                raise ValueError("A fact cannot supersede itself.")
            target = (
                MemoryFact.all_objects.filter(pk=self.supersedes_id)
                .values_list("workspace_id", "subject_id", "predicate")
                .first()
            )
            if target != (self.workspace_id, self.subject_id, self.predicate):
                raise ValueError(
                    "supersedes must name a fact of the same workspace, subject and predicate."
                )
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-valid_from", "-created_at"]
        base_manager_name = "all_objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gte=models.F("valid_from")),
                name="memory_fact_valid_window",
            ),
            models.CheckConstraint(
                condition=(models.Q(origin="graphify") & models.Q(status="system"))
                | (~models.Q(origin="graphify") & ~models.Q(status="system")),
                name="memory_fact_system_iff_graphify",
            ),
            models.CheckConstraint(
                condition=~models.Q(origin="learned") | ~models.Q(provenance_quote=""),
                name="memory_fact_learned_has_quote",
            ),
            models.CheckConstraint(
                condition=(models.Q(object_entity__isnull=False) & models.Q(object_text=""))
                | (models.Q(object_entity__isnull=True) & ~models.Q(object_text="")),
                name="memory_fact_exactly_one_object",
            ),
            models.CheckConstraint(
                condition=models.Q(supersedes__isnull=True) | models.Q(status="pending_approval"),
                name="memory_fact_supersedes_only_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__lte=100),
                name="memory_fact_confidence_range",
            ),
            models.CheckConstraint(
                condition=~models.Q(origin="graphify")
                | models.Q(predicate__in=["contains", "imports", "calls", "inherits"]),
                name="memory_fact_graphify_predicates",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["subject", "predicate"]),
            models.Index(fields=["object_entity"]),
            models.Index(fields=["ingestion_source"]),
        ]

    def __str__(self):
        return f"{self.predicate} fact {self.id} ({self.status})"


# The closed predicate split (D2): a model may propose only the learned set;
# the Graphify set is written only by the code-graph import (branch 5).
LEARNED_PREDICATES = frozenset(
    {
        MemoryFact.Predicate.DECIDED,
        MemoryFact.Predicate.USES,
        MemoryFact.Predicate.DEPENDS_ON,
        MemoryFact.Predicate.DEPLOYED_TO,
        MemoryFact.Predicate.OWNED_BY,
        MemoryFact.Predicate.WORKS_ON,
        MemoryFact.Predicate.PREFERS,
        MemoryFact.Predicate.BLOCKED_BY,
        MemoryFact.Predicate.REPLACED_BY,
        MemoryFact.Predicate.HAS_STATUS,
    }
)
GRAPHIFY_PREDICATES = frozenset(
    {
        MemoryFact.Predicate.CONTAINS,
        MemoryFact.Predicate.IMPORTS,
        MemoryFact.Predicate.CALLS,
        MemoryFact.Predicate.INHERITS,
    }
)
LEARNED_ENTITY_KINDS = frozenset(
    {
        MemoryEntity.Kind.REPO,
        MemoryEntity.Kind.SERVICE,
        MemoryEntity.Kind.FIRM,
        MemoryEntity.Kind.PERSON_ROLE,
        MemoryEntity.Kind.PROJECT,
        MemoryEntity.Kind.TOOL,
        MemoryEntity.Kind.CONCEPT,
    }
)
# The entity kinds a Graphify import may write (D10): the code kinds, plus
# `tool` for a module's top-level import of an external package.
GRAPHIFY_ENTITY_KINDS = frozenset(
    {
        MemoryEntity.Kind.CODE_MODULE,
        MemoryEntity.Kind.CODE_CLASS,
        MemoryEntity.Kind.CODE_FUNCTION,
        MemoryEntity.Kind.TOOL,
    }
)


class CodeGraphImport(WorkspaceScopedModel, TimestampedModel):
    """The current Graphify snapshot of one repo in one workspace (Phase 8B
    branch 5, brief D10). Written only by `memory.graph.ports.
    import_code_graph` / `retire_code_graph`, and the importer serialises on
    this row. `repo_ref` is the producer's repo id (a `HarnessRepo` pk as
    text: memory never imports harness, so it is an opaque reference); it
    prefixes the `external_ref` of every entity of the repo's graph.

    Counts only, never labels. `retired_at` is set when the repo's
    `graphify_enabled` switch is turned off: every current `system` fact of
    the repo is closed and its entities drop out of every read. Nothing is
    deleted until a gate-class `purge_trash` names the repo in
    `graphify_repo_ids` (ADR-009, D7)."""

    repo_ref = models.CharField(max_length=64)
    commit_sha = models.CharField(max_length=64)
    committed_at = models.DateTimeField()
    imported_at = models.DateTimeField()
    entity_count = models.PositiveIntegerField(default=0)
    fact_count = models.PositiveIntegerField(default=0)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-imported_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "repo_ref"], name="memory_code_graph_import_unique_repo"
            ),
        ]

    def __str__(self):
        return f"code graph import {self.id} ({self.repo_ref})"


# --- Eval runs (Phase 8B branch 3, brief D9) --------------------------------


class MemoryEvalRun(WorkspaceScopedModel, TimestampedModel):
    """One recorded eval run: **metrics only** (brief D9).

    Written only by `manage.py memory_eval record <file> --owner <email>`
    from a metrics-only result file, into the named owner's **personal**
    workspace (rule 1: a firm-only member's `for_actor()` sees nothing, so
    no installation-level exception is needed). It stores no case text,
    case ids, `workspace_ref` values, tags, questions, answers, per-case
    rows or per-workspace breakdown: every field below is a count, a
    rate, an enum or a timestamp (`memory/tests/test_evals.py` scans the
    fields). `run_id` is the run's random id (the result file's name), so
    a re-record is idempotent and the two arms of a compare pair up."""

    class Set(models.TextChoices):
        PUBLIC = "public"
        PRIVATE = "private"

    class Kind(models.TextChoices):
        RUN = "run"
        BASELINE = "baseline"
        COMPARE = "compare"

    class Arm(models.TextChoices):
        A = "a"
        B = "b"

    class Verdict(models.TextChoices):
        PASS = "pass"
        FAIL = "fail"
        INCONCLUSIVE = "inconclusive"

    MAX_MODE_CHARS = 20
    MAX_PURPOSE_CHARS = 64

    run_id = models.UUIDField()
    set = models.CharField(max_length=10, choices=Set.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    retrieval_mode = models.CharField(max_length=MAX_MODE_CHARS)
    # "" = none (a result file's null; Django stores no NULL strings).
    compression_purpose = models.CharField(max_length=MAX_PURPOSE_CHARS, blank=True, default="")
    arm = models.CharField(max_length=1, choices=Arm.choices, blank=True, default="")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    total = models.PositiveIntegerField(default=0)
    answered = models.PositiveIntegerField(default=0)
    no_provider = models.PositiveIntegerField(default=0)
    budget_stopped = models.PositiveIntegerField(default=0)
    spend_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    recall_at_10 = models.FloatField(null=True, blank=True)
    leak_rate = models.FloatField(null=True, blank=True)
    temporal_correctness = models.FloatField(null=True, blank=True)
    answer_accuracy = models.FloatField(null=True, blank=True)
    pre_tokens = models.PositiveIntegerField(null=True, blank=True)
    post_tokens = models.PositiveIntegerField(null=True, blank=True)
    verdict = models.CharField(max_length=12, choices=Verdict.choices, blank=True, default="")

    class Meta:
        ordering = ["-started_at", "arm"]
        constraints = [
            models.UniqueConstraint(
                fields=["run_id", "arm"],
                name="memory_eval_run_unique_run_arm",
            ),
            models.CheckConstraint(
                condition=models.Q(finished_at__gte=models.F("started_at")),
                name="memory_eval_run_window",
            ),
            models.CheckConstraint(
                condition=models.Q(arm="") | models.Q(kind="compare"),
                name="memory_eval_run_arm_only_compare",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "-started_at"])]

    def __str__(self):
        return f"{self.set} {self.kind} eval run {self.run_id}"
