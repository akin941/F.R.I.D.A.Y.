"""Grant issuance (Phase 10 brief D2; ADR-013 decision 4).

`issue(execution)` is the only code that signs a gate-class envelope with a
grant. It is called by `nodes.services.dispatch_gated` with the
`NodeActionExecution` row locked (`select_for_update`), and it refuses --
signing nothing -- unless every condition below holds, checked in this
order:

1. **The actor check** (first): the approval's `requested_by` **and**
   `decided_by` are both the node's owner, who is active and still holds the
   `owner` role in the approval's workspace (`server_refused:
   actor_not_owner`). `approve_request`'s approver hook already refused a
   non-owner approver; this re-check covers forged rows and a role lost
   after approval.
2. The approval is `approved` and its tool is `node_action`
   (`approval_not_approved` / `wrong_tool`).
3. It was decided at most `NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS` ago,
   else the execution fails `stale_dispatch` ("nothing queued to run
   later" also holds for a worker backlog, D2/R8).
4. The stored snapshot is intact: its `args_sha256` equals the recomputed
   hash of its args and the execution's hash, and it names this node and
   capability (`args_changed`).
5. The server contract still accepts it: a known gate-class capability of
   that version whose `args_schema` the args match
   (`nodes.capabilities.check_gate_request`, before anything is built;
   `args_contract`).
6. The node is the one named and is present (`node_offline`), its device
   may act (not revoked, kill switch released: `kill_switch`), and its
   latest snapshot still has the capability invocable at the same version,
   class and schema hashes (`not_invocable`).
7. A `repo_path` still resolves to the approval's workspace, and a
   personal-class capability is still in the personal workspace
   (`path_not_in_workspace` / `workspace_not_allowed`).
8. A usable signing key exists (`signing_unavailable`).

Then the grant is built and signed (`nodes.protocol.grants.
build_gated_invoke`, TTL `NODE_GRANT_TTL_SECONDS` <= 120 s), and the row
records `grant_id`, `grant_issued_at`, `invocation_id`, `nonce` and
`expires_at` once. Single use is enforced three times: the node's nonce
cache, this row's `grant_issued_at` (a second dispatch finds the row no
longer `approved` and issues nothing), and the `OneToOne` between execution
and approval.
"""

import time

from django.conf import settings
from django.utils import timezone

from accounts.devices import node_device_refusal
from nodes import bus
from nodes.action_rules import actor_is_node_owner, workspace_refusal
from nodes.capabilities import check_gate_request
from nodes.keys import SigningKeyUnavailable, signing_keyring
from nodes.models import CapabilitySnapshot
from nodes.protocol.canonical import CanonicalizationError, sha256_hex
from nodes.protocol.grants import build_gated_invoke
from nodes.protocol.messages import MAX_GRANT_TTL_SECONDS, ProtocolError
from nodes.services import invocable_entry

NODE_ACTION_TOOL = "node_action"
APPROVED = "approved"


class GrantRefused(Exception):
    """`issue` refused; nothing was signed. `status` is the execution's
    terminal status (`server_refused`, `stale_dispatch`, `node_offline`),
    `code` its refusal code ("" for the code-less statuses)."""

    def __init__(self, status, code=""):
        super().__init__(f"{status}:{code}" if code else status)
        self.status = status
        self.code = code


def grant_ttl_seconds() -> int:
    """`NODE_GRANT_TTL_SECONDS`, never above the protocol ceiling (the boot
    check in `nodes.checks` refuses a larger setting outright)."""
    return max(1, min(int(settings.NODE_GRANT_TTL_SECONDS), MAX_GRANT_TTL_SECONDS))


def dispatch_max_delay_seconds() -> int:
    return max(1, int(settings.NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS))


def _refuse(code):
    raise GrantRefused("server_refused", code)


def _check_actor(execution, approval):
    node = execution.node
    owner = node.owner
    workspace = approval.workspace
    if (
        approval.requested_by_id != owner.pk
        or approval.decided_by_id != owner.pk
        or execution.actor_id != owner.pk
        or not actor_is_node_owner(owner, node, workspace)
    ):
        _refuse("actor_not_owner")


def _snapshot_args(execution, approval):
    payload = approval.payload if isinstance(approval.payload, dict) else {}
    args = payload.get("args")
    try:
        recomputed = sha256_hex(args)
    except (CanonicalizationError, TypeError, ValueError):
        _refuse("args_changed")
    if (
        recomputed != payload.get("args_sha256")
        or recomputed != execution.args_sha256
        or payload.get("node_id") != str(execution.node_id)
        or payload.get("capability") != execution.capability
        or payload.get("capability_version") != execution.capability_version
        or str(approval.workspace_id) != str(execution.workspace_id)
    ):
        _refuse("args_changed")
    return args


def issue(execution, *, now=None, keyring=None) -> dict:
    """Run the D2 refusals for a locked, `approved` execution and return
    the signed v2 gate-class envelope (grant embedded). Raises
    GrantRefused; on success the row's grant fields are set (the caller
    saves it)."""
    now = int(now if now is not None else time.time())
    approval = execution.approval
    if approval is None:
        _refuse("approval_not_approved")
    _check_actor(execution, approval)
    if approval.status != APPROVED or approval.decided_at is None:
        _refuse("approval_not_approved")
    if approval.tool_name != NODE_ACTION_TOOL:
        _refuse("wrong_tool")
    if now - int(approval.decided_at.timestamp()) > dispatch_max_delay_seconds():
        raise GrantRefused("stale_dispatch")
    args = _snapshot_args(execution, approval)
    try:
        contract = check_gate_request(execution.capability, execution.capability_version, args)
    except ProtocolError:
        _refuse("args_contract")
    node = execution.node
    try:
        present = bus.is_present(node.pk)
    except bus.BusUnavailable:
        _refuse("bus_unavailable")
    if not present:
        raise GrantRefused("node_offline")
    if node_device_refusal(node.device) is not None:
        _refuse("kill_switch")
    snapshot = CapabilitySnapshot.objects.filter(node=node).order_by("-created_at").first()
    if invocable_entry(snapshot, contract) is None:
        _refuse("not_invocable")
    refusal = workspace_refusal(contract, args, approval.workspace, node.owner)
    if refusal is not None:
        _refuse(refusal)
    try:
        keyring = keyring or signing_keyring()
    except SigningKeyUnavailable:
        _refuse("signing_unavailable")
    envelope = build_gated_invoke(
        signer=keyring,
        approval_id=approval.pk,
        node_id=node.pk,
        workspace_id=execution.workspace_id,
        capability=contract.name,
        capability_version=contract.version,
        args=args,
        trace_id=execution.trace_id,
        issued_at=now,
        ttl_seconds=grant_ttl_seconds(),
    )
    execution.grant_id = envelope["grant"]["grant_id"]
    execution.grant_issued_at = timezone.now()
    execution.invocation_id = envelope["invocation_id"]
    execution.nonce = envelope["nonce"]
    execution.expires_at = envelope["expires_at"]
    return envelope
