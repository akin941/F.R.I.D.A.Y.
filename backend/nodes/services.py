"""Node services (Phase 9 brief D4, D7).

Branch 2 holds pairing and envelope signing. Branch 4 (`feat/p9-node-gateway`)
adds `invoke_read` (D7: the bus side -- snapshot and contract checks,
presence, the shared in-flight cap, sign, push, wait, result validation --
and the one chain row + one `nodes.invoke` AuditLog row per call) and
`refuse_read` (a caller's own pre-invoke refusal: nothing signed or sent,
the same two rows). The read tools that call them are branch 5.

Phase 10 branch 1 adds snapshot classification by protocol version and
class (`classify_catalogue`, `record_capability_snapshot`,
`invocable_entry`): confirm- and gate-class entries are invocable only on a
v2 session (D1, D3).

Phase 10 branch 3 adds the action services (D2, D6-D8): `finish_execution`
(the one chain row + AuditLog row per execution), `create_gated_execution`,
`dispatch_gated` (grant issuance through `nodes.grants.issue`, push, never
wait), `invoke_confirm` (the confirm-class path, called only by the actions
view), `record_gated_progress` (the gateway's status moves, with the server
re-scrub of `tail`), and the beat backstops `sweep_node_actions` and
`clear_expired_tails`.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.devices import DeviceTrustError, enroll_node_device, node_device_refusal
from accounts.models import TrustedDevice
from audit.services import record_audit
from core.owner_web import OwnerWebRequired, require_owner_web
from core.speech import phrase_key
from core.unattended import deny_if_unattended
from nodes import audit_chain, bus
from nodes.action_rules import actor_is_node_owner, workspace_refusal
from nodes.activity import mark_node_active
from nodes.capabilities import check_catalogue_entry, get_action_contract, get_contract
from nodes.keys import SigningKeyUnavailable, signing_keyring
from nodes.models import (
    CALLER_REFUSAL_CODES,
    EXECUTION_TERMINAL_STATUSES,
    NODE_REFUSED_CODES,
    NODE_REFUSED_CODES_V2,
    NOT_SENT_STATUSES,
    READ_TERMINAL_VOCABULARY,
    CapabilitySnapshot,
    Node,
    NodeActionExecution,
)
from nodes.output import scrub_tail
from nodes.protocol.canonical import CanonicalizationError, canonicalize, sha256_hex
from nodes.protocol.messages import (
    GATEWAY_PATH,
    MAX_READ_INVOKE_TTL_SECONDS,
    PROTOCOL_VERSION,
    PROTOCOL_VERSIONS,
    ProtocolError,
)
from nodes.protocol.signing import KeyRing, build_invoke
from workspaces.models import Room, Workspace
from workspaces.rooms import (
    LabelConflict,
    LabelInvalid,
    LabelUnknownRoom,
    clean_aliases,
    clean_label,
    label_keys,
)

SUPPORTED_PROTOCOL_VERSIONS = frozenset({PROTOCOL_VERSION})
logger = logging.getLogger(__name__)


class UnsupportedProtocolVersion(Exception):
    """The node offered no protocol version this server speaks."""


@dataclass(frozen=True)
class PairingResult:
    node: Node
    node_secret: str
    keyring: KeyRing

    def response_payload(self) -> dict:
        """The pairing response body (D4), returned exactly once. It names
        the active signing key only: a retired key verifies old envelopes
        but is never pinned by a newly paired node."""
        return {
            "node_id": str(self.node.pk),
            "node_secret": self.node_secret,
            "server_signing_keys": [self.keyring.active.public_entry()],
            "protocol_version": PROTOCOL_VERSION,
            "gateway_path": GATEWAY_PATH,
            "rotate_after_days": settings.NODE_CREDENTIAL_ROTATE_AFTER_DAYS,
        }


# --- node labels (Phase 13 D3) ---

NODE_NAME_MAX_CHARS = 100
DEFAULT_NODE_NAMES = {
    Node.Platform.MACOS: "Mac düğümü",
    Node.Platform.LINUX: "Linux düğümü",
    Node.Platform.WINDOWS: "Windows düğümü",
}
NODE_RELABELLED_EVENT = "nodes.relabelled"
# A node whose device is refused for one of these reasons keeps its labels
# reserved: the kill switch is released without re-pairing, so a name freed
# while it is engaged would come back as a duplicate. Revoked and expired
# nodes (re-pairing is the only way back) free their labels.
_LABEL_HOLDING_REFUSALS = frozenset({None, "kill_switch"})


class NodeLabelPermissionDenied(Exception):
    """Not the node's owner over the web (`core.owner_web` codes)."""

    def __init__(self, message, code="owner_required"):
        self.code = code
        super().__init__(message)


def holds_labels(node, now=None) -> bool:
    """Whether `node`'s name and aliases are reserved: it is usable, or cut
    off only by the kill switch."""
    return node_device_refusal(node.device, now) in _LABEL_HOLDING_REFUSALS


def _lock_owner_nodes(owner):
    """Lock the owner's node rows (after the personal workspace row, which
    is what actually serializes label writes: it exists before the first
    node does)."""
    list(Node.objects.select_for_update().filter(owner=owner).values_list("pk", flat=True))


def taken_label_keys(owner, *, exclude_pk=None, now=None) -> set:
    """Every name and alias key of `owner`'s label-holding nodes."""
    now = now or timezone.now()
    keys = set()
    nodes = Node.objects.filter(owner=owner).select_related("device", "device__user")
    for node in nodes.exclude(pk=exclude_pk):
        if holds_labels(node, now):
            keys |= label_keys(node.name_key or phrase_key(node.name), node.aliases)
    return keys


def _suffixed(name, n):
    tail = f" {n}"
    return name[: NODE_NAME_MAX_CHARS - len(tail)].rstrip() + tail


def unique_node_name(name, taken) -> tuple[str, str]:
    """`(name, key)`: `name`, or "name 2", "name 3", ... -- the first whose
    key is not in `taken`."""
    key = phrase_key(name)
    base, n = name, 2
    while key in taken:
        name = _suffixed(base, n)
        key = phrase_key(name)
        n += 1
    return name, key


_UNSET = object()


def relabel_node(*, actor, node, channel, name=_UNSET, room_id=_UNSET, role=_UNSET, aliases=_UNSET):
    """Set a node's owner labels (Phase 13 D3): name, room, role, aliases.
    The node's owner over the web only (`require_owner_web` on the node's
    personal workspace). Labels are server-side data: **nothing is sent to
    the node** (no bus message; its next `hello` is unaffected).

    Rules: the name is 1-100 characters with a non-empty key; aliases as
    `workspaces.rooms.clean_aliases`; the name key and every alias key are
    unique across the owner's label-holding nodes (names and aliases
    together; an alias may not repeat this node's own name), checked under
    the personal-workspace lock pairing also takes; the room must be a room
    of the node's workspace (None clears it); the role one of `Node.Role`;
    `platform` is never editable. Audited `nodes.relabelled` with the
    changed fields and old/new keys and ids (never free text beyond keys).
    A no-op writes nothing. Returns the fresh node."""
    try:
        require_owner_web(actor=actor, workspace=node.workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise NodeLabelPermissionDenied(str(exc), code=exc.code) from exc
    if str(node.owner_id) != str(getattr(actor, "pk", "")):
        raise NodeLabelPermissionDenied("Only the node's owner can label it.")
    with transaction.atomic():
        Workspace.objects.select_for_update().filter(pk=node.workspace_id).first()
        _lock_owner_nodes(node.owner)
        locked = Node.objects.select_related("device", "room", "workspace").get(pk=node.pk)
        new_name, new_key = locked.name, locked.name_key or phrase_key(locked.name)
        if name is not _UNSET:
            new_name, new_key = clean_label(name, max_chars=NODE_NAME_MAX_CHARS)
        new_aliases = list(locked.aliases or [])
        if aliases is not _UNSET:
            new_aliases, _ = clean_aliases(aliases)
        alias_keys = [phrase_key(alias) for alias in new_aliases]
        if new_key in alias_keys:
            raise LabelConflict("An alias repeats the node's name.")
        if name is not _UNSET or aliases is not _UNSET:
            taken = taken_label_keys(locked.owner, exclude_pk=locked.pk)
            if taken & {new_key, *alias_keys}:
                raise LabelConflict("Another node already uses this name or alias.")
        new_room = locked.room
        if room_id is not _UNSET:
            new_room = None
            if room_id is not None:
                new_room = Room.objects.filter(pk=room_id, workspace=locked.workspace).first()
                if new_room is None:
                    raise LabelUnknownRoom("Unknown room.")
        new_role = locked.role
        if role is not _UNSET:
            if role not in Node.Role.values:
                raise LabelInvalid("Unknown role.")
            new_role = role

        changed, metadata = [], {"node_id": str(locked.pk)}
        if new_name != locked.name:
            metadata.update(old_key=locked.name_key, new_key=new_key)
            locked.name, locked.name_key = new_name, new_key
            changed += ["name", "name_key"]
        if new_aliases != list(locked.aliases or []):
            metadata["alias_keys"] = alias_keys
            locked.aliases = new_aliases
            changed.append("aliases")
        if new_room != locked.room:
            metadata.update(
                old_room_id=str(locked.room_id) if locked.room_id else "",
                new_room_id=str(new_room.pk) if new_room else "",
            )
            locked.room = new_room
            changed.append("room")
        if new_role != locked.role:
            metadata.update(old_role=locked.role, new_role=new_role)
            locked.role = new_role
            changed.append("role")
        if not changed:
            return locked
        locked.save(update_fields=[*changed, "updated_at"])
        if "name" in changed:
            TrustedDevice.objects.filter(pk=locked.device_id).update(name=locked.name)
        metadata["fields"] = sorted(set(changed) - {"name_key"})
        record_audit(
            action=NODE_RELABELLED_EVENT,
            actor=actor,
            workspace=locked.workspace,
            resource=locked,
            metadata=metadata,
        )
    return locked


def pair_node(
    *,
    raw_code,
    node_name,
    protocol_versions,
    client_ip=None,
    user_agent="",
    platform=Node.Platform.MACOS,
):
    """Pair a Friday Node with a node pairing code (D4). Returns a
    PairingResult (the `jnd_` secret is in it once and stored nowhere).

    Phase 13 D3: `platform` (macos / linux / windows, default macos so the
    Swift app pairs unchanged) is recorded once; a name that another
    label-holding node of the owner already answers to is suffixed
    ("MacBook 2"), never refused.

    Order: the signing key and the protocol version are checked first, so a
    server that could not sign (or speak v1) consumes no code. Then
    `accounts.devices.enroll_node_device` applies every code rule (node
    codes only, PIN on LAN, attempt burn, single use, kill switch) and the
    Node row is created in the owner's personal workspace with the key id
    it pinned. Raises `nodes.keys.SigningKeyUnavailable`,
    UnsupportedProtocolVersion or an `accounts.devices.DeviceTrustError`.
    """
    keyring = signing_keyring()
    if not SUPPORTED_PROTOCOL_VERSIONS & set(protocol_versions or ()):
        raise UnsupportedProtocolVersion()
    if platform not in Node.Platform.values:
        raise ValueError(f"unknown node platform {platform!r}")
    # Phase 13 D3: a blank or key-less name gets the platform's default.
    node_name = " ".join(str(node_name or "").split())
    if not phrase_key(node_name):
        node_name = DEFAULT_NODE_NAMES[platform]

    refusal = None
    with transaction.atomic():
        try:
            device, raw_secret = enroll_node_device(
                raw_code=raw_code,
                node_name=node_name,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        except DeviceTrustError as exc:
            # Caught INSIDE the atomic block on purpose: the refusal's own
            # writes (the failure audit row, PIN attempt burning) must
            # commit, not roll back with this transaction.
            refusal = exc
        else:
            workspace = Workspace.objects.select_for_update().get(
                owner=device.user, type=Workspace.Type.PERSONAL
            )
            _lock_owner_nodes(device.user)
            # Phase 13 D3: a duplicate name is suffixed ("MacBook 2"),
            # never refused (pairing must not fail on a label).
            name, name_key = unique_node_name(
                device.name[:NODE_NAME_MAX_CHARS], taken_label_keys(device.user)
            )
            if name != device.name:
                TrustedDevice.objects.filter(pk=device.pk).update(name=name)
                device.name = name
            node = Node.objects.create(
                id=device.pk,
                device=device,
                owner=device.user,
                workspace=workspace,
                name=name,
                name_key=name_key,
                platform=platform,
                protocol_version=PROTOCOL_VERSION,
                pinned_key_id=keyring.key_id,
            )
            record_audit(
                action="nodes.node.paired",
                actor=device.user,
                workspace=workspace,
                resource=node,
                metadata={
                    "node_id": str(node.pk),
                    "name_key": name_key,
                    "platform": platform,
                    "pinned_key_id": keyring.key_id,
                    "protocol_version": PROTOCOL_VERSION,
                    "client_ip": client_ip or "",
                },
            )
    if refusal is not None:
        raise refusal
    return PairingResult(node=node, node_secret=raw_secret, keyring=keyring)


def read_invoke_ttl_seconds() -> int:
    """NODE_READ_INVOKE_TTL_SECONDS, never above the protocol ceiling."""
    return max(1, min(int(settings.NODE_READ_INVOKE_TTL_SECONDS), MAX_READ_INVOKE_TTL_SECONDS))


def sign_read_invoke(*, node, workspace_id, capability, args, trace_id, keyring=None, now=None):
    """A signed v1 read `invoke` for `node` (D5). Raises KeyError for a
    capability the server has no contract for, and
    `nodes.keys.SigningKeyUnavailable` without a usable key. Validation of
    `args` against the contract and the snapshot check are `invoke_read`'s
    (branch 4/5); this only builds and signs."""
    contract = get_contract(capability)
    if contract is None:
        raise KeyError(capability)
    keyring = keyring or signing_keyring()
    envelope = build_invoke(
        node_id=node.pk,
        workspace_id=workspace_id,
        capability=contract.name,
        capability_version=contract.version,
        args=args,
        trace_id=trace_id,
        issued_at=int(now if now is not None else time.time()),
        ttl_seconds=read_invoke_ttl_seconds(),
    )
    return keyring.sign(envelope)


# --- capability snapshots (Phase 9 D6; Phase 10 D1/D3 classes) ---


def classify_catalogue(catalogue, *, protocol_version) -> list:
    """The `CapabilitySnapshot.entries` for one (schema-valid) `hello`
    catalogue on a session of `protocol_version`: per entry its name,
    version, class, availability, the sha256 of both schemas, `invocable`,
    `reason` (None or a `NOT_INVOCABLE_REASONS` code) and the session's
    `protocol_version`. Never the schemas' content.

    Confirm- and gate-class entries are invocable only on a v2 session
    whose entry matches the server's contract on name, version, class and
    both schema hashes; on a v1 session they are `not_invocable` with
    reason `protocol_v1` (D1)."""
    if protocol_version not in PROTOCOL_VERSIONS:
        raise UnsupportedProtocolVersion()
    entries = []
    for entry in catalogue:
        reason = check_catalogue_entry(entry, protocol_version=protocol_version)
        entries.append(
            {
                "name": entry.get("name"),
                "version": entry.get("version"),
                "class": entry.get("class"),
                "availability": entry.get("availability"),
                "args_schema_sha256": _schema_sha256_or_none(entry.get("args_schema")),
                "result_schema_sha256": _schema_sha256_or_none(entry.get("result_schema")),
                "invocable": reason is None,
                "reason": reason,
                "protocol_version": protocol_version,
            }
        )
    return entries


def _schema_sha256_or_none(schema):
    try:
        return sha256_hex(schema)
    except CanonicalizationError:
        return None


def snapshot_protocol_version(snapshot) -> int | None:
    """The session version a snapshot was classified for (stored per
    entry, so no column is needed); None for an empty catalogue."""
    versions = {entry.get("protocol_version", 1) for entry in snapshot.entries or ()}
    return versions.pop() if len(versions) == 1 else None


def record_capability_snapshot(
    *, node, catalogue, catalogue_sha256, protocol_version, node_version="", os_version=""
):
    """Write a `CapabilitySnapshot` when the catalogue hash **or** the
    session's protocol version changed since the node's latest snapshot
    (a node that reconnects on v2 with the same catalogue must not keep its
    v1 `protocol_v1` verdicts, and the reverse), or when the latest
    snapshot's entries differ from this `hello`'s verdicts (a
    `grant.availability_changed` successor, Phase 9 branch 6, keeps the
    hello's hash: a node that reconnects with the catalogue it advertised
    before the change must not inherit the changed flags). Returns the
    snapshot in force. Called by the gateway on `hello` (Phase 9 branch 4)."""
    entries = classify_catalogue(catalogue, protocol_version=protocol_version)
    latest = CapabilitySnapshot.objects.filter(node=node).order_by("-created_at").first()
    if (
        latest is not None
        and latest.catalogue_sha256 == catalogue_sha256
        and snapshot_protocol_version(latest) in (protocol_version, None)
        and list(latest.entries or ()) == entries
    ):
        return latest
    return CapabilitySnapshot.objects.create(
        workspace=node.workspace,
        node=node,
        catalogue_sha256=catalogue_sha256,
        entries=entries,
        node_version=node_version,
        os_version=os_version,
    )


def invocable_entry(snapshot, contract):
    """The snapshot entry that lets the server invoke `contract`, or None:
    it must be `invocable` and equal the contract on name, version, class
    and both schema hashes (Phase 10 D2 re-checks this before signing a
    grant)."""
    if snapshot is None:
        return None
    for entry in snapshot.entries or ():
        if (
            entry.get("invocable")
            and entry.get("name") == contract.name
            and entry.get("version") == contract.version
            and entry.get("class") == contract.capability_class
            and entry.get("args_schema_sha256") == contract.args_schema_sha256
            and entry.get("result_schema_sha256") == contract.result_schema_sha256
            and (
                not contract.is_action
                or entry.get("protocol_version", 1) >= contract.min_protocol_version
            )
        ):
            return entry
    return None


# --- Phase 10 branch 3: confirm- and gate-class actions (D2, D6, D7, D8) ---

ACTION_CHANNEL = "web"  # every action starts with the owner's web act (D6, D8)
CONFIRM_TTL_SECONDS = 90  # the confirm-class envelope's lifetime (D8)
WATCHDOG_MARGIN_SECONDS = 30  # D7: deadline = expires_at + max_runtime + 30 s
# The beat backstop (`sweep_node_actions`) waits this much longer than the
# gateway's own watchdog, so the gateway (which can flush the scrub carry)
# normally wins.
SWEEP_GRACE_SECONDS = 60
NODE_REFUSED_EVENT = "nodes.refused"
NODE_INVOKE_EVENT = "nodes.invoke"
GATEWAY_TERMINALS = frozenset({"timeout", "node_disconnected"})

# Node-reported refusal codes that are their own execution statuses.
_REFUSAL_STATUS = {"owner_denied": "denied", "confirm_timeout": "confirm_timeout"}
# The node's `confirmation.state` -> the execution status it moves to.
_CONFIRMATION_STATUS = {"shown": "confirming", "approved": "running"}
# Phase 13 D6: a click on the node's own confirmation window (either way)
# shows the owner at that PC -> `last_active_at`. `shown`, `timed_out` and
# `closed` are not clicks.
_CLICK_STATES = frozenset({"approved", "denied"})


_terminal_hook = None


def register_terminal_hook(fn) -> None:
    """`fn(execution)` runs after the commit of every gated execution's
    terminal state (the `node_action` tool's work-log entry, ADR-008
    decision 4). Registered by `tools.node_actions`; a failing hook is
    logged and never changes the execution."""
    global _terminal_hook
    _terminal_hook = fn


def _run_terminal_hook(execution_id):
    if _terminal_hook is None:
        return
    execution = (
        NodeActionExecution.objects.select_related("approval", "workspace")
        .filter(pk=execution_id)
        .first()
    )
    if execution is None or execution.approval_id is None:
        return
    try:
        _terminal_hook(execution)
    except Exception:
        logger.exception("node action terminal hook failed execution=%s", execution_id)


class ActionRefused(Exception):
    """An action request refused before an execution exists (`execution`
    None: a validation error, nothing written) or ended in a terminal
    execution. `code` is a closed token; the message never carries args."""

    def __init__(self, code, execution=None):
        super().__init__(code)
        self.code = code
        self.execution = execution


class NodeNotFound(ActionRefused):
    """Unknown node, or one the actor does not own: one answer for both
    (`actor_not_owner`), so a non-owner learns nothing (D6, D8)."""

    def __init__(self):
        super().__init__("actor_not_owner")


def find_node(node_id):
    """The Node with `node_id` (an unscoped system lookup by primary key),
    or None for an unknown or malformed id. Callers run the actor check
    before anything else."""
    try:
        pk = uuid.UUID(str(node_id))
    except (TypeError, ValueError, AttributeError):
        return None
    return Node.objects.select_related("owner", "device", "workspace").filter(pk=pk).first()


def latest_snapshot(node):
    return CapabilitySnapshot.objects.filter(node=node).order_by("-created_at").first()


def _trace_id():
    return uuid.uuid4().hex


def _args_hash(args):
    try:
        return sha256_hex(args)
    except (CanonicalizationError, TypeError, ValueError):
        return sha256_hex(None)


def _human(actor):
    return actor if getattr(actor, "is_authenticated", False) else None


# --- reads over the invoke bus (Phase 9 D6, D7; branch 4) ---

READ_CHANNEL = "web"
# Capabilities a system-initiated read (`channel` other than web) may
# invoke (Phase 8B D13 amendment to D7). Empty until branch 5 moves the two
# owner-turn contracts into `nodes/capabilities/v1/`: until then every
# non-web read is refused `not_invocable`.
SYSTEM_READ_CAPABILITIES = frozenset()
# The gateway's own verdicts on the result key -> (status, refusal_code);
# the node's result frames are mapped by `_read_outcome`.
GATEWAY_READ_VERDICTS = {
    "node_disconnected": ("node_disconnected", ""),
    "node_mismatch": ("server_refused", "node_mismatch"),
    "result_schema": ("error", "result_schema"),
    "result_too_large": ("error", "result_too_large"),
}


@dataclass(frozen=True)
class ReadOutcome:
    """The answer of one node read (D7). `output` is set only for `ok`: the
    validated result, returned to the caller and stored nowhere."""

    status: str
    refusal_code: str = ""
    output: object = None
    truncated: bool = False
    output_sha256: str = ""
    output_bytes: int = 0
    duration_ms: int = 0
    invocation_id: str = ""
    trace_id: str = ""
    invocation: object = None
    # Rows the caller's `row_filter` dropped (D9, Q5): a count only.
    dropped: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def invoke_timeout_seconds() -> int:
    """NODE_INVOKE_TIMEOUT_SECONDS, always below the read envelope's TTL."""
    timeout = max(1, int(settings.NODE_INVOKE_TIMEOUT_SECONDS))
    return min(timeout, max(1, read_invoke_ttl_seconds() - 1))


def max_inflight() -> int:
    return max(1, int(settings.NODE_MAX_INFLIGHT))


def _bounded_trace_id(trace_id):
    return str(trace_id or "")[:64] or _trace_id()


def _record_read(
    *,
    node,
    workspace,
    actor,
    channel,
    capability,
    capability_version,
    args_sha256,
    trace_id,
    status,
    refusal_code="",
    nonce="",
    output_sha256="",
    output_bytes=0,
    truncated=False,
    duration_ms=0,
    dropped_unmapped=0,
):
    """D7 steps 7-8: exactly one chain row and one `nodes.invoke` AuditLog
    row for one read at its terminal state. Hashes, sizes and codes only:
    never args, never output. A `(status, refusal_code)` pair outside the
    read vocabulary (D7, `READ_TERMINAL_VOCABULARY`) is a programming error."""
    if refusal_code not in READ_TERMINAL_VOCABULARY.get(status, ()):
        raise ValueError(f"not a read terminal state: {status!r}/{refusal_code!r}")
    not_sent = status in NOT_SENT_STATUSES
    with transaction.atomic():
        row = audit_chain.append(
            workspace=workspace,
            node=node,
            capability=capability,
            capability_version=capability_version,
            args_sha256=args_sha256,
            status=status,
            channel=channel,
            trace_id=trace_id,
            actor=_human(actor),
            nonce="" if not_sent else nonce,
            refusal_code=refusal_code,
            output_sha256="" if not_sent else output_sha256,
            output_bytes=0 if not_sent else min(int(output_bytes), 2**31 - 1),
            truncated=False if not_sent else bool(truncated),
            dropped_unmapped=dropped_unmapped,
            duration_ms=max(0, min(int(duration_ms), 3_600_000)),
        )
        record_audit(
            action=NODE_INVOKE_EVENT,
            actor=_human(actor),
            workspace=workspace,
            resource=row,
            metadata={
                "node_id": str(node.pk),
                "capability": capability,
                "capability_version": capability_version,
                "class": "read",
                "args_sha256": args_sha256,
                "nonce": row.nonce,
                "status": status,
                "refusal_code": refusal_code,
                "duration_ms": row.duration_ms,
                "output_bytes": row.output_bytes,
                "dropped_unmapped": row.dropped_unmapped,
                "trace_id": trace_id,
                "channel": channel,
            },
        )
    return row


def refuse_read(
    *, node, capability, args, workspace, actor, trace_id, code, channel=READ_CHANNEL
) -> ReadOutcome:
    """A caller's own pre-invoke refusal (D7, D9: `path_not_in_workspace`,
    `workspace_not_allowed`, `session_not_found`): nothing is signed or
    sent; the chain row and the AuditLog row are written as for any call.
    The args are hashed, never stored. `code` must be one of
    `CALLER_REFUSAL_CODES` (a programming error otherwise)."""
    if code not in CALLER_REFUSAL_CODES:
        raise ValueError(f"not a caller refusal code: {code!r}")
    contract = get_contract(capability) if isinstance(capability, str) else None
    trace_id = _bounded_trace_id(trace_id)
    row = _record_read(
        node=node,
        workspace=workspace,
        actor=actor,
        channel=channel,
        capability=str(capability)[:64],
        capability_version=contract.version if contract is not None else 0,
        args_sha256=_args_hash(args),
        trace_id=trace_id,
        status="server_refused",
        refusal_code=code,
    )
    return ReadOutcome(
        status="server_refused", refusal_code=code, trace_id=trace_id, invocation=row
    )


def _read_outcome(payload, contract):
    """A result-key payload (the node's result, or a gateway verdict) ->
    (status, refusal_code, output, truncated). The output is validated
    again here against the server's own contract (the gateway already did;
    this is the caller's own D7 step 6)."""
    verdict = payload.get("verdict")
    if verdict is not None:
        status, code = GATEWAY_READ_VERDICTS.get(verdict, ("error", "node_error"))
        return status, code, None, False
    status = payload.get("status")
    code = payload.get("refusal_code") or ""
    if status == "ok":
        output = payload.get("output")
        try:
            contract.validate_result(output)
        except ProtocolError as exc:
            return "error", exc.code, None, False
        return "ok", "", output, bool(payload.get("truncated"))
    if status == "refused":
        if code in NODE_REFUSED_CODES:
            return "refused", code, None, False
        return "error", "node_error", None, False
    if status == "timeout":
        return "timeout", "", None, False
    return "error", "node_error", None, False


def invoke_read(
    *,
    node,
    capability,
    args,
    workspace,
    actor,
    trace_id=None,
    channel=READ_CHANNEL,
    keyring=None,
    now=None,
    row_filter=None,
) -> ReadOutcome:
    """One read of a node capability, synchronously (D7). Runs in any
    process (web, worker): the gateway process that holds the node's socket
    forwards the envelope and pushes the answer back over Redis (D6).

    Order: the server contract and the node's latest snapshot
    (`server_refused:not_invocable`), the args (`args_contract`), presence
    (`node_offline`: nothing queued), the shared in-flight cap (`busy`), the
    signing key (`signing_unavailable`), the push (`bus_unavailable`), then
    a wait of at most `NODE_INVOKE_TIMEOUT_SECONDS` (`timeout`). The answer
    is `ok` (output valid against the server's `result_schema`), `refused`
    (a D5 node code), `error` (`node_error` / `result_schema` /
    `result_too_large`), `node_disconnected` or `server_refused:node_mismatch`.
    Never retried (ADR-013 decision 6).

    Every call writes exactly one `NodeInvocation` chain row and one
    `nodes.invoke` AuditLog row, sent or not; neither holds args or output.
    The output is returned to the caller and stored nowhere.

    **Step 0** (Phase 8B D13 amendment, branch 5): for a capability whose
    contract has `data_class: personal`, the actor must be active, human,
    the node's owner and an `owner` of the target workspace; otherwise
    `server_refused:actor_not_owner`, before anything is signed.

    `row_filter(output) -> (output, dropped)` (D9, branch 5) runs on a valid
    `ok` output before the chain row is written: the caller's row filter
    (rows of another workspace or unmapped are dropped, aggregates
    recomputed). The returned output is what the caller gets; `dropped` is
    recorded on the chain row (`dropped_unmapped`, Q5) and the AuditLog row.
    `output_sha256` / `output_bytes` stay those of the node's own answer. A
    filter that raises ends the read `error:result_schema` (nothing of the
    output is returned).

    The D9 workspace pre-checks live in `tools` (they call `refuse_read`);
    a system-channel read is limited to `SYSTEM_READ_CAPABILITIES`."""
    deny_if_unattended("node_invocation")
    started = time.monotonic()
    trace_id = _bounded_trace_id(trace_id)
    contract = get_contract(capability) if isinstance(capability, str) else None
    common = {
        "node": node,
        "workspace": workspace,
        "actor": actor,
        "channel": channel,
        "capability": contract.name if contract is not None else str(capability)[:64],
        "capability_version": contract.version if contract is not None else 0,
        "args_sha256": _args_hash(args),
        "trace_id": trace_id,
    }

    def finish(status, code="", *, invocation_id="", output=None, dropped=0, **sent):
        duration = int((time.monotonic() - started) * 1000)
        row = _record_read(
            status=status,
            refusal_code=code,
            duration_ms=duration,
            dropped_unmapped=max(0, min(int(dropped), 2**31 - 1)),
            **common,
            **sent,
        )
        return ReadOutcome(
            dropped=row.dropped_unmapped,
            status=status,
            refusal_code=code,
            output=output,
            truncated=row.truncated,
            output_sha256=row.output_sha256,
            output_bytes=row.output_bytes,
            duration_ms=row.duration_ms,
            invocation_id=invocation_id,
            trace_id=trace_id,
            invocation=row,
        )

    if contract is None or contract.capability_class != "read":
        return finish("server_refused", "not_invocable")
    if contract.data_class == "personal" and not actor_is_node_owner(actor, node, workspace):
        return finish("server_refused", "actor_not_owner")
    if channel != READ_CHANNEL and contract.name not in SYSTEM_READ_CAPABILITIES:
        return finish("server_refused", "not_invocable")
    if invocable_entry(latest_snapshot(node), contract) is None:
        return finish("server_refused", "not_invocable")
    try:
        contract.validate_args(args)
    except ProtocolError:
        return finish("server_refused", "args_contract")
    try:
        version = bus.present_version(node.pk)
    except bus.BusUnavailable:
        return finish("bus_unavailable")
    if version is None:
        return finish("node_offline")
    timeout = invoke_timeout_seconds()
    try:
        acquired = bus.acquire_inflight(
            node.pk, max_inflight(), read_invoke_ttl_seconds() + timeout + 30
        )
    except bus.BusUnavailable:
        return finish("bus_unavailable")
    if not acquired:
        return finish("busy")
    try:
        try:
            keyring = keyring or signing_keyring()
        except SigningKeyUnavailable:
            return finish("server_refused", "signing_unavailable")
        envelope = keyring.sign(
            build_invoke(
                node_id=node.pk,
                workspace_id=workspace.pk,
                capability=contract.name,
                capability_version=contract.version,
                args=args,
                trace_id=trace_id,
                issued_at=int(now if now is not None else time.time()),
                ttl_seconds=read_invoke_ttl_seconds(),
                version=version,
            )
        )
        invocation_id = envelope["invocation_id"]
        try:
            bus.push_invoke(node.pk, envelope, now=now)
        except bus.BusUnavailable:
            return finish("bus_unavailable", invocation_id=invocation_id)
        try:
            payload = bus.wait_result(invocation_id, timeout)
        except bus.BusUnavailable:
            payload = None  # sent, but no answer can come back: a timeout
        nonce = envelope["nonce"]
        if payload is None:
            return finish("timeout", invocation_id=invocation_id, nonce=nonce)
        status, code, output, truncated = _read_outcome(payload, contract)
        if status in NOT_SENT_STATUSES:
            return finish(status, code, invocation_id=invocation_id)
        if status != "ok":
            return finish(status, code, invocation_id=invocation_id, nonce=nonce)
        received = {
            "nonce": nonce,
            "output_sha256": sha256_hex(output),
            "output_bytes": len(canonicalize(output)),
            "truncated": truncated,
        }
        dropped = 0
        if row_filter is not None:
            try:
                output, dropped = row_filter(output)
            except Exception:
                logger.exception("node read row filter failed capability=%s", contract.name)
                return finish("error", "result_schema", invocation_id=invocation_id, **received)
        return finish("ok", invocation_id=invocation_id, output=output, dropped=dropped, **received)
    finally:
        try:
            bus.release_inflight(node.pk)
        except bus.BusUnavailable:
            logger.warning("node in-flight slot not released node=%s", node.pk)


def record_actor_refusal(*, actor, node_id, capability, workspace=None):
    """One `nodes.refused` AuditLog row for a refused actor check, so
    probing for other users' nodes is visible (D6). The node id is recorded
    as given (bounded), never args."""
    record_audit(
        action=NODE_REFUSED_EVENT,
        actor=_human(actor),
        workspace=workspace,
        metadata={
            "node_id": str(node_id)[:64],
            "capability": str(capability)[:64],
            "refusal_code": "actor_not_owner",
        },
    )


def watchdog_deadline(execution) -> int | None:
    """D7: `expires_at` + the contract's `max_runtime_seconds` + 30 s, in
    Unix seconds (None before anything was signed)."""
    if execution.expires_at is None:
        return None
    contract = get_action_contract(execution.capability)
    runtime = (contract.max_runtime_seconds or 0) if contract is not None else 0
    return int(execution.expires_at) + int(runtime) + WATCHDOG_MARGIN_SECONDS


def _duration_ms(execution, now):
    start = execution.started_at or execution.created_at
    if start is None:
        return 0
    return max(0, min(int((now - start).total_seconds() * 1000), 3_600_000))


def finish_execution(
    execution,
    *,
    status,
    refusal_code="",
    confirm_state=None,
    exit_code=None,
    output_bytes=None,
    tail=None,
    truncated=None,
    sandbox_violation=None,
):
    """Move a locked, non-terminal execution to its terminal `status` and
    write exactly one `NodeInvocation` chain row (hash version 2) and one
    `nodes.invoke` AuditLog row (D7). `tail` must already be server-scrubbed
    (`record_gated_progress` does that). Idempotent on a terminal row.
    Returns the execution."""
    if execution.status in EXECUTION_TERMINAL_STATUSES:
        return execution
    now = timezone.now()
    not_sent = status in NOT_SENT_STATUSES
    execution.status = status
    execution.refusal_code = refusal_code or ""
    if confirm_state is not None:
        execution.confirm_state = confirm_state
    if exit_code is not None:
        execution.exit_code = exit_code
    if output_bytes is not None:
        execution.output_bytes = output_bytes
    if tail is not None:
        execution.tail = tail
        execution.output_sha256 = sha256_hex(tail) if tail else ""
    if truncated is not None:
        execution.truncated = bool(truncated)
    if sandbox_violation is not None:
        execution.sandbox_violation = bool(sandbox_violation)
    execution.finished_at = now
    execution.save()
    audit_chain.append(
        workspace=execution.workspace,
        node=execution.node,
        capability=execution.capability,
        capability_version=execution.capability_version,
        args_sha256=execution.args_sha256,
        status=status,
        channel=ACTION_CHANNEL,
        trace_id=execution.trace_id,
        actor=execution.actor,
        nonce="" if not_sent else execution.nonce,
        refusal_code=execution.refusal_code,
        output_sha256="" if not_sent else execution.output_sha256,
        output_bytes=0 if not_sent else min(execution.output_bytes, 2**31 - 1),
        truncated=False if not_sent else execution.truncated,
        duration_ms=_duration_ms(execution, now),
        invocation_class=execution.capability_class,
        approval_id=execution.approval_id,
        grant_id=None if not_sent else execution.grant_id,
        exit_code=execution.exit_code,
        confirm_state=execution.confirm_state,
    )
    record_audit(
        action=NODE_INVOKE_EVENT,
        actor=execution.actor,
        workspace=execution.workspace,
        resource=execution,
        metadata={
            "capability": execution.capability,
            "capability_version": execution.capability_version,
            "class": execution.capability_class,
            "args_sha256": execution.args_sha256,
            "nonce": "" if not_sent else execution.nonce,
            "status": status,
            "refusal_code": execution.refusal_code,
            "approval_id": str(execution.approval_id or ""),
            "grant_id": "" if not_sent else str(execution.grant_id or ""),
            "exit_code": execution.exit_code,
            "confirm_state": execution.confirm_state,
            "output_bytes": execution.output_bytes,
            "trace_id": execution.trace_id,
            "channel": ACTION_CHANNEL,
        },
    )
    if execution.approval_id is not None:
        execution_id = execution.pk
        transaction.on_commit(lambda: _run_terminal_hook(execution_id))
    return execution


def _locked_execution(**filters):
    return (
        NodeActionExecution.objects.select_for_update(of=("self",))
        .select_related("workspace", "node", "actor")
        .filter(**filters)
        .first()
    )


def _finish_locked(pk, *, status, refusal_code="", only_from=None):
    with transaction.atomic():
        locked = _locked_execution(pk=pk)
        if locked is None or (only_from is not None and locked.status != only_from):
            return locked
        return finish_execution(locked, status=status, refusal_code=refusal_code)


def create_gated_execution(approval_request):
    """`node_action`'s `create_execution` (D6): the `approved` execution
    row, created inside the approval's own transaction. Returns its id."""
    deny_if_unattended("node_invocation")
    payload = approval_request.payload if isinstance(approval_request.payload, dict) else {}
    node = find_node(payload.get("node_id"))
    if node is None:
        # Unreachable through `prepare_payload`, which refused an unknown
        # node; a forged row never creates an execution against nothing.
        raise ActionRefused("actor_not_owner")
    capability_version = payload.get("capability_version")
    execution = NodeActionExecution.objects.create(
        workspace=approval_request.workspace,
        approval=approval_request,
        node=node,
        capability=str(payload.get("capability") or "")[:64],
        capability_version=capability_version if isinstance(capability_version, int) else 0,
        capability_class="gate",
        args_sha256=str(payload.get("args_sha256") or "")[:64],
        source_message_id=str(payload.get("source_message_id") or "")[:64],
        status=NodeActionExecution.Status.APPROVED,
        trace_id=_trace_id(),
        actor=approval_request.requested_by or approval_request.decided_by,
    )
    return execution.pk


def dispatch_gated(execution_id, *, now=None, keyring=None):
    """The body of `nodes.tasks.run_node_action` (D7): lock the row, run
    the D2 refusals, issue the grant, push the signed envelope to the node's
    inbox and return -- never wait for the result (the gateway owns the
    rest). Nothing is retried: a second call for the same execution finds
    it no longer `approved` and does nothing. Returns the execution."""
    from nodes import grants  # grants imports this module

    Status = NodeActionExecution.Status
    with transaction.atomic():
        execution = (
            NodeActionExecution.objects.select_for_update(of=("self",))
            .select_related("approval", "node", "node__owner", "node__device", "workspace")
            .filter(pk=execution_id)
            .first()
        )
        if execution is None:
            return None
        if execution.status != Status.APPROVED or execution.grant_issued_at is not None:
            return execution
        try:
            envelope = grants.issue(execution, now=now, keyring=keyring)
        except grants.GrantRefused as refusal:
            return finish_execution(execution, status=refusal.status, refusal_code=refusal.code)
        execution.status = Status.GRANTED
        execution.save()
    try:
        bus.push_invoke(execution.node_id, envelope, now=now)
    except bus.BusUnavailable:
        return _finish_locked(
            execution.pk,
            status="server_refused",
            refusal_code="bus_unavailable",
            only_from=Status.GRANTED,
        )
    with transaction.atomic():
        locked = _locked_execution(pk=execution.pk)
        if locked is not None and locked.status == Status.GRANTED:
            locked.status = Status.SENT
            locked.save(update_fields=["status", "updated_at"])
        return locked


def invoke_confirm(
    *, actor, node_id, capability, args, source_message_id="", now=None, keyring=None
):
    """The confirm-class path (D8): `apps.open` / `apps.focus`, started by
    the owner's card click and confirmed by the node's window click. No
    `ApprovalRequest`. Only `nodes.views.NodeActionListView` calls this (a
    source test pins it): the router, tools and drivers cannot.

    Order: the actor check (an unknown or foreign node raises NodeNotFound
    after one `nodes.refused` AuditLog row, plus one chain row for a node
    that exists); the server contract (a confirm-class capability with
    valid args, else ActionRefused with nothing written, like any other
    validation error); then the server-side outcomes, each a terminal
    execution with its chain and AuditLog rows: the workspace rule (personal
    only), the snapshot, the device / kill switch, presence, the signing
    key. Otherwise the envelope (`class` confirm, `grant` null, 90 s) is
    signed and pushed to the node's inbox, and the `sent` execution is
    returned."""
    deny_if_unattended("node_invocation")
    now = int(now if now is not None else time.time())
    node = find_node(node_id)
    if node is None or not actor_is_node_owner(actor, node, node.workspace):
        record_actor_refusal(
            actor=actor,
            node_id=node_id,
            capability=capability,
            workspace=node.workspace if node is not None else None,
        )
        if node is not None:
            audit_chain.append(
                workspace=node.workspace,
                node=node,
                capability=str(capability)[:64],
                capability_version=0,
                args_sha256=_args_hash(args),
                status="server_refused",
                refusal_code="actor_not_owner",
                channel=ACTION_CHANNEL,
                trace_id=_trace_id(),
                actor=_human(actor),
                invocation_class="confirm",
            )
        raise NodeNotFound()
    contract = get_action_contract(capability) if isinstance(capability, str) else None
    if contract is None or contract.capability_class != "confirm":
        raise ActionRefused("not_confirm_class")
    try:
        contract.validate_args(args)
    except ProtocolError:
        raise ActionRefused("args_contract") from None
    workspace = node.workspace
    execution = NodeActionExecution.objects.create(
        workspace=workspace,
        node=node,
        capability=contract.name,
        capability_version=contract.version,
        capability_class="confirm",
        args_sha256=sha256_hex(args),
        source_message_id=str(source_message_id or "")[:64],
        status=NodeActionExecution.Status.SENT,
        trace_id=_trace_id(),
        actor=actor,
    )

    def refuse(status, code=""):
        return _finish_locked(execution.pk, status=status, refusal_code=code)

    refusal = workspace_refusal(contract, args, workspace, node.owner)
    if refusal is not None:
        return refuse("server_refused", refusal)
    if invocable_entry(latest_snapshot(node), contract) is None:
        return refuse("server_refused", "not_invocable")
    if node_device_refusal(node.device) is not None:
        return refuse("server_refused", "kill_switch")
    try:
        if not bus.is_present(node.pk):
            return refuse("node_offline")
    except bus.BusUnavailable:
        return refuse("server_refused", "bus_unavailable")
    try:
        keyring = keyring or signing_keyring()
    except SigningKeyUnavailable:
        return refuse("server_refused", "signing_unavailable")
    envelope = keyring.sign(
        build_invoke(
            node_id=node.pk,
            workspace_id=workspace.pk,
            capability=contract.name,
            capability_version=contract.version,
            args=args,
            trace_id=execution.trace_id,
            issued_at=now,
            ttl_seconds=CONFIRM_TTL_SECONDS,
            capability_class="confirm",
            version=2,
        )
    )
    execution.invocation_id = envelope["invocation_id"]
    execution.nonce = envelope["nonce"]
    execution.expires_at = envelope["expires_at"]
    execution.save()
    try:
        bus.push_invoke(node.pk, envelope, now=now)
    except bus.BusUnavailable:
        return refuse("server_refused", "bus_unavailable")
    return execution


def _result_outcome(result):
    """A schema-valid v2 `result` -> its (status, refusal_code) (D7)."""
    status = result.get("status")
    code = result.get("refusal_code") or ""
    if status == "ok":
        exit_code = result.get("exit_code")
        return ("failed", "") if exit_code not in (None, 0) else ("ok", "")
    if status == "refused":
        if code in _REFUSAL_STATUS:
            return _REFUSAL_STATUS[code], code
        if code in NODE_REFUSED_CODES_V2:
            return "refused", code
        return "failed", "node_error"
    if status == "timeout":
        return "timeout", ""
    return "failed", "node_error"


def record_gated_progress(
    *, node_id, invocation_id, confirmation=None, result=None, terminal=None, output_bytes=None
):
    """Move one in-flight action execution (the gateway's side, D7).
    Exactly one of `confirmation` (a node `confirmation.state`), `result` (a
    schema-valid v2 `result` frame) or `terminal` (`timeout` /
    `node_disconnected`, the gateway's own verdicts) is given. The row is
    found by invocation id **and** node id; an unknown or finished
    invocation returns None (the caller counts the frame as discarded).

    The node's `tail` is never trusted: it is re-scrubbed here with the
    registered output scrubber and the workspace's terms
    (`nodes.output.scrub_tail`; no scrubber = the fail-closed marker)
    before it is saved."""
    if sum(value is not None for value in (confirmation, result, terminal)) != 1:
        raise ValueError("exactly one of confirmation, result, terminal")
    if terminal is not None and terminal not in GATEWAY_TERMINALS:
        raise ValueError("unknown gateway terminal")
    Status = NodeActionExecution.Status
    with transaction.atomic():
        execution = _locked_execution(invocation_id=invocation_id, node_id=node_id)
        if execution is None or execution.status in EXECUTION_TERMINAL_STATUSES:
            return None
        if execution.status == Status.APPROVED:
            return None  # nothing was sent for it yet
        if confirmation is not None:
            if confirmation in _CLICK_STATES:
                mark_node_active(execution.node_id)
            target = _CONFIRMATION_STATUS.get(confirmation)
            if target is not None:
                execution.status = target
                if target == Status.RUNNING:
                    execution.confirm_state = "approved"
                    execution.started_at = execution.started_at or timezone.now()
            else:
                execution.confirm_state = confirmation
            execution.save()
            return execution
        if terminal is not None:
            return finish_execution(execution, status=terminal, output_bytes=output_bytes)
        status, code = _result_outcome(result)
        if result.get("confirm_state") in _CLICK_STATES:
            mark_node_active(execution.node_id)
        exit_code = result.get("exit_code")
        return finish_execution(
            execution,
            status=status,
            refusal_code=code,
            confirm_state=result.get("confirm_state") or "",
            exit_code=exit_code if isinstance(exit_code, int) else None,
            output_bytes=output_bytes,
            tail=scrub_tail(result.get("tail"), execution.workspace),
            truncated=result.get("truncated"),
            sandbox_violation=result.get("sandbox_violation"),
        )


def execution_for_session(*, node_id, invocation_id):
    """The in-flight execution an inbox envelope belongs to, or None (the
    gateway drops envelopes it cannot place)."""
    execution = (
        NodeActionExecution.objects.select_related("workspace")
        .filter(invocation_id=invocation_id)
        .first()
    )
    if execution is None or execution.status in EXECUTION_TERMINAL_STATUSES:
        return None
    if str(execution.node_id) != str(node_id):
        return None
    return execution


def refuse_misrouted(invocation_id):
    """An inbox envelope whose `node_id` is not the socket's node is
    dropped, and its execution ends `server_refused:node_mismatch` (Phase 9
    D6: the caller is resolved, nothing is forwarded)."""
    try:
        invocation_uuid = uuid.UUID(str(invocation_id))
    except (TypeError, ValueError, AttributeError):
        return None
    with transaction.atomic():
        execution = _locked_execution(invocation_id=invocation_uuid)
        if execution is None or execution.status in EXECUTION_TERMINAL_STATUSES:
            return None
        return finish_execution(execution, status="server_refused", refusal_code="node_mismatch")


def sweep_node_actions(*, now=None):
    """The beat backstop (every minute): an `approved` execution whose
    approval was decided more than `NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS`
    ago (its dispatch task never ran) fails `stale_dispatch`; an in-flight
    one past its watchdog deadline plus a grace (its gateway is gone) ends
    `timeout`. Returns the number of rows finished."""
    from nodes.grants import dispatch_max_delay_seconds

    Status = NodeActionExecution.Status
    current = timezone.now() if now is None else now
    finished = 0
    stale_before = current - timedelta(seconds=dispatch_max_delay_seconds())
    stale_ids = NodeActionExecution.objects.filter(
        status=Status.APPROVED, approval__decided_at__lt=stale_before
    ).values_list("pk", flat=True)
    for pk in list(stale_ids):
        row = _finish_locked(pk, status="stale_dispatch", only_from=Status.APPROVED)
        finished += int(row is not None and row.status == "stale_dispatch")
    unix_now = int(current.timestamp())
    in_flight = NodeActionExecution.objects.exclude(
        status__in=EXECUTION_TERMINAL_STATUSES | {Status.APPROVED}
    )
    for row in list(in_flight.only("pk", "status", "expires_at", "capability")):
        deadline = watchdog_deadline(row)
        if deadline is None or unix_now <= deadline + SWEEP_GRACE_SECONDS:
            continue
        with transaction.atomic():
            locked = _locked_execution(pk=row.pk)
            if locked is not None and locked.status not in EXECUTION_TERMINAL_STATUSES:
                finish_execution(locked, status="timeout")
                finished += 1
    return finished


def clear_expired_tails(*, now=None):
    """Clear `tail` on executions finished more than
    `NODE_ACTION_TAIL_RETENTION_DAYS` ago (D7). Returns the count."""
    current = timezone.now() if now is None else now
    cutoff = current - timedelta(days=int(settings.NODE_ACTION_TAIL_RETENTION_DAYS))
    return (
        NodeActionExecution.objects.filter(finished_at__lt=cutoff)
        .exclude(tail="")
        .update(tail="", updated_at=current)
    )
