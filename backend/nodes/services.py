"""Node services (Phase 9 brief D4, D7).

Branch 2 holds pairing and envelope signing. `invoke_read` / `refuse_read`
(the invoke bus and the per-call chain + AuditLog rows) arrive with the
gateway (branch 4) and the read tools (branch 5).
"""

import time
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from accounts.devices import DeviceTrustError, enroll_node_device
from audit.services import record_audit
from nodes.capabilities import get_contract
from nodes.keys import signing_keyring
from nodes.models import Node
from nodes.protocol.messages import (
    GATEWAY_PATH,
    MAX_READ_INVOKE_TTL_SECONDS,
    PROTOCOL_VERSION,
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
