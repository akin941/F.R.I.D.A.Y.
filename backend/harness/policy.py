"""Harness agent job-distribution policy (Phase 5 D11).

`settings.HARNESS_AGENT_POLICY` replaces the Phase 3 `_SELECTABLE_*_AGENTS`
sets: which agent runs a discovery or implementation job when the owner
names none, which agents the owner may name, and which agent reviews each
implementer's work. Every job row records why its agent was chosen
(`agent_routing_reason`), and the external `ModelUsage` rows carry the same
reason, so in-process and CLI usage both explain themselves.

ADR-008 decision 3 (coder and reviewer are never the same) is enforced
twice: `policy_errors()` backs a Django system check that refuses to boot
on a self-pairing, and `reviewer_for()` independently refuses to return
the implementer itself at run time.
"""

from django.conf import settings

ROUTING_POLICY_DEFAULT = "policy:default"
ROUTING_OWNER_SELECTED = "owner_selected"
ROUTING_CROSS_VENDOR = "policy:cross_vendor"

DISCOVERY = "discovery"
IMPLEMENTATION = "implementation"
JOB_KINDS = (DISCOVERY, IMPLEMENTATION)


class AgentNotSelectable(ValueError):
    """The requested agent is not owner-selectable for this job kind."""


def _policy(kind):
    return settings.HARNESS_AGENT_POLICY[kind]


def selectable_agents(kind):
    return frozenset(_policy(kind).get("selectable", ()))


def resolve_agent(kind, requested=None):
    """`(agent, agent_routing_reason)` for a new job of `kind`. `requested`
    None/empty -> the policy default; otherwise it must be selectable (the
    owner named it), else `AgentNotSelectable`."""
    policy = _policy(kind)
    if not requested:
        return policy["default"], ROUTING_POLICY_DEFAULT
    if requested not in selectable_agents(kind):
        raise AgentNotSelectable(str(requested))
    return str(requested), ROUTING_OWNER_SELECTED


def reviewer_for(implementation_agent):
    """The cross-vendor reviewer paired with `implementation_agent`, or
    None when it has none -- including a (mis)configured self-pairing,
    which is never returned whatever the settings say."""
    reviewer = _policy(IMPLEMENTATION).get("reviewers", {}).get(str(implementation_agent))
    if not reviewer or reviewer == str(implementation_agent):
        return None
    return reviewer


def policy_errors(policy, *, known_agents):
    """Human-readable problems with `policy` (empty = valid). Used by the
    `harness.E001` system check; pure, so tests can feed it any shape."""
    errors = []
    if not isinstance(policy, dict):
        return ["HARNESS_AGENT_POLICY must be a dict keyed by job kind."]
    for kind in JOB_KINDS:
        entry = policy.get(kind)
        if not isinstance(entry, dict):
            errors.append(f"HARNESS_AGENT_POLICY[{kind!r}] is missing.")
            continue
        selectable = list(entry.get("selectable") or [])
        default = entry.get("default")
        for agent in [default, *selectable]:
            if agent not in known_agents:
                errors.append(f"HARNESS_AGENT_POLICY[{kind!r}] names unknown agent {agent!r}.")
        if default not in selectable:
            errors.append(
                f"HARNESS_AGENT_POLICY[{kind!r}] default {default!r} is not in its selectable list."
            )
    implementation = policy.get(IMPLEMENTATION)
    if isinstance(implementation, dict):
        reviewers = implementation.get("reviewers") or {}
        if not isinstance(reviewers, dict):
            return [*errors, "HARNESS_AGENT_POLICY['implementation']['reviewers'] must be a dict."]
        for implementer, reviewer in reviewers.items():
            if reviewer not in known_agents:
                errors.append(f"Reviewer {reviewer!r} for {implementer!r} is not a known agent.")
            if reviewer == implementer:
                errors.append(
                    f"Self-pairing: {implementer!r} would review its own work "
                    "(ADR-008 decision 3: coder and reviewer are never the same)."
                )
        for implementer in implementation.get("selectable") or []:
            if implementer not in reviewers:
                errors.append(
                    f"Selectable implementation agent {implementer!r} has no cross-vendor "
                    "reviewer; its jobs could never be reviewed."
                )
    return errors
