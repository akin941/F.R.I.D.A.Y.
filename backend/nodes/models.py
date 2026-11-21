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
TERMINAL_VOCABULARY = {
    NodeInvocation.Status.OK: frozenset({""}),
    NodeInvocation.Status.REFUSED: NODE_REFUSED_CODES,
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
    ),
    NodeInvocation.Status.NODE_OFFLINE: frozenset({""}),
    NodeInvocation.Status.BUSY: frozenset({""}),
    NodeInvocation.Status.BUS_UNAVAILABLE: frozenset({""}),
}
# Statuses where nothing was signed or sent: nonce, output hash and size
# are empty (D7).
NOT_SENT_STATUSES = frozenset(
    {
        NodeInvocation.Status.SERVER_REFUSED,
        NodeInvocation.Status.NODE_OFFLINE,
        NodeInvocation.Status.BUSY,
        NodeInvocation.Status.BUS_UNAVAILABLE,
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
