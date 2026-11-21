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
  model call, `is_stub = True`), registered explicitly by `assistant.apps`
  until branch 4 replaces that line.
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


def reset_ports() -> None:
    """Test-only: back to the unregistered (fail-closed) state."""
    global _trust_check, _evidence_source
    _trust_check = None
    _evidence_source = None
