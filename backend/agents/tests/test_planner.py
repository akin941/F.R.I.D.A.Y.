import pytest

from agents.planner import PlanParseError, parse_plan


def test_valid_plan_with_all_three_kinds():
    raw = (
        '{"steps": ['
        '{"kind": "read", "description": "Look something up", "payload": {}},'
        '{"kind": "tool_request", "description": "Export vault", '
        '"payload": {"tool_name": "vault_export"}},'
        '{"kind": "task_draft", "description": "Draft a task", '
        '"payload": {"title": "Buy milk", "due_date": "2026-07-15"}}'
        "]}"
    )
    steps = parse_plan(raw)
    assert steps == [
        {"kind": "read", "description": "Look something up", "payload": {}},
        {
            "kind": "tool_request",
            "description": "Export vault",
            "payload": {"tool_name": "vault_export"},
        },
        {
            "kind": "task_draft",
            "description": "Draft a task",
            "payload": {"title": "Buy milk", "due_date": "2026-07-15"},
        },
    ]


def test_task_draft_null_due_date_is_allowed():
    raw = (
        '{"steps": [{"kind": "task_draft", "description": "Draft a task", '
        '"payload": {"title": "Buy milk", "due_date": null}}]}'
    )
    steps = parse_plan(raw)
    assert steps[0]["payload"]["due_date"] is None


def test_empty_steps_list_parses_to_empty_list():
    assert parse_plan('{"steps": []}') == []


def test_invalid_json_raises():
    with pytest.raises(PlanParseError):
        parse_plan("not json")


def test_non_object_top_level_raises():
    with pytest.raises(PlanParseError):
        parse_plan("[]")


def test_unexpected_top_level_keys_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": [], "extra": 1}')


def test_steps_not_a_list_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": {}}')


def test_step_not_an_object_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": ["not an object"]}')


def test_step_unexpected_keys_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": [{"kind": "read", "description": "x", "payload": {}, "extra": 1}]}')


def test_unrecognized_kind_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": [{"kind": "delete_everything", "description": "x", "payload": {}}]}')


def test_empty_description_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": [{"kind": "read", "description": "  ", "payload": {}}]}')


def test_description_too_long_raises():
    huge = "x" * 2001
    with pytest.raises(PlanParseError):
        parse_plan(f'{{"steps": [{{"kind": "read", "description": "{huge}", "payload": {{}}}}]}}')


def test_read_step_with_nonempty_payload_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": [{"kind": "read", "description": "x", "payload": {"foo": 1}}]}')


def test_tool_request_missing_tool_name_raises():
    with pytest.raises(PlanParseError):
        parse_plan('{"steps": [{"kind": "tool_request", "description": "x", "payload": {}}]}')


def test_tool_request_empty_tool_name_raises():
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "tool_request", "description": "x", '
            '"payload": {"tool_name": "  "}}]}'
        )


def test_tool_request_unexpected_payload_keys_raises():
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "tool_request", "description": "x", '
            '"payload": {"tool_name": "vault_export", "extra": 1}}]}'
        )


def test_task_draft_missing_title_raises():
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "task_draft", "description": "x", "payload": {"due_date": null}}]}'
        )


def test_task_draft_empty_title_raises():
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "task_draft", "description": "x", '
            '"payload": {"title": "  ", "due_date": null}}]}'
        )


def test_task_draft_title_too_long_raises():
    huge_title = "x" * 256
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "task_draft", "description": "x", '
            f'"payload": {{"title": "{huge_title}", "due_date": null}}}}]}}'
        )


def test_task_draft_invalid_due_date_raises():
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "task_draft", "description": "x", '
            '"payload": {"title": "Buy milk", "due_date": "not-a-date"}}]}'
        )


def test_task_draft_compact_iso_date_raises():
    """`date.fromisoformat` alone accepts compact/week-date ISO 8601 forms
    on Python 3.11+ -- this schema is strictly YYYY-MM-DD only (Codex
    round-1 MEDIUM fix)."""
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "task_draft", "description": "x", '
            '"payload": {"title": "Buy milk", "due_date": "20260715"}}]}'
        )


def test_task_draft_non_string_due_date_raises():
    with pytest.raises(PlanParseError):
        parse_plan(
            '{"steps": [{"kind": "task_draft", "description": "x", '
            '"payload": {"title": "Buy milk", "due_date": 20260715}}]}'
        )
