import pytest

from agents.skill_match import SkillMatchParseError, parse_skill_match


class TestParseSkillMatch:
    def test_valid_null_match(self):
        assert parse_skill_match('{"skill_name": null}') is None

    def test_valid_named_match(self):
        assert parse_skill_match('{"skill_name": "weekly_export"}') == "weekly_export"

    def test_invalid_json_raises(self):
        with pytest.raises(SkillMatchParseError):
            parse_skill_match("not json")

    def test_non_object_top_level_raises(self):
        with pytest.raises(SkillMatchParseError):
            parse_skill_match("[]")

    def test_unexpected_top_level_keys_raises(self):
        with pytest.raises(SkillMatchParseError):
            parse_skill_match('{"skill_name": null, "confidence": 0.9}')

    def test_missing_skill_name_key_raises(self):
        with pytest.raises(SkillMatchParseError):
            parse_skill_match("{}")

    def test_non_string_non_null_skill_name_raises(self):
        with pytest.raises(SkillMatchParseError):
            parse_skill_match('{"skill_name": 42}')
