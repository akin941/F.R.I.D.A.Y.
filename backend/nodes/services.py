"""Node services (Phase 9 brief D4, D7).

Branch 2 holds pairing and envelope signing. `invoke_read` / `refuse_read`
(the invoke bus and the per-call chain + AuditLog rows) arrive with the
gateway (branch 4) and the read tools (branch 5).

Phase 10 branch 1 adds snapshot classification by protocol version and
class (`classify_catalogue`, `record_capability_snapshot`,
`invocable_entry`): confirm- and gate-class entries are invocable only on a
v2 session (D1, D3). Grant issuance and gated invocation are branch 3.
"""

import time
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from accounts.devices import DeviceTrustError, enroll_node_device
from audit.services import record_audit
from nodes.capabilities import check_catalogue_entry, get_contract
from nodes.keys import signing_keyring
from nodes.models import CapabilitySnapshot, Node
from nodes.protocol.canonical import CanonicalizationError, sha256_hex
from nodes.protocol.messages import (
    GATEWAY_PATH,
    MAX_READ_INVOKE_TTL_SECONDS,
    PROTOCOL_VERSION,
    PROTOCOL_VERSIONS,
)
from nodes.protocol.signing import KeyRing, build_invoke
from workspaces.models import Workspace

SUPPORTED_PROTOCOL_VERSIONS = frozenset({PROTOCOL_VERSION})


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


def pair_node(*, raw_code, node_name, protocol_versions, client_ip=None, user_agent=""):
    """Pair a Friday Node with a node pairing code (D4). Returns a
    PairingResult (the `jnd_` secret is in it once and stored nowhere).

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
            workspace = Workspace.objects.get(owner=device.user, type=Workspace.Type.PERSONAL)
            node = Node.objects.create(
                id=device.pk,
                device=device,
                owner=device.user,
                workspace=workspace,
                name=device.name,
                protocol_version=PROTOCOL_VERSION,
                pinned_key_id=keyring.key_id,
            )
            record_audit(
                action="nodes.node.paired",
                actor=device.user,
                workspace=workspace,
                resource=node,
                metadata={
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
    v1 `protocol_v1` verdicts, and the reverse). Returns the snapshot in
    force. Called by the gateway on `hello` (Phase 9 branch 4)."""
    entries = classify_catalogue(catalogue, protocol_version=protocol_version)
    latest = CapabilitySnapshot.objects.filter(node=node).order_by("-created_at").first()
    if (
        latest is not None
        and latest.catalogue_sha256 == catalogue_sha256
        and snapshot_protocol_version(latest) in (protocol_version, None)
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
