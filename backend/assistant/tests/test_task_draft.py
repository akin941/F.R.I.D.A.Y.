from datetime import date

import pytest

from assistant.task_draft import TaskDraftParseError, parse_task_draft


class TestParseTaskDraft:
    def test_title_only_parses_with_null_due_date(self):
        raw = '{"title": "Call mom", "due_date": null}'
        assert parse_task_draft(raw) == {"title": "Call mom", "due_date": None}

    def test_title_and_due_date_parse(self):
        raw = '{"title": "Submit report", "due_date": "2026-07-15"}'
        assert parse_task_draft(raw) == {"title": "Submit report", "due_date": date(2026, 7, 15)}

    def test_malformed_json_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft("not json")

    def test_non_object_json_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft("[1, 2]")

    def test_unexpected_top_level_keys_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": "x", "due_date": null, "extra": 1}')

    def test_missing_due_date_key_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": "x"}')

    def test_empty_title_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": "", "due_date": null}')

    def test_blank_title_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": "   ", "due_date": null}')

    def test_non_string_title_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": 5, "due_date": null}')

    def test_title_exceeding_max_chars_raises(self):
        raw = f'{{"title": "{"x" * 256}", "due_date": null}}'
        with pytest.raises(TaskDraftParseError):
            parse_task_draft(raw)

    def test_invalid_due_date_string_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": "x", "due_date": "tomorrow"}')

    def test_non_string_non_null_due_date_raises(self):
        with pytest.raises(TaskDraftParseError):
            parse_task_draft('{"title": "x", "due_date": 5}')
