"""Parity tests (MVP 8 §1, brief): `skills.catalog`'s per-kind step-shape
validator is a deliberate duplicate of `agents.planner._parse_step`'s
validation logic, not an import of it (`skills` must not import `agents`
in production code -- see the `skills must not import agents` import-linter
contract). This file exists so the two independently-maintained
implementations can't silently drift apart: every case below is fed to both
validators and both must reach the same accept/reject verdict.

`skills.tests.* -> agents.*` is an explicit import-linter exemption
(pyproject.toml) -- tests only, never production code.
"""

import pytest

from agents.planner import PlanParseError, _parse_step
from skills.catalog import SkillStep, SkillValidationError, _validate_step_shape

_LONG_DESCRIPTION = "x" * 2001
_LONG_TITLE = "x" * 256

CASES = [
    pytest.param(
        {"kind": "read", "description": "Read something", "payload": {}},
        True,
        id="valid-read",
    ),
    pytest.param(
        {
            "kind": "tool_request",
            "description": "Export",
            "payload": {"tool_name": "vault_export"},
        },
        True,
        id="valid-tool_request",
    ),
    pytest.param(
        {
            "kind": "task_draft",
            "description": "Draft",
            "payload": {"title": "Follow up", "due_date": "2026-01-01"},
        },
        True,
        id="valid-task_draft-with-date",
    ),
    pytest.param(
        {
            "kind": "task_draft",
            "description": "Draft",
            "payload": {"title": "Follow up", "due_date": None},
        },
        True,
        id="valid-task_draft-null-date",
    ),
    pytest.param(
        {"kind": "bogus", "description": "Nope", "payload": {}},
        False,
        id="invalid-unrecognized-kind",
    ),
    pytest.param(
        {"kind": "read", "description": "Read", "payload": {"unexpected": 1}},
        False,
        id="invalid-read-nonempty-payload",
    ),
    pytest.param(
        {
            "kind": "tool_request",
            "description": "Export",
            "payload": {"tool_name": "vault_export", "extra": 1},
        },
        False,
        id="invalid-tool_request-extra-key",
    ),
    pytest.param(
        {"kind": "tool_request", "description": "Export", "payload": {"tool_name": ""}},
        False,
        id="invalid-tool_request-empty-name",
    ),
    pytest.param(
        {"kind": "task_draft", "description": "Draft", "payload": {"title": "Follow up"}},
        False,
        id="invalid-task_draft-missing-due_date-key",
    ),
    pytest.param(
        {
            "kind": "task_draft",
            "description": "Draft",
            "payload": {"title": "Follow up", "due_date": "01-01-2026"},
        },
        False,
        id="invalid-task_draft-bad-date-format",
    ),
    pytest.param(
        {
            "kind": "task_draft",
            "description": "Draft",
            "payload": {"title": "Follow up", "due_date": "2026-02-30"},
        },
        False,
        id="invalid-task_draft-nonexistent-calendar-date",
    ),
    pytest.param(
        {"kind": "read", "description": "", "payload": {}},
        False,
        id="invalid-empty-description",
    ),
    pytest.param(
        {"kind": "read", "description": _LONG_DESCRIPTION, "payload": {}},
        False,
        id="invalid-description-too-long",
    ),
    pytest.param(
        {
            "kind": "task_draft",
            "description": "Draft",
            "payload": {"title": _LONG_TITLE, "due_date": None},
        },
        False,
        id="invalid-task_draft-title-too-long",
    ),
]


@pytest.mark.parametrize("raw_step, expected_valid", CASES)
def test_skills_and_agents_validators_agree(raw_step, expected_valid):
    skill_step = SkillStep(
        kind=raw_step["kind"], description=raw_step["description"], payload=raw_step["payload"]
    )
    try:
        _validate_step_shape(skill_step)
        skills_valid = True
    except SkillValidationError:
        skills_valid = False

    try:
        _parse_step(raw_step)
        agents_valid = True
    except PlanParseError:
        agents_valid = False

    assert skills_valid == expected_valid
    assert agents_valid == expected_valid
    assert skills_valid == agents_valid
