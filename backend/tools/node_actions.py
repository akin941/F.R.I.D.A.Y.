"""The `node_action` gated tool (Phase 10 brief D6, D9 ports).

One gated `ToolSpec` for every gate-class node action (`repo.command.run`,
`applescript.recipe.run`, `ax.press`, `ax.type_text`; Q1): the router may
propose it, a card click requests it (`POST /approvals`), the node's owner
approves it on `/approvals`, and only then does `nodes.tasks.
run_node_action` issue a single-use, signed, <= 120 s grant. The node's own
window click is a second, independent act on top (D5 check 23).

Request path (`approvals.services.create_request`):

1. `validate_request`: the shape of the caller's payload.
2. `precheck_request` (outside any transaction, only for a caller who
   passed the role and validation checks): **the actor check first** (the
   actor is active, the owner of the named `node_id` and an `owner` of the
   request's workspace; an unknown node gets the same `actor_not_owner`
   and one `nodes.refused` AuditLog row is written either way), then the
   contract and workspace rules, then the trust check (a model call in
   branch 4) through the registered port.
3. `prepare_payload(..., precheck=)` inside the transaction: the actor and
   the rules again, then the snapshot `{node_id, capability,
   capability_version, class, args, args_sha256, workspace_id,
   source_message_id, source_input_mode, trust, data_class}`. Every other
   caller-supplied key is dropped; `trust` can only come from the precheck
   object, never from the payload. A verdict `block` refuses.

Refusal order (each an `InvalidTransition`, so no row exists):
`actor_not_owner`; unknown or non-invocable capability; args outside the
server contract; a confirm-class capability (those use `POST
/nodes/<id>/actions`, D8); a `repo_path` that does not resolve to the
request's workspace; a personal-class capability outside the personal
workspace; a trust verdict `block`.

Decision path: `authorize_decision` refuses an approver who is not the
node's active owner holding the `owner` role (the request stays pending).
The approval lives 10 minutes (`approval_ttl_seconds=600`).

Ports (D9): `tools` may not import `assistant`, so the trust check and the
evidence source are registered by `assistant.apps.AssistantConfig.ready()`.
Unregistered -> `block` (`trust_check_unavailable_config`): a
misconfigured process fails closed, and that default never changes. In
branch 3 `assistant` registers `pending_trust_check` (`warn:
trust_check_pending`, no model call, `is_stub = True`); branch 4 replaces
that one line with the real check.
"""

import logging
import posixpath
from dataclasses import dataclass, field

from memory.ingestion.mapping import workspace_for_path
from nodes import action_rules
from nodes import services as node_services
from nodes.capabilities import get_action_contract, get_any_contract
from nodes.models import NodeActionExecution
from nodes.protocol.canonical import CanonicalizationError, sha256_hex
from nodes.protocol.messages import ProtocolError
from tools import node_action_ports as ports
from tools.node_action_ports import (
    INPUT_MODES,
    TRUST_REASON_CODES,
    TRUST_VERDICTS,
    UNCONFIGURED,
    TrustVerdict,
    pending_trust_check,
    register_evidence_source,
    register_trust_check,
    reset_ports,
)
from tools.registry import (
    ToolDecisionForbidden,
    ToolSpec,
    ToolValidationError,
    register_tool,
)
from tools.work_log import record_tool_outcome

logger = logging.getLogger(__name__)

TOOL_NAME = "node_action"
APPROVAL_TTL_SECONDS = 600  # ADR-013 decision 6: owner away = expire at 10 min
ACCEPTED_KEYS = frozenset({"node_id", "capability", "args", "source_message_id"})
CONFIRM_NOTE = "Mac'teki onay penceresi kendi metnini gösterir"
VOICE_LABEL = "Sesle önerildi"


@dataclass(frozen=True)
class NodeActionPrecheck:
    """What `precheck_request` hands `prepare_payload` (never a client
    value: an instance of this class, created only here)."""

    actor_id: object
    node_id: str
    trust: TrustVerdict
    source_input_mode: str = "unknown"
    extra: dict = field(default_factory=dict)


def _refuse(code):
    raise ToolValidationError(code)


def validate_request(workspace, payload):
    if not isinstance(payload, dict):
        _refuse("invalid_payload")
    if not isinstance(payload.get("node_id"), str) or len(payload["node_id"]) > 64:
        _refuse("invalid_payload")
    if not isinstance(payload.get("capability"), str) or len(payload["capability"]) > 64:
        _refuse("invalid_payload")
    if not isinstance(payload.get("args"), dict):
        _refuse("invalid_payload")
    source = payload.get("source_message_id", "")
    if source is None:
        source = ""
    if not isinstance(source, str) or len(source) > 64:
        _refuse("invalid_payload")


def _actor_node(workspace, actor, payload):
    node = node_services.find_node(payload.get("node_id"))
    if node is None or not action_rules.actor_is_node_owner(actor, node, workspace):
        return None
    return node


def _rules(workspace, node, payload):
    """The deterministic D6 refusals after the actor check; returns the
    contract."""
    capability = payload["capability"]
    args = payload["args"]
    contract = get_any_contract(capability)
    if contract is None or not contract.is_action:
        _refuse("unknown_capability")
    entry = node_services.invocable_entry(node_services.latest_snapshot(node), contract)
    if entry is None:
        _refuse("not_invocable")
    try:
        contract.validate_args(args)
    except ProtocolError:
        _refuse("args_contract")
    if contract.capability_class != "gate":
        _refuse("confirm_class")
    refusal = action_rules.workspace_refusal(contract, args, workspace, node.owner)
    if refusal is not None:
        _refuse(refusal)
    return contract


def _verdict(value) -> TrustVerdict:
    if (
        isinstance(value, TrustVerdict)
        and value.verdict in TRUST_VERDICTS
        and value.reason_code in TRUST_REASON_CODES
    ):
        return value
    return TrustVerdict("block", "trust_check_failed")


def _run_trust_check(workspace, actor, payload, contract) -> TrustVerdict:
    if ports.trust_check() is None:
        return UNCONFIGURED
    try:
        value = ports.trust_check()(
            workspace=workspace,
            actor=actor,
            source_message_id=payload.get("source_message_id") or "",
            capability=contract.name,
            args=payload["args"],
        )
    except Exception:
        logger.warning("node action trust check failed; blocking")
        return TrustVerdict("block", "trust_check_failed")
    return _verdict(value)


def _input_mode(workspace, actor, payload) -> str:
    if ports.evidence_source() is None:
        return "unknown"
    try:
        mode = ports.evidence_source()(
            workspace=workspace,
            actor=actor,
            source_message_id=payload.get("source_message_id") or "",
        )
    except Exception:
        return "unknown"
    return mode if mode in INPUT_MODES else "unknown"


def precheck_request(workspace, actor, payload) -> NodeActionPrecheck:
    """Outside any transaction (D6): the actor check, the rules, then the
    trust check. Raises ToolValidationError."""
    node = _actor_node(workspace, actor, payload)
    if node is None:
        node_services.record_actor_refusal(
            actor=actor,
            node_id=payload.get("node_id"),
            capability=payload.get("capability"),
            workspace=workspace,
        )
        _refuse("actor_not_owner")
    contract = _rules(workspace, node, payload)
    trust = _run_trust_check(workspace, actor, payload, contract)
    return NodeActionPrecheck(
        actor_id=actor.pk,
        node_id=str(node.pk),
        trust=trust,
        source_input_mode=_input_mode(workspace, actor, payload),
    )


def prepare_payload(workspace, payload, *, precheck=None) -> dict:
    """The stored snapshot (D6). Only a `NodeActionPrecheck` for the same
    node reaches here; a caller-supplied `trust` (or any other key) is
    dropped."""
    if not isinstance(precheck, NodeActionPrecheck):
        _refuse("precheck_missing")
    node = node_services.find_node(payload.get("node_id"))
    if node is None or str(node.pk) != precheck.node_id or node.owner_id != precheck.actor_id:
        _refuse("actor_not_owner")
    if not action_rules.actor_is_node_owner(node.owner, node, workspace):
        _refuse("actor_not_owner")
    contract = _rules(workspace, node, payload)
    if precheck.trust.verdict == "block":
        _refuse("trust_block")
    args = payload["args"]
    try:
        args_sha256 = sha256_hex(args)
    except (CanonicalizationError, TypeError, ValueError):
        _refuse("args_contract")
    return {
        "node_id": str(node.pk),
        "capability": contract.name,
        "capability_version": contract.version,
        "class": contract.capability_class,
        "args": args,
        "args_sha256": args_sha256,
        "workspace_id": str(workspace.pk),
        "source_message_id": str(payload.get("source_message_id") or ""),
        "source_input_mode": precheck.source_input_mode,
        "trust": precheck.trust.as_dict(),
        "data_class": action_rules.call_data_class(contract, args, workspace),
    }


def authorize_decision(approval_request, actor) -> None:
    """D6 approver check: only the node's active owner holding the `owner`
    role in the approval's workspace may approve a `node_action`."""
    payload = approval_request.payload if isinstance(approval_request.payload, dict) else {}
    node = node_services.find_node(payload.get("node_id"))
    if node is None or not action_rules.actor_is_node_owner(
        actor, node, approval_request.workspace
    ):
        raise ToolDecisionForbidden("Only the node's owner can approve this node action.")


def _repo_name(contract, args, workspace):
    path = action_rules.repo_path_of(contract, args)
    if not path:
        return ""
    # The repo resolved to this workspace when the request was made, so its
    # last segment is the mapped name the owner recognises; never more of
    # the raw path than that.
    return posixpath.basename(path.rstrip("/"))[:120]


def describe_request(approval_request):
    """The whitelisted Turkish summary the approvals UI shows (D6)."""
    payload = approval_request.payload if isinstance(approval_request.payload, dict) else {}
    contract = get_action_contract(payload.get("capability"))
    if contract is None:
        return None
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    trust = payload.get("trust") if isinstance(payload.get("trust"), dict) else {}
    node = node_services.find_node(payload.get("node_id"))
    summary = {
        "kind": TOOL_NAME,
        "title": contract.label,
        "capability": contract.name,
        "node_name": node.name if node is not None else "",
        "repo_name": _repo_name(contract, args, approval_request.workspace),
        "trust_verdict": trust.get("verdict", ""),
        "trust_reason": trust.get("reason_code", ""),
        "voice": payload.get("source_input_mode") == "voice",
        "note": CONFIRM_NOTE,
        "expires_in_seconds": APPROVAL_TTL_SECONDS,
    }
    if summary["voice"]:
        summary["voice_label"] = VOICE_LABEL
    if contract.name == "repo.command.run":
        summary["command_kind"] = args.get("command_kind", "")
    recipe = contract.recipe_for(args) if contract.recipes else None
    if recipe is not None:
        summary["recipe"] = recipe.label
    if "bundle_id" in args:
        summary["bundle_id"] = args["bundle_id"]
    if contract.name == "ax.press":
        summary["role"] = args.get("role", "")
        summary["element_title"] = args.get("title", "")
    if contract.name == "ax.type_text":
        summary["text"] = args.get("text", "")
        summary["submit"] = bool(args.get("submit"))
    return summary


def create_execution(approval_request):
    return node_services.create_gated_execution(approval_request)


def dispatch(execution_id):
    """Enqueue `run_node_action`; a broker error is swallowed (the ToolSpec
    contract) -- the beat sweep then fails the execution `stale_dispatch`."""
    from nodes.tasks import run_node_action

    try:
        run_node_action.delay(str(execution_id))
    except Exception:
        logger.warning("node action dispatch failed to publish execution=%s", execution_id)


def describe_execution(approval_request):
    execution = NodeActionExecution.objects.filter(approval=approval_request).first()
    if execution is None:
        return None
    return {
        "status": execution.status,
        "refusal_code": execution.refusal_code,
        "exit_code": execution.exit_code,
        "confirm_state": execution.confirm_state,
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
    }


def _workspace_for_path(owner, path):
    return workspace_for_path(owner, path)


def record_outcome(execution):
    """The work-log entry of a terminal gated execution (ADR-008 decision
    4): capability label, status and exit code only -- never args or
    output. Personal-class calls are `private`."""
    approval_request = execution.approval
    payload = approval_request.payload if isinstance(approval_request.payload, dict) else {}
    contract = get_action_contract(execution.capability)
    label = contract.label if contract is not None else execution.capability
    summary = f"{label}: {execution.status}"
    if execution.exit_code is not None:
        summary += f" (çıkış kodu {execution.exit_code})"
    record_tool_outcome(
        approval_request,
        outcome=execution.status,
        summary=summary,
        sensitivity="private" if payload.get("data_class") == "personal" else "normal",
    )


def _register():
    register_tool(
        ToolSpec(
            name=TOOL_NAME,
            gated=True,
            create_execution=create_execution,
            dispatch=dispatch,
            validate_request=validate_request,
            describe_execution=describe_execution,
            user_invocable=True,
            proposable=True,
            records_work_log=True,
            describe_request=describe_request,
            prepare_payload=prepare_payload,
            approval_ttl_seconds=APPROVAL_TTL_SECONDS,
            precheck_request=precheck_request,
            authorize_decision=authorize_decision,
        )
    )
    # The 8A path rules, for grant issuance's re-check in the worker (D2),
    # and the work-log entry of each terminal gated execution.
    action_rules.register_path_resolver(_workspace_for_path)
    node_services.register_terminal_hook(record_outcome)


__all__ = [
    "pending_trust_check",
    "register_evidence_source",
    "register_trust_check",
    "reset_ports",
]
