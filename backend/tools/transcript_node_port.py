"""The `nodes`-backed transcript port (runbook A12; Phase 8B branch 8 owner
steps 3-5, closeout owner step 2).

`NodesTranscriptPort` is the `tools.transcript_ingest.NodeReadPort` over
the real node layer: every read is `nodes.services.invoke_read` (signed,
pushed over the bus to the gateway process that holds the node's socket,
chain-audited), with the caller's `channel` and `actor` passed through.
`ToolsConfig.ready` installs it as the pull's default port (`install`).

- **Which node.** `owner_node(owner)` picks among the owner's own nodes
  (`nodes.selectors.nodes_for`, `for_actor()`), usable ones only (not
  revoked, killed or expired), ranked by, in this order: online (a live
  presence key) first; then a node whose latest `CapabilitySnapshot`
  offers an owner-turn read; then the most recently active; then the
  newest. Online comes first so an offline node that offers the reads
  never hides an online one (the pull reaches the online node and its
  `capability_granted` decides). When no usable node is online the answer
  is `None` (the pull skips it as `no_node`, nothing is sent). One node per
  owner: the pull's seam has one node per owner (the cursor is keyed by
  the node's id, `NodeHandle.ref`).
- **Granted.** `capability_granted(node, capability)` is true only when
  the node's latest snapshot marks the capability invocable for the
  server's own contract (name, version, class and both schema hashes match,
  and the owner granted it on the node: `nodes.services.invocable_entry`).
- **The read.** `invoke_read(...)` re-finds the node row by its id, then
  calls `nodes.services.invoke_read`, which re-runs the D13 amendment
  checks before anything is signed: the personal-class actor check
  (`server_refused:actor_not_owner`) and the system channel limited to
  `SYSTEM_READ_CAPABILITIES` (`server_refused:not_invocable`). Its outcome
  maps onto the pull's statuses: `ok` -> `ok` (the validated output),
  `refused` (a node code) -> `node_refused`, `server_refused` ->
  `server_refused`, everything else (`error`, `timeout`, `node_offline`,
  `busy`, `bus_unavailable`, `node_disconnected`) -> `failed` with the
  nodes status (and code) as the code. Output is returned, never stored.
"""

from nodes import presence, services
from nodes.capabilities import get_contract
from nodes.selectors import latest_snapshot, nodes_for
from nodes.targeting import is_usable
from tools import transcript_ingest
from tools.transcript_ingest import (
    FAILED,
    NODE_REFUSED,
    SENT,
    SERVER_REFUSED,
    NodeHandle,
    ReadOutcome,
)

NODE_UNKNOWN = "node_unknown"


def _offers_owner_turns(node) -> bool:
    snapshot = latest_snapshot(node)
    for name in services.SYSTEM_READ_CAPABILITIES:
        contract = get_contract(name)
        if contract is not None and services.invocable_entry(snapshot, contract) is not None:
            return True
    return False


class NodesTranscriptPort:
    """The pull's node port over `nodes.services` (see the module doc)."""

    def owner_node(self, owner):
        if owner is None or getattr(owner, "pk", None) is None:
            return None
        candidates = [node for node in nodes_for(owner) if is_usable(node)]
        if not candidates:
            return None

        def rank(node):
            last_active = node.last_active_at.timestamp() if node.last_active_at else 0.0
            # The order is documented in the module docstring ("Which node").
            return (
                presence.is_online(node),
                _offers_owner_turns(node),
                last_active,
                node.created_at.timestamp(),
            )

        best = max(candidates, key=rank)
        if not presence.is_online(best):
            return None
        return NodeHandle(ref=str(best.pk), owner=best.owner)

    def capability_granted(self, node, capability):
        contract = get_contract(capability) if isinstance(capability, str) else None
        row = services.find_node(getattr(node, "ref", None))
        if contract is None or row is None or row.owner_id != getattr(node.owner, "pk", None):
            return False
        return services.invocable_entry(latest_snapshot(row), contract) is not None

    def invoke_read(self, *, node, capability, args, workspace, actor, channel, trace_id):
        row = services.find_node(getattr(node, "ref", None))
        if row is None:
            return ReadOutcome(FAILED, code=NODE_UNKNOWN)
        outcome = services.invoke_read(
            node=row,
            capability=capability,
            args=args,
            workspace=workspace,
            actor=actor,
            trace_id=trace_id,
            channel=channel,
        )
        invocation_id = outcome.invocation_id or None
        if outcome.status == "ok":
            return ReadOutcome(SENT, output=outcome.output, invocation_id=invocation_id)
        if outcome.status == "refused":
            return ReadOutcome(NODE_REFUSED, code=outcome.refusal_code, invocation_id=invocation_id)
        if outcome.status == "server_refused":
            return ReadOutcome(SERVER_REFUSED, code=outcome.refusal_code)
        code = (
            outcome.status
            if not outcome.refusal_code
            else f"{outcome.status}:{outcome.refusal_code}"
        )
        return ReadOutcome(FAILED, code=code[:64], invocation_id=invocation_id)


def install() -> None:
    """Install the port as the pull's default, replacing only the
    `UnavailableNodePort` placeholder (a port a test or caller installed
    stays)."""
    if isinstance(transcript_ingest.get_node_port(), transcript_ingest.UnavailableNodePort):
        transcript_ingest.set_node_port(NodesTranscriptPort())
