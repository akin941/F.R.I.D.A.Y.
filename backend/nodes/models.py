"""Friday Node rows (Phase 9 brief D2, D6, D8).

- `Node`: one paired Mac of the owner, in the owner's personal workspace.
  Its primary key IS its `TrustedDevice`'s id, so `node_id` means the same
  thing in the pairing response, the rotate response, `hello.ack` and every
  `invoke`. Never deleted: revocation flips the TrustedDevice row (D1) and
  the Node stays as the audit chain's subject.
- `CapabilitySnapshot`: the catalogue a node advertised in `hello`, written
  when `catalogue_sha256` changes (branch 4), with each entry's server-side
  verdict (invocable, or a `not_invocable` reason). Read-only data: nothing
  ever writes node policy (ADR-013 decision 5).
- `NodeInvocation`: one hash-chained, append-only row per node call (D7
  terminal statuses, D8). It holds hashes, sizes and codes, never args or
  output content. Every FK is PROTECT: an append-only, hashed row must never
  be touched by a cascade (a SET_NULL would be an UPDATE the trigger refuses
  and that would also break `row_hash`).
- `NodeAuditHead`: the single row that serializes chain appends
  (`select_for_update`) and remembers the tip.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.db import WorkspaceScopedManager, WorkspaceScopedModel
from core.models import TimestampedModel

GENESIS_HASH = "0" * 64


class Node(WorkspaceScopedModel, TimestampedModel):
    """A paired Friday Node. `id` == `device_id` (set at pairing)."""

    class Role(models.TextChoices):
        WORKSTATION = "workstation"
        LAPTOP = "laptop"
        MEDIA = "media"
        SERVER = "server"
        OTHER = "other"

    class Platform(models.TextChoices):
        MACOS = "macos"
        LINUX = "linux"
        WINDOWS = "windows"

    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.PROTECT, related_name="nodes"
    )
    device = models.OneToOneField(
        "accounts.TrustedDevice", on_delete=models.PROTECT, related_name="node"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="nodes"
    )
    name = models.CharField(max_length=100)
    protocol_version = models.PositiveSmallIntegerField(default=1)
    # The server signing key id handed out at pairing (D4): preflight
    # (closeout) compares it with the configured key, so a key change that
    # would strand a paired node is caught.
    pinned_key_id = models.CharField(max_length=16)
    # Reported by the node in `hello` / `heartbeat` (branch 4).
    node_version = models.CharField(max_length=64, blank=True, default="")
    os_version = models.CharField(max_length=64, blank=True, default="")
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    spool_count = models.PositiveIntegerField(default=0)
    spool_bytes = models.PositiveBigIntegerField(default=0)
    # Phase 11 D15: the latest `voice.state` event (v2 only), written by
    # `nodes.events` at most once a second per node. Codes and counts only:
    # the voice child's state, the node's local mic mode, the session flag
    # and the child's daily counters. `voice_state_at` is the SERVER's
    # receive time (staleness never trusts the node clock); "" / null until
    # the node first reports.
    voice_state = models.CharField(max_length=16, blank=True, default="")
    voice_mode = models.CharField(max_length=16, blank=True, default="")
    voice_session_open = models.BooleanField(default=False)
    voice_state_at = models.DateTimeField(null=True, blank=True)
    voice_wake_accepts_today = models.PositiveIntegerField(default=0)
    voice_wake_discards_today = models.PositiveIntegerField(default=0)
    voice_storm_mutes_today = models.PositiveIntegerField(default=0)
    # Phase 13 D3: owner-set labels -- server-side display and resolution
    # data, never node policy (ADR-013 decision 5): no server message
    # carries them. `name_key` is `core.speech.phrase_key(name)`; it and
    # every alias key are unique per owner among USABLE nodes (not revoked,
    # killed or expired), checked in `nodes.services` under a lock on the
    # owner's personal workspace row at pairing and at relabel. No DB
    # constraint: revocation lives on `TrustedDevice` (also flipped by
    # `accounts.revoke_all_devices`, which may not import `nodes`), so a
    # partial index cannot express "usable".
    name_key = models.CharField(max_length=100, db_index=True, default="")
    room = models.ForeignKey(
        "workspaces.Room",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="nodes",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.OTHER)
    # Set once at pairing (`platform`, optional, default macOS), never
    # edited afterwards.
    platform = models.CharField(max_length=16, choices=Platform.choices, default=Platform.MACOS)
    aliases = models.JSONField(default=list, blank=True)
    # When the owner demonstrably used this PC (Phase 13 D6; written from
    # branch 2). Heartbeats never count.
    last_active_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.pk})"


class CapabilitySnapshot(WorkspaceScopedModel, TimestampedModel):
    """One advertised catalogue (D6). `entries` holds, per catalogue entry,
    `name`, `version`, `class`, `availability`, the sha256 of both schemas,
    `invocable` and `reason` -- never the node's raw schemas' content beyond
    their hashes."""

    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.PROTECT, related_name="node_snapshots"
    )
    node = models.ForeignKey(Node, on_delete=models.PROTECT, related_name="snapshots")
    catalogue_sha256 = models.CharField(max_length=64)
    entries = models.JSONField(default=list)
    node_version = models.CharField(max_length=64, blank=True, default="")
    os_version = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.node_id} catalogue {self.catalogue_sha256[:12]}"


class NodeInvocation(WorkspaceScopedModel):
    """One node call, hash-chained (D8). The workspace is the call's target
    workspace (redeclared PROTECT; the column stays `workspace_id`, so
    `for_actor()` is unchanged). Written only by `nodes.audit_chain.append`;
    UPDATE and DELETE are refused by a DB trigger (migration 0002)."""

    class Status(models.TextChoices):
        OK = "ok"
        REFUSED = "refused"
        ERROR = "error"
        TIMEOUT = "timeout"
        NODE_DISCONNECTED = "node_disconnected"
        SERVER_REFUSED = "server_refused"
        NODE_OFFLINE = "node_offline"
        BUSY = "busy"
        BUS_UNAVAILABLE = "bus_unavailable"
        # Phase 10 D7: the gated / confirm-class terminal states.
        FAILED = "failed"
        DENIED = "denied"
        CONFIRM_TIMEOUT = "confirm_timeout"
        STALE_DISPATCH = "stale_dispatch"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seq = models.PositiveBigIntegerField(unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.PROTECT, related_name="node_invocations"
    )
    node = models.ForeignKey(Node, on_delete=models.PROTECT, related_name="invocations")
    capability = models.CharField(max_length=64)
    capability_version = models.PositiveSmallIntegerField()
    args_sha256 = models.CharField(max_length=64)
    # "" when nothing was signed or sent (D7 not-sent statuses).
    nonce = models.CharField(max_length=43, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices)
    refusal_code = models.CharField(max_length=32, blank=True, default="")
    output_sha256 = models.CharField(max_length=64, blank=True, default="")
    output_bytes = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)
    # Rows the server dropped as unmapped / another workspace's (D9, Q5).
    dropped_unmapped = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    trace_id = models.CharField(max_length=64)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="node_invocations",
    )
    channel = models.CharField(max_length=20)
    # Phase 10 D7: action rows (confirm / gate class). Nullable, so Phase 9
    # read rows keep their shape; `hash_version` 2 hashes these fields too
    # (`nodes.audit_chain.HASHED_FIELDS_V2`), 1 is a Phase 9 row.
    invocation_class = models.CharField(max_length=10, blank=True, default="")
    approval_id = models.UUIDField(null=True, blank=True)
    grant_id = models.UUIDField(null=True, blank=True)
    exit_code = models.SmallIntegerField(null=True, blank=True)
    confirm_state = models.CharField(max_length=16, blank=True, default="")
    hash_version = models.PositiveSmallIntegerField(default=1)
    prev_hash = models.CharField(max_length=64)
    row_hash = models.CharField(max_length=64, unique=True)

    objects = WorkspaceScopedManager()

    class Meta:
        ordering = ["-seq"]

    def __str__(self):
        return f"#{self.seq} {self.capability} {self.status}"


# The closed (status, refusal_code) vocabulary of D7: a status maps to the
# set of refusal codes it may carry ("" = none). `nodes.audit_chain.append`
# refuses any other pair; branch 5 pins it against the brief's table.
NODE_REFUSED_CODES = frozenset(
    {
        "unknown_key",
        "bad_signature",
        "wrong_node",
        "not_yet_valid",
        "expired",
        "ttl_too_long",
        "replay",
        "nonce_cache_full",
        "unknown_capability",
        "class_not_allowed",
        "args_hash_mismatch",
        "args_schema",
        "not_granted",
    }
)
# Phase 10 D5: the v2 node refusal codes a gated / confirm-class call may
# end with. `owner_denied` and `confirm_timeout` are their own statuses
# (`denied`, `confirm_timeout`), never `refused`.
NODE_REFUSED_CODES_V2 = frozenset(
    {
        "unknown_key",
        "bad_signature",
        "wrong_node",
        "not_yet_valid",
        "expired",
        "ttl_too_long",
        "replay",
        "nonce_cache_full",
        "unknown_capability",
        "class_mismatch",
        "grant_unexpected",
        "grant_missing",
        "bad_grant_signature",
        "grant_mismatch",
        "args_hash_mismatch",
        "args_schema",
        "not_granted",
        "not_configured",
        "busy_confirming",
        "expired_after_confirm",
    }
)
# Phase 10 D2/D7/D8: why the server refused a gated or confirm-class call
# before (or instead of) sending it. Nothing is signed for any of them.
SERVER_REFUSED_ACTION_CODES = frozenset(
    {
        "actor_not_owner",
        "approval_not_approved",
        "wrong_tool",
        "args_changed",
        "args_contract",
        "not_invocable",
        "kill_switch",
        "path_not_in_workspace",
        "workspace_not_allowed",
        "signing_unavailable",
        "node_mismatch",
        "bus_unavailable",
    }
)
TERMINAL_VOCABULARY = {
    NodeInvocation.Status.OK: frozenset({""}),
    NodeInvocation.Status.REFUSED: NODE_REFUSED_CODES | NODE_REFUSED_CODES_V2,
    NodeInvocation.Status.ERROR: frozenset({"node_error", "result_schema", "result_too_large"}),
    NodeInvocation.Status.TIMEOUT: frozenset({""}),
    NodeInvocation.Status.NODE_DISCONNECTED: frozenset({""}),
    NodeInvocation.Status.SERVER_REFUSED: frozenset(
        {
            "not_invocable",
            "args_contract",
            "signing_unavailable",
            "path_not_in_workspace",
            "workspace_not_allowed",
            "session_not_found",
        }
    )
    | SERVER_REFUSED_ACTION_CODES,
    NodeInvocation.Status.NODE_OFFLINE: frozenset({""}),
    NodeInvocation.Status.BUSY: frozenset({""}),
    NodeInvocation.Status.BUS_UNAVAILABLE: frozenset({""}),
    # Phase 10 D7. `failed`: the command ran and exited non-zero (""), or
    # the node reported an error (start failure, kill switch while running).
    NodeInvocation.Status.FAILED: frozenset({"", "node_error"}),
    NodeInvocation.Status.DENIED: frozenset({"owner_denied"}),
    NodeInvocation.Status.CONFIRM_TIMEOUT: frozenset({"confirm_timeout"}),
    NodeInvocation.Status.STALE_DISPATCH: frozenset({""}),
}
# Statuses where nothing was signed or sent: nonce, output hash and size
# are empty (D7).
NOT_SENT_STATUSES = frozenset(
    {
        NodeInvocation.Status.SERVER_REFUSED,
        NodeInvocation.Status.NODE_OFFLINE,
        NodeInvocation.Status.BUSY,
        NodeInvocation.Status.BUS_UNAVAILABLE,
        NodeInvocation.Status.STALE_DISPATCH,
    }
)
# Phase 9 D7 (branch 5): the whole `(status, refusal_code)` vocabulary of a
# **read** (`nodes.services.invoke_read` / `refuse_read`), pinned by a test
# against the brief's terminal-status table. A subset of
# TERMINAL_VOCABULARY; `_record_read` refuses any other pair. Beyond the D7
# table: the Phase 8B step-0 actor check (`actor_not_owner`) and the
# gateway's own verdict for an envelope of another node (`node_mismatch`,
# branch 4).
CALLER_REFUSAL_CODES = frozenset(
    {"path_not_in_workspace", "workspace_not_allowed", "session_not_found"}
)
READ_SERVER_REFUSED_CODES = (
    frozenset(
        {
            "not_invocable",
            "args_contract",
            "signing_unavailable",
            "actor_not_owner",
            "node_mismatch",
        }
    )
    | CALLER_REFUSAL_CODES
)
READ_TERMINAL_VOCABULARY = {
    NodeInvocation.Status.OK: frozenset({""}),
    NodeInvocation.Status.REFUSED: NODE_REFUSED_CODES,
    NodeInvocation.Status.ERROR: frozenset({"node_error", "result_schema", "result_too_large"}),
    NodeInvocation.Status.TIMEOUT: frozenset({""}),
    NodeInvocation.Status.NODE_DISCONNECTED: frozenset({""}),
    NodeInvocation.Status.SERVER_REFUSED: READ_SERVER_REFUSED_CODES,
    NodeInvocation.Status.NODE_OFFLINE: frozenset({""}),
    NodeInvocation.Status.BUSY: frozenset({""}),
    NodeInvocation.Status.BUS_UNAVAILABLE: frozenset({""}),
}


class NodeActionExecution(WorkspaceScopedModel, TimestampedModel):
    """One confirm- or gate-class node action (Phase 10 brief D7), in its
    target workspace. Not append-only: `status` moves along

        approved -> granted -> sent -> confirming -> running -> {ok, failed}

    with the terminal side exits `refused`, `denied`, `confirm_timeout`,
    `timeout`, `node_disconnected`, `node_offline`, `server_refused` and
    `stale_dispatch`. A confirm-class execution has no approval and starts
    at `sent`. The append-only record is the one `NodeInvocation` chain row
    written at the terminal state (`nodes.services.finish_execution`).

    Holds hashes, codes and the scrubbed `tail` only -- never the args
    (the approval's snapshot holds those) and never raw output. `tail` is
    re-scrubbed by the server before it is saved (`nodes.output`) and
    cleared after `NODE_ACTION_TAIL_RETENTION_DAYS`."""

    class Status(models.TextChoices):
        APPROVED = "approved"
        GRANTED = "granted"
        SENT = "sent"
        CONFIRMING = "confirming"
        RUNNING = "running"
        OK = "ok"
        FAILED = "failed"
        REFUSED = "refused"
        DENIED = "denied"
        CONFIRM_TIMEOUT = "confirm_timeout"
        TIMEOUT = "timeout"
        NODE_DISCONNECTED = "node_disconnected"
        NODE_OFFLINE = "node_offline"
        SERVER_REFUSED = "server_refused"
        STALE_DISPATCH = "stale_dispatch"

    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.PROTECT, related_name="node_action_executions"
    )
    # A string reference, never an import: `tools` imports `nodes`, and
    # `tools` must not reach `approvals` (import contract), not even
    # transitively.
    approval = models.OneToOneField(
        "approvals.ApprovalRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="node_action_execution",
    )
    node = models.ForeignKey(Node, on_delete=models.PROTECT, related_name="action_executions")
    capability = models.CharField(max_length=64)
    capability_version = models.PositiveSmallIntegerField()
    capability_class = models.CharField(max_length=10)
    args_sha256 = models.CharField(max_length=64)
    source_message_id = models.CharField(max_length=64, blank=True, default="")
    invocation_id = models.UUIDField(null=True, blank=True, unique=True)
    grant_id = models.UUIDField(null=True, blank=True, unique=True)
    grant_issued_at = models.DateTimeField(null=True, blank=True)
    nonce = models.CharField(max_length=43, blank=True, default="")
    # Unix seconds of the envelope's (and grant's) expiry; the watchdog's
    # deadline is this + the contract's max_runtime_seconds + 30 s.
    expires_at = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    refusal_code = models.CharField(max_length=32, blank=True, default="")
    confirm_state = models.CharField(max_length=16, blank=True, default="")
    exit_code = models.SmallIntegerField(null=True, blank=True)
    output_bytes = models.PositiveBigIntegerField(default=0)
    output_sha256 = models.CharField(max_length=64, blank=True, default="")
    tail = models.TextField(blank=True, default="")
    truncated = models.BooleanField(default=False)
    sandbox_violation = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trace_id = models.CharField(max_length=64)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="node_action_executions",
    )

    objects = WorkspaceScopedManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.capability} {self.status}"


# Terminal execution statuses (D7); every other status is in flight.
EXECUTION_TERMINAL_STATUSES = frozenset(
    {
        NodeActionExecution.Status.OK,
        NodeActionExecution.Status.FAILED,
        NodeActionExecution.Status.REFUSED,
        NodeActionExecution.Status.DENIED,
        NodeActionExecution.Status.CONFIRM_TIMEOUT,
        NodeActionExecution.Status.TIMEOUT,
        NodeActionExecution.Status.NODE_DISCONNECTED,
        NodeActionExecution.Status.NODE_OFFLINE,
        NodeActionExecution.Status.SERVER_REFUSED,
        NodeActionExecution.Status.STALE_DISPATCH,
    }
)


class NodeAuditHead(models.Model):
    """The chain tip (a single row, pk 1). Infrastructure, not business
    data: it names no workspace and holds only a counter and a hash."""

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK)
    last_seq = models.PositiveBigIntegerField(default=0)
    last_hash = models.CharField(max_length=64, default=GENESIS_HASH)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"node audit head at seq {self.last_seq}"
