"""Connector state in Postgres (ADR-010 decision 3, brief D3/D6).

Only two things live here: whether a workspace has a connector switched on
(`ConnectorBinding`) and the at-most-once execution record of each approved
write (`ConnectorWriteExecution`). What a connector *is* -- its tools, pins,
channels, data class -- is code (`connectors/manifests/`), never a row.
Neither model holds a credential, a server URL or connector output: no
column exists that could.

String FK label for `approvals.ApprovalRequest`: `connectors` must not import
`approvals` (ADR-010 contract) -- the same shape as `tools.VaultExportRun`.

Phase 12 (branch 4) adds the third: `ConnectorAsyncRead`, the run-once
record of one async read (D11), whose bounded result text lives only until
the follow-up that consumes it has been written.
"""

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class ConnectorBinding(WorkspaceScopedModel, TimestampedModel):
    """One manifest switched on (or not) for one workspace. `enabled`
    defaults False: creating a binding row never enables anything; the
    owner enables it explicitly over the web (audited, D3).

    `last_health` is the most recent pin/health report, Friday-authored
    bookkeeping only: `checked_at`, `server_name`/`server_version`
    (server-supplied identifiers, bounded and stripped, display-only),
    `protocol_version`, `error` (closed vocabulary) and `tools` --
    `{tool_name: "ok" | "mismatch" | "missing" | "unchecked"}`. A tool is
    callable only while its status is `ok`; a failed check never turns one
    back on. Server descriptions are never stored."""

    connector = models.CharField(max_length=40)
    enabled = models.BooleanField(default=False)
    last_health = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "connector"], name="connectors_binding_unique_per_workspace"
            ),
        ]
        indexes = [models.Index(fields=["connector", "enabled"])]

    def __str__(self):
        return f"{self.connector} binding {self.id} ({'on' if self.enabled else 'off'})"

    def tool_status(self, tool_name):
        return (self.last_health or {}).get("tools", {}).get(tool_name, "unchecked")


class ConnectorWriteExecution(WorkspaceScopedModel, TimestampedModel):
    """The execution record of one approved connector write (brief D6),
    lease-fenced like `tools.VaultExportRun` -- with one deliberate
    difference: a write is **never retried**. Only a `pending` row is ever
    claimed; a `running` row whose lease expired is not taken over but moved
    to `unknown_outcome` (the worker was lost mid-call: the server may or may
    not have acted), shown to the owner and never re-sent. A new attempt is
    a new request and a new decision."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        UNKNOWN_OUTCOME = "unknown_outcome"

    class ErrorType(models.TextChoices):
        """Closed vocabulary: never the exception's or the server's own
        text (which can carry URLs, tokens or attacker-authored content)."""

        CONNECTOR_CHANGED = "connector_changed"
        PIN_MISMATCH = "pin_mismatch"
        NOT_CONFIGURED = "not_configured"
        TIMEOUT = "timeout"
        TRANSPORT_ERROR = "transport_error"
        PROTOCOL_ERROR = "protocol_error"
        TOOL_ERROR = "tool_error"
        # A native bridge found the target gone before sending (D8: a Home
        # Assistant entity removed after the decision) -- failed closed.
        TARGET_MISSING = "target_missing"
        WORKER_LOST = "worker_lost"
        # A write's precondition (Phase 5 branch 5): the PR's remote head
        # is no longer the SHA the owner approved -- nothing was sent.
        HEAD_MOVED = "head_moved"
        UNKNOWN = "unknown"

    TERMINAL = frozenset({Status.SUCCEEDED, Status.FAILED, Status.UNKNOWN_OUTCOME})

    approval_request = models.OneToOneField(
        "approvals.ApprovalRequest",
        on_delete=models.CASCADE,
        related_name="connector_write_execution",
    )
    binding = models.ForeignKey(
        ConnectorBinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="write_executions",
    )
    connector = models.CharField(max_length=40)
    tool = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Set in the same transaction that claims the row, i.e. the moment the
    # one send may start; a row with `started_at` set is never sent again.
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=30, choices=ErrorType.choices, blank=True, default="")
    # Sanitized, bounded result (text parts stripped and capped); low-trust
    # server output, never rendered as instructions and never a memory.
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "lease_expires_at"]),
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"{self.connector}.{self.tool} write {self.id} ({self.status})"


class ConnectorAsyncRead(WorkspaceScopedModel, TimestampedModel):
    """One async connector read (Phase 12 D11, ADR-010 amendment 2026-11):
    started by an owner's turn (`connectors.reads.start_async_read_impl`),
    run **once** by `connectors.tasks.run_async_read`, never retried.

    `pending` -> `running` (claimed; `started_at` set in the same
    transaction, the moment the one call may start) -> `ok` / `failed` /
    `timed_out`. A `pending` row is sent at most once: only one claim can
    win. A `running` row whose worker was lost is moved to `failed`
    (`worker_lost`) by the recovery scan -- never re-sent.

    `arguments` are the serializer-validated arguments the call sends; they
    are cleared when the read ends (only their sha256 stays). `result_text`
    is the bounded, invisible-formatting-stripped server text -- low-trust
    data, fenced by the follow-up and never rendered as instructions, never
    a memory. `error_code` is a closed vocabulary, never server text."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        OK = "ok"
        FAILED = "failed"
        TIMED_OUT = "timed_out"

    TERMINAL = frozenset({Status.OK, Status.FAILED, Status.TIMED_OUT})

    binding = models.ForeignKey(
        ConnectorBinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="async_reads",
    )
    connector = models.CharField(max_length=40)
    tool = models.CharField(max_length=40)
    channel = models.CharField(max_length=20)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    manifest_version = models.PositiveIntegerField()
    schema_sha256 = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict, blank=True)
    arguments_sha256 = models.CharField(max_length=64)
    # "" = the manifest's class; `personal` when one call is stricter.
    data_class = models.CharField(max_length=10, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True, default="")
    result_text = models.TextField(blank=True, default="")
    result_size = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"{self.connector}.{self.tool} async read {self.id} ({self.status})"


HOME_ENTITY_ID_MAX_CHARS = 141
HOME_ENTITY_LABEL_MAX_CHARS = 60


class HomeEntity(WorkspaceScopedModel, TimestampedModel):
    """One Home Assistant entity the owner allowlisted (Phase 13 D11).

    `ha_call_service` is refused unless its `entity_id` is an **enabled**
    `HomeEntity` of the request's workspace and its `service` is in
    `services` -- at request time, as the execution precondition and in the
    bridge's own pre-send read. An empty allowlist means no entity call is
    possible (fail closed; Wake-on-LAN, which has no entity, stays governed
    by `HA_WOL_ALLOWED_MACS`). Rows of the owner's **personal** workspace
    only; created, changed and removed only by the installation owner over
    the web (`connectors.home_entities`, audited `ha.entity_allowed` /
    `ha.entity_changed` / `ha.entity_removed`). No tool, route, schedule or
    connector writes this table (CLAUDE.md rule 4; a registry walk test
    pins it).

    `services` is a subset of the bridge's code maximum for the entity's
    domain (`connectors.bridges.home_assistant.SERVICES`), never of a
    `NEVER_DOMAINS` or read-only domain: `save()` re-checks it, so not even
    a direct ORM write can widen the maximum. `HA_ALLOWED_DOMAINS` narrows
    further at call time. `label` / `aliases` follow the shared label rules
    (`workspaces.rooms.clean_label` / `clean_aliases`); `key` is the
    label's `core.speech.phrase_key` (what the home routes resolve on)."""

    entity_id = models.CharField(max_length=HOME_ENTITY_ID_MAX_CHARS)
    room = models.ForeignKey(
        "workspaces.Room",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="home_entities",
    )
    label = models.CharField(max_length=HOME_ENTITY_LABEL_MAX_CHARS)
    key = models.CharField(max_length=HOME_ENTITY_LABEL_MAX_CHARS)
    aliases = models.JSONField(default=list, blank=True)
    services = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    # Phase 13b D5 (ADR-018 decision 2): the owner's per-entity "direct"
    # flag -- default off, only on a `media_player` with at least one
    # service of the closed direct class (`save()` refuses anything else),
    # set only by the owner over the web (`connectors.home_entities`,
    # audited `ha.entity_direct_changed`). Never by an agent, tool or model.
    direct = models.BooleanField(default=False)

    class Meta:
        ordering = ["entity_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "entity_id"], name="connectors_home_entity_unique"
            ),
        ]

    def __str__(self):
        return f"{self.entity_id} ({self.label})"

    @property
    def domain(self):
        return self.entity_id.split(".", 1)[0]

    def save(self, *args, **kwargs):
        # The code maximum holds on every write path, not only the
        # owner-web service. Lazy import: the bridge loads the connector
        # framework, which must not run during app-registry setup.
        from django.core.exceptions import ValidationError

        from connectors.bridges.home_assistant import code_maximum_refusal

        refusal = code_maximum_refusal(self.entity_id, self.services)
        if refusal is not None:
            raise ValidationError(refusal, code=refusal)
        if self.direct:
            from connectors.home_direct import DIRECT_NOT_ALLOWED, direct_eligible

            if not direct_eligible(self.entity_id, self.services):
                raise ValidationError(DIRECT_NOT_ALLOWED, code=DIRECT_NOT_ALLOWED)
        super().save(*args, **kwargs)

    @property
    def direct_services(self):
        """The entity's services inside ADR-018's closed direct class (in
        the code maximum's order); what a `direct` flag covers."""
        from connectors.home_direct import direct_services

        return direct_services(self.entity_id, self.services)


HOME_DIRECT_VOLUME_DECIMALS = 2


class HomeDirectSettings(WorkspaceScopedModel, TimestampedModel):
    """The owner's direct-media settings (Phase 13b D6, ADR-018 decisions
    6-7): one row per **personal** workspace, created on the owner's first
    read over the web (`connectors.home_direct.get_settings`) and changed
    only by `update_settings` (owner, web, audited
    `ha.direct_settings_changed`). Until the row exists the defaults apply
    (`kill_switch` off, `volume_max` = `HA_DIRECT_VOLUME_MAX`); the
    classifier only reads it and never creates it.

    `kill_switch=True` turns every would-be direct call back into a
    gate-class card. `HA_DIRECT_MEDIA_KILL_SWITCH` in the environment does
    the same and cannot be switched off here."""

    kill_switch = models.BooleanField(default=False)
    volume_max = models.DecimalField(max_digits=3, decimal_places=HOME_DIRECT_VOLUME_DECIMALS)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"], name="connectors_home_direct_settings_one"
            ),
            models.CheckConstraint(
                condition=models.Q(volume_max__gte=0) & models.Q(volume_max__lte=1),
                name="connectors_home_direct_volume_max_range",
            ),
        ]

    def __str__(self):
        return f"home direct settings {self.workspace_id} (kill={self.kill_switch})"


class HomeDirectCall(WorkspaceScopedModel, TimestampedModel):
    """The record of one direct (card-less) Home Assistant media call
    (Phase 13b D11, ADR-018 decision 8): created `running` immediately
    before the single send and finalized once (`succeeded` / `failed` /
    `unknown_outcome`); never retried, never re-sent. Written only by the
    direct executor (`connectors.home_direct.execute`, branch 1c); branch
    1b adds the model, the owner's list and no writer (a registry walk pins
    it). `error_type` uses `ConnectorWriteExecution.ErrorType`'s closed
    vocabulary; no HA free text is ever stored. `requested_data` is what
    the owner asked for when it differs from what was sent (a clamped
    `volume_set`)."""

    class Origin(models.TextChoices):
        VOICE = "voice"
        NODE = "node"
        CHAT = "chat"

    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        UNKNOWN_OUTCOME = "unknown_outcome"

    TERMINAL = frozenset({Status.SUCCEEDED, Status.FAILED, Status.UNKNOWN_OUTCOME})

    # The brief's `entity` FK; named `home_entity` because `entity_id` is
    # the Home Assistant id column (kept when the allowlist row is removed).
    home_entity = models.ForeignKey(
        HomeEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="direct_calls"
    )
    entity_id = models.CharField(max_length=HOME_ENTITY_ID_MAX_CHARS)
    service = models.CharField(max_length=40)
    data = models.JSONField(default=dict, blank=True)
    requested_data = models.JSONField(null=True, blank=True)
    origin = models.CharField(max_length=10, choices=Origin.choices)
    node = models.UUIDField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="home_direct_calls",
    )
    source_message_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    error_type = models.CharField(
        max_length=40, blank=True, default="", choices=ConnectorWriteExecution.ErrorType.choices
    )
    state_before = models.JSONField(null=True, blank=True)
    state_after = models.JSONField(null=True, blank=True)
    read_back = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"direct {self.service} on {self.entity_id} ({self.status})"
