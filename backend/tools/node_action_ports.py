"""The node action trust-check and evidence ports (Phase 10 brief D9).

Kept apart from `tools.node_actions` on purpose: `assistant` registers into
these ports (`AssistantConfig.ready()`), and `assistant` may not reach
`nodes` (import contract "only tools, hud, ops may import nodes"), not even
transitively through `tools.node_actions`. This module imports nothing of
ours.

- `register_trust_check(fn)`: `fn(*, workspace, actor, source_message_id,
  capability, args) -> TrustVerdict`. It can warn or block; it never
  approves, creates or decides anything.
- `register_evidence_source(fn)`: `fn(*, workspace, actor,
  source_message_id) -> "text" | "voice" | None`.
- Unregistered trust check -> `block` (`trust_check_unavailable_config`):
  a misconfigured process fails closed. This default never changes.
- `pending_trust_check`: the branch-3 stub (`warn:trust_check_pending`, no
  model call, `is_stub = True`). Branch 4 replaced its registration in
  `assistant.apps` with the real `assistant.trust_check.check_node_action`;
  the stub stays here only so a test can register it, and the closeout
  `trust_check` preflight fails while it is the registered function.
- Branch 4 (D13) adds the two **proposal ports** the router uses, both
  registered by `tools.node_actions` (which may reach `nodes`; the router in
  `assistant` may not): `register_proposal_source(fn)` -- `fn(*, workspace,
  actor) -> list[dict]`, the actor's own nodes with their invocable action
  capabilities and the workspace's 8A path prefixes -- and
  `register_proposal_validator(fn)` -- `fn(*, workspace, actor, proposal,
  context) -> dict | None`, the deterministic server contract (actor check,
  contract, args, workspace rule and, Phase 13 D6, the "which PC" resolver
  over the turn's `tools.registry.ToolCallContext`) applied to the router's
  untrusted output; a `{"refusal", "reply"}` dict means "no draft, answer
  with this Friday-authored text". Both unregistered -> no target and no
  valid proposal (the router degrades to chat).
"""

from dataclasses import dataclass

TRUST_VERDICTS = ("ok", "warn", "block")
# The closed reason vocabulary of a trust verdict (D9; branch 4 adds the
# model step's own reasons through its closed list).
TRUST_REASON_CODES = frozenset(
    {
        "consistent",
        "no_owner_evidence",
        "voice_evidence",
        "repo_not_mentioned",
        "unclear",
        "inconsistent",
        "trust_check_unavailable",
        "trust_check_unavailable_config",
        "trust_check_pending",
        "trust_check_failed",
    }
)
# Branch 4 (D9): the model step's own closed reason list
# (`assistant.trust_check.MODEL_REASON_CODES`, pinned equal by a test).
MODEL_REASON_CODES = frozenset(
    {
        "matches_request",
        "different_action",
        "different_target",
        "not_requested",
        "ambiguous_request",
    }
)
TRUST_REASON_CODES = TRUST_REASON_CODES | MODEL_REASON_CODES
INPUT_MODES = ("text", "voice", "unknown")


@dataclass(frozen=True)
class TrustVerdict:
    verdict: str
    reason_code: str
    evidence_modes: tuple = ()

    def as_dict(self):
        return {
            "verdict": self.verdict,
            "reason_code": self.reason_code,
            "evidence_modes": list(self.evidence_modes),
        }


UNCONFIGURED = TrustVerdict("block", "trust_check_unavailable_config")


def pending_trust_check(**kwargs) -> TrustVerdict:
    """The branch-3 stub (D9): `warn:trust_check_pending`, no model call.
    Registered explicitly by `assistant.apps` until branch 4 replaces that
    line; the closeout preflight fails while it is registered."""
    return TrustVerdict("warn", "trust_check_pending")


pending_trust_check.is_stub = True

_trust_check = None
_evidence_source = None
_proposal_source = None
_proposal_validator = None


def register_trust_check(fn) -> None:
    """`fn(*, workspace, actor, source_message_id, capability, args) ->
    TrustVerdict`. It can warn or block; it never approves anything."""
    global _trust_check
    if not callable(fn):
        raise TypeError("the trust check must be callable")
    _trust_check = fn


def register_evidence_source(fn) -> None:
    """`fn(*, workspace, actor, source_message_id) -> "text" | "voice" |
    None`: the source message's input mode (branch 4)."""
    global _evidence_source
    if not callable(fn):
        raise TypeError("the evidence source must be callable")
    _evidence_source = fn


def trust_check():
    """The registered trust check, or None."""
    return _trust_check


def evidence_source():
    """The registered evidence source, or None."""
    return _evidence_source


def registered_trust_check():
    return _trust_check


def register_proposal_source(fn) -> None:
    """`fn(*, workspace, actor) -> list[dict]` (D13): what the router may
    propose to this actor in this workspace."""
    global _proposal_source
    if not callable(fn):
        raise TypeError("the proposal source must be callable")
    _proposal_source = fn


def register_proposal_validator(fn) -> None:
    """`fn(*, workspace, actor, proposal, context) -> dict | None` (D13;
    `context` since Phase 13 D6): the server contract applied to the
    router's untrusted argument output."""
    global _proposal_validator
    if not callable(fn):
        raise TypeError("the proposal validator must be callable")
    _proposal_validator = fn


def proposal_source():
    return _proposal_source


def proposal_validator():
    return _proposal_validator


def snapshot_ports() -> tuple:
    """Test-only: the current registrations, for `restore_ports`."""
    return (_trust_check, _evidence_source, _proposal_source, _proposal_validator)


def restore_ports(state) -> None:
    """Test-only: put back what `snapshot_ports` returned."""
    global _trust_check, _evidence_source, _proposal_source, _proposal_validator
    _trust_check, _evidence_source, _proposal_source, _proposal_validator = state


def reset_ports() -> None:
    """Test-only: back to the unregistered (fail-closed) state of the trust
    check and the evidence source (the proposal ports are `tools`'s own
    registrations and are left alone)."""
    global _trust_check, _evidence_source
    _trust_check = None
    _evidence_source = None
