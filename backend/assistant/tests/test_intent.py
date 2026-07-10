import pytest

from assistant.intent import IntentParseError, parse_intent


class TestParseIntent:
    @pytest.mark.parametrize(
        "intent",
        ["chat", "memory_query", "knowledge_query", "task_draft"],
    )
    def test_non_tool_intents_parse_with_null_tool_name(self, intent):
        raw = f'{{"intent": "{intent}", "tool_name": null}}'
        assert parse_intent(raw) == {"intent": intent, "tool_name": None}

    def test_tool_request_parses_with_tool_name(self):
        raw = '{"intent": "tool_request", "tool_name": "vault_export"}'
        assert parse_intent(raw) == {"intent": "tool_request", "tool_name": "vault_export"}

    def test_malformed_json_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent("not json")

    def test_non_object_json_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent("[1, 2, 3]")

    def test_unexpected_top_level_keys_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "chat", "tool_name": null, "extra": 1}')

    def test_missing_tool_name_key_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "chat"}')

    def test_unrecognized_intent_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "delete_everything", "tool_name": null}')

    def test_non_string_intent_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": 1, "tool_name": null}')

    def test_tool_request_with_null_tool_name_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "tool_request", "tool_name": null}')

    def test_tool_request_with_blank_tool_name_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "tool_request", "tool_name": "   "}')

    def test_tool_request_with_non_string_tool_name_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "tool_request", "tool_name": 5}')

    def test_non_tool_intent_with_non_null_tool_name_raises(self):
        with pytest.raises(IntentParseError):
            parse_intent('{"intent": "chat", "tool_name": "vault_export"}')
