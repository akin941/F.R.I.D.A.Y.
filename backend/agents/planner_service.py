"""Planner orchestration: the gateway call(s) this MVP's planning step
makes (MVP 7 §2 / MVP 8 §3, brief), plus the tool-name resolution that
fails a plan before any `AgentStep` row is written.

`agents` may import `tools` (the one-way direction this MVP establishes),
so `tools.registry.list_tool_names()` is imported directly here, unlike
`assistant.services`'s local import of the same function (which exists
there only to avoid a *different* app's import-time constraints -- no such
constraint applies here). `agents` may also import `skills` (MVP 8 §1's new
one-way root, mirroring the same shape) -- the reverse is forbidden by an
import-linter contract, keeping `skills.catalog` a plain code-only registry
with no execution-engine knowledge at all.
"""

from django.core.exceptions import ImproperlyConfigured

from agents.models import Agent
from agents.planner import PlanParseError, parse_plan
from agents.services import fail_agent, finalize_plan, start_agent
from agents.skill_match import SkillMatchParseError, parse_skill_match
from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from skills.catalog import (
    get_skill,
    list_skill_names,
    list_skill_prompt_entries,
    skill_steps_to_plan_dicts,
)
from tools.registry import list_tool_names

_AGENT_PLAN_PROMPT_NAME = "jarvis_agent_plan"
_AGENT_SKILL_MATCH_PROMPT_NAME = "jarvis_agent_skill_match"

# Planner-local tool vocabulary (Codex round-1 LOW fix -- the prompt told
# the model tool names were "listed below" without ever actually listing
# them, which would make tool_request planning impossible against a real
# LLM). Mirrors `assistant.services._TOOL_DESCRIPTIONS`/
# `_tool_vocabulary_line`'s exact shape; duplicated rather than imported
# since `agents` and `assistant` have no import relationship in either
# direction (pyproject.toml import-linter).
_TOOL_DESCRIPTIONS = {
    "vault_export": "export approved memories/notes to the Obsidian vault",
}


def _tool_vocabulary_line():
    names = list_tool_names()
    if not names:
        return ""
    described = [
        f"{name} ({_TOOL_DESCRIPTIONS.get(name, 'no description available')})" for name in names
    ]
    return (
        "\n\nRegistered tools you may plan a tool_request step against: "
        + ", ".join(described)
        + ". Set tool_name to the exact matched name."
    )


def _agent_plan_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_AGENT_PLAN_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_AGENT_PLAN_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_AGENT_PLAN_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def _agent_skill_match_prompt_version():
    """Unlike `_agent_plan_prompt_version` above, this NEVER raises --
    `agent.skill_match` is an optional, always-skippable optimization
    layered in front of `agent.plan` (MVP 8 §3), never a second hard
    prerequisite for agent creation. A missing/inactive prompt config here
    degrades to `None` (no skill-match attempted at all), exactly like a
    `null` model response would, rather than turning a missing optional
    config into a new way for agent creation to fail."""
    try:
        template = PromptTemplate.objects.get(name=_AGENT_SKILL_MATCH_PROMPT_NAME)
    except PromptTemplate.DoesNotExist:
        return None
    if template.active_version_id is None:
        return None
    return template.active_version


def _skill_match_vocabulary_line():
    entries = list_skill_prompt_entries()
    if not entries:
        return ""
    described = [
        f'{name} ("{trigger_hint}") -- {description}' for name, description, trigger_hint in entries
    ]
    return "\n\nRegistered skills you may match: " + "; ".join(described) + "."


def _attempt_skill_match(*, actor, workspace, goal):
    """Attempts the `purpose="agent.skill_match"` gateway call (MVP 8 §3):
    an additive, always-skippable optimization -- every failure mode here
    (missing config, `GatewayError`, parse failure, a `null` response, or a
    hallucinated/unregistered name) returns `None`, degrading cleanly to
    the unmodified `agent.plan` flow below. Never raises. Does NOT touch
    `Agent.gateway_calls_used` -- that counter is a dedicated execution-time
    brake (MVP 7 branch 3), out of scope for a planning-phase call, exactly
    like `agent.plan` itself is already out of its scope.

    Returns the matched skill's name, or `None` if no skill should be used.
    """
    prompt_version = _agent_skill_match_prompt_version()
    if prompt_version is None:
        return None

    messages = [
        {"role": "system", "content": prompt_version.content + _skill_match_vocabulary_line()},
        {"role": "user", "content": goal},
    ]
    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="agent.skill_match",
            messages=messages,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError:
        return None

    try:
        skill_name = parse_skill_match(result.text)
    except SkillMatchParseError:
        return None

    if skill_name is None:
        return None
    if skill_name not in list_skill_names():
        return None
    return skill_name


def create_agent_from_goal(*, workspace, actor, goal):
    """Creates an `Agent` in `planning` status, runs the `purpose="agent.
    plan"` gateway call, and either finalizes a valid plan (`agents.
    services.finalize_plan`) or fails the whole thing with `failure_reason
    = planner_error` (Codex plan-review round-1 CRITICAL precedent from
    MVP 6: a router classification failure degrades safely rather than
    corrupting state -- the same discipline applies here to a planner
    failure, except there is no safe "chat" fallback for an agent, so it
    fails loudly instead of silently).

    Three distinct failure modes all collapse to the same `planner_error`
    reason (brief §2: "an unresolvable tool name fails the plan for the
    same reason a malformed shape does, not a per-step skip") -- a
    `GatewayError` from the gateway call itself, a `PlanParseError` from
    the model's output not matching the schema, and a `tool_request` step
    naming a tool that isn't (or is no longer) registered. Never raises
    any of these to the caller.

    The prompt-config lookup runs *before* `start_agent` (Codex round-2 LOW
    fix -- reversed, this order would durably create a stuck `planning`-
    status `Agent` row every time a misconfigured deployment's missing/
    inactive prompt template raised `ImproperlyConfigured`, mirroring
    `assistant.services.classify_intent`'s identical "validate config
    first, deploy-time misconfiguration is not a per-request failure"
    ordering). The `agent.skill_match` prompt version is deliberately
    NOT resolved here -- see `_agent_skill_match_prompt_version`'s own
    docstring.

    Before the `agent.plan` call, attempts `purpose="agent.skill_match"`
    (MVP 8 §3): if a registered skill unambiguously matches this goal, its
    fixed step template is used in place of `agent.plan`, which is skipped
    entirely -- the actual efficiency gain (one fewer LLM call), not just a
    faster plan. Any other outcome (no config, `GatewayError`, parse
    failure, `null`, or a hallucinated/unregistered name) falls through to
    the unmodified `agent.plan` flow below, with no skill provenance
    recorded on the `Agent` row.
    """
    prompt_version = _agent_plan_prompt_version()
    agent = start_agent(workspace=workspace, actor=actor, goal=goal)

    matched_skill_name = _attempt_skill_match(actor=actor, workspace=workspace, goal=goal)
    if matched_skill_name is not None:
        spec = get_skill(matched_skill_name)
        if spec is not None:
            return finalize_plan(
                agent=agent,
                steps=skill_steps_to_plan_dicts(spec),
                skill_name=spec.name,
                skill_content_hash=spec.content_hash,
            )

    messages = [
        {"role": "system", "content": prompt_version.content + _tool_vocabulary_line()},
        {"role": "user", "content": goal},
    ]
    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="agent.plan",
            messages=messages,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError:
        return fail_agent(agent=agent, failure_reason=Agent.FailureReason.PLANNER_ERROR)

    try:
        steps = parse_plan(result.text)
    except PlanParseError:
        return fail_agent(agent=agent, failure_reason=Agent.FailureReason.PLANNER_ERROR)

    registered_tool_names = list_tool_names()
    for step in steps:
        if step["kind"] != "tool_request":
            continue
        if step["payload"]["tool_name"] not in registered_tool_names:
            return fail_agent(agent=agent, failure_reason=Agent.FailureReason.PLANNER_ERROR)

    return finalize_plan(agent=agent, steps=steps)
