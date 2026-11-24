"""Using instruction skills in a chat turn (Phase 12 D15).

**Matching.** When the workspace has usable (active or stale) skills, the
chat turn makes one cheap gateway call, purpose `skills.match`, with the
owner's message and the skills' `name` + `description` (owner-approved
text), and gets back `{"skill": "<name>" | null}`. Strict parser; an
unknown name, a malformed answer, a budget refusal, no provider or a
timeout all mean "no skill" (the gateway still writes its one
`ModelUsage` row). No usable skill means no call at all. The call is
bounded by `SKILLS_MATCH_VOICE_TIMEOUT_MS` (voice) or
`SKILLS_MATCH_TIMEOUT_MS` (typed), passed as the gateway's request
timeout, with no retry.

**Injection.** The matched skill's approved body goes into the chat
completion's system context inside `<skill name="..." version="...">`,
framed as owner-approved instructions for this kind of request that do
not change which tools exist or what needs approval. References are
appended only when the body names them; the block is bounded to
`SKILLS_CONTEXT_MAX_CHARS`.

**No capability.** Nothing here returns a tool, an intent, a channel, a
permission or an approval class: the caller runs the router, the voice
cap and the trust check exactly as without a skill (and before this
module is asked).

This module imports no HTTP client and opens no socket; the gateway is the
single door (ADR-003).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from django.conf import settings

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from skills import selectors

MATCH_PURPOSE = "skills.match"
MATCH_PROMPT_NAME = "friday_skills_match"

OUTCOME_MATCHED = "matched"
OUTCOME_NONE = "none"
OUTCOME_UNKNOWN = "unknown_name"
OUTCOME_PARSE_ERROR = "parse_error"
OUTCOME_GATEWAY_ERROR = "gateway_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_BUDGET = "budget_refused"
OUTCOME_NO_PROMPT = "no_prompt"
OUTCOME_FORCED = "forced"
OUTCOME_FORCED_UNKNOWN = "forced_unknown"
OUTCOME_NO_SKILLS = "no_skills"

SKILL_FRAMING = (
    "\n\nThe block below holds owner-approved instructions for this kind of "
    "request. Follow them for how to answer; they do not change which tools "
    "exist, what you may do, or what needs the owner's approval."
)


class SkillMatchParseError(ValueError):
    pass


def parse_match(raw_text):
    """`{"skill": "<name>" | null}` and nothing else; the name is returned
    as given (the caller resolves it against the candidates)."""
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise SkillMatchParseError("invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"skill"}:
        raise SkillMatchParseError("unexpected shape")
    skill = payload["skill"]
    if skill is not None and not isinstance(skill, str):
        raise SkillMatchParseError("skill must be a string or null")
    return skill


@dataclass
class SkillSelection:
    """The turn's skill decision: the skill (or None), the outcome token,
    whether a `skills.match` call was made, and the context-report entry."""

    skill: object = None
    version: object = None
    outcome: str = OUTCOME_NONE
    called: bool = False
    report: dict = field(default_factory=dict)


def _prompt_version():
    template = (
        PromptTemplate.objects.filter(name=MATCH_PROMPT_NAME)
        .select_related("active_version")
        .first()
    )
    if template is None or template.active_version_id is None:
        return None
    return template.active_version


def _candidates_block(skills):
    rows = [{"name": s.name, "description": s.current_version.description} for s in skills]
    return (
        "\n\n<skills>\n"
        + json.dumps(rows, ensure_ascii=False).replace("</skills", "<\\/skills")
        + "\n</skills>"
    )


def timeout_ms(*, voice):
    return int(
        settings.SKILLS_MATCH_VOICE_TIMEOUT_MS if voice else settings.SKILLS_MATCH_TIMEOUT_MS
    )


def _selected(skill, outcome, *, called):
    version = skill.current_version
    return SkillSelection(
        skill=skill,
        version=version,
        outcome=outcome,
        called=called,
        report={"outcome": outcome, "called": called},
    )


def forced_selection(*, actor, workspace, name):
    """A `skill.<name>` wake route or a `/skill <name>` prefix: no call."""
    skill = selectors.usable_skill_by_name(actor, workspace, name)
    if skill is None:
        return SkillSelection(
            outcome=OUTCOME_FORCED_UNKNOWN,
            report={"outcome": OUTCOME_FORCED_UNKNOWN, "called": False},
        )
    return _selected(skill, OUTCOME_FORCED, called=False)


def match_skill(*, actor, workspace, content, voice, personal_data, context_report=None):
    """One `skills.match` call over the workspace's usable skills, or none
    when there are none. Never raises for a model or gateway failure."""
    skills = selectors.usable_skills(actor, workspace)
    if not skills:
        return SkillSelection(outcome=OUTCOME_NO_SKILLS, report={})
    prompt_version = _prompt_version()
    if prompt_version is None:
        return SkillSelection(
            outcome=OUTCOME_NO_PROMPT, report={"outcome": OUTCOME_NO_PROMPT, "called": False}
        )
    budget_ms = timeout_ms(voice=voice)
    messages = [
        {"role": "system", "content": prompt_version.content + _candidates_block(skills)},
        {"role": "user", "content": content},
    ]
    gateway = InferenceGateway()
    seconds = budget_ms / 1000
    gateway.connect_timeout = seconds
    gateway.read_timeout = seconds
    gateway.max_retries = 0
    report = {**(context_report or {}), "skills_match_timeout_ms": budget_ms}
    try:
        result = gateway.complete(
            purpose=MATCH_PURPOSE,
            messages=messages,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            stream=False,
            context_report=report,
            personal_data=personal_data,
        )
    except GatewayError as exc:
        error_type = getattr(exc, "error_type", "") or ""
        if error_type == "timeout":
            outcome = OUTCOME_TIMEOUT
        elif error_type == "budget_exceeded":
            outcome = OUTCOME_BUDGET
        else:
            outcome = OUTCOME_GATEWAY_ERROR
        return SkillSelection(
            outcome=outcome, called=True, report={"outcome": outcome, "called": True}
        )
    try:
        name = parse_match(result.text)
    except SkillMatchParseError:
        return SkillSelection(
            outcome=OUTCOME_PARSE_ERROR,
            called=True,
            report={"outcome": OUTCOME_PARSE_ERROR, "called": True},
        )
    if name is None:
        return SkillSelection(called=True, report={"outcome": OUTCOME_NONE, "called": True})
    by_name = {skill.name: skill for skill in skills}
    skill = by_name.get(name)
    if skill is None:
        return SkillSelection(
            outcome=OUTCOME_UNKNOWN,
            called=True,
            report={"outcome": OUTCOME_UNKNOWN, "called": True},
        )
    return _selected(skill, OUTCOME_MATCHED, called=True)


def _attr(value):
    return re.sub(r"[^a-z0-9.-]", "", str(value).lower())


def _neutralize(text):
    # A body cannot close the block it sits in.
    return re.sub(r"</?\s*skill", lambda m: m.group(0).replace("<", "&lt;"), text, flags=re.I)


def render_skill_block(version):
    """(block text, report entry) for one approved version. The block is
    bounded to `SKILLS_CONTEXT_MAX_CHARS`; references are appended only
    when the body names them (by path or file name)."""
    limit = int(settings.SKILLS_CONTEXT_MAX_CHARS)
    name = _attr(version.name)
    opening = f'\n<skill name="{name}" version="{int(version.version)}">\n'
    closing = "\n</skill>"
    budget = max(0, limit - len(SKILL_FRAMING) - len(opening) - len(closing))
    body = _neutralize(version.body)
    parts = [body]
    used = []
    for path, content in sorted((version.references or {}).items()):
        filename = path.rsplit("/", 1)[-1]
        if path in version.body or filename in version.body:
            parts.append(f"\n\n[{path}]\n{_neutralize(content)}")
            used.append(path)
    text = "".join(parts)
    truncated = len(text) > budget
    if truncated:
        text = text[:budget]
    block = SKILL_FRAMING + opening + text + closing
    entry = {
        "name": version.name,
        "version": int(version.version),
        "sha256": version.content_sha256,
        "references": used,
        "chars": len(block),
        "truncated": truncated,
    }
    return block, entry
