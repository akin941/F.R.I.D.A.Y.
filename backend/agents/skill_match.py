"""Structured output contract for the `agent.skill_match` gateway call
(MVP 8 §3, brief).

The skill-matching prompt instructs the model to return exactly one JSON
object of this shape:

    {"skill_name": "<name>" | null}

No other shape. Unlike `agents.planner.parse_plan`, this parser does NOT
check `skill_name` against the registered skill catalog -- that resolution
(and the "unknown/hallucinated name degrades to null" behavior) happens in
`agents.planner_service.create_agent_from_goal`, the same separation
`agents.planner`'s own module docstring already draws for `tool_name`
(shape validated here, existence resolved by the caller at planning time).
"""

import json

_TOP_LEVEL_KEYS = {"skill_name"}


class SkillMatchParseError(Exception):
    """The skill-match response did not conform to the schema. Callers must
    treat this exactly like a null match (fall through to `agent.plan`) --
    never raise it to the user, never fail agent creation because of it."""


def parse_skill_match(raw_text):
    """Returns the matched skill's name, or `None` for an explicit
    "no match" response. Raises `SkillMatchParseError` on anything else --
    malformed JSON, wrong shape, unexpected keys, or a non-string/non-null
    `skill_name`."""
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SkillMatchParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SkillMatchParseError("top-level payload must be a JSON object")
    if set(payload.keys()) != _TOP_LEVEL_KEYS:
        raise SkillMatchParseError(f"unexpected top-level keys: {sorted(payload.keys())}")

    skill_name = payload["skill_name"]
    if skill_name is not None and not isinstance(skill_name, str):
        raise SkillMatchParseError("skill_name must be a string or null")
    return skill_name
