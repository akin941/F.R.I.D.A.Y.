import json

import pytest

from ai_core.adapters.fake import FAKE_MEMORY_EXTRACTION_RESPONSE
from memory.extraction import (
    MAX_CANDIDATES_PER_JOB,
    MAX_CONTENT_CHARS,
    MAX_TITLE_CHARS,
    ExtractionParseError,
    parse_candidates,
)


def valid_payload(**overrides):
    payload = {
        "version": 2,
        "candidates": [
            {
                "type": "preference",
                "title": "Coffee",
                "content": "Drinks coffee black.",
                "quote": "I drink it black",
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestHappyPath:
    def test_parses_valid_candidates(self):
        candidates = parse_candidates(valid_payload())
        assert candidates == [
            {
                "type": "preference",
                "title": "Coffee",
                "content": "Drinks coffee black.",
                "quote": "I drink it black",
            }
        ]

    def test_empty_candidates_list_is_valid(self):
        assert parse_candidates(valid_payload(candidates=[])) == []

    def test_all_conversation_derivable_memory_types_accepted(self):
        """WORK_LOG is deliberately excluded (Phase 1 p1-work-log, Codex
        round-6 P2): it's reserved for record_work_log()'s system_generated
        pairing (ADR-008 k.4) and must never be extractable from a
        conversation — see TestWorkLogTypeRejected below."""
        from memory.models import Memory

        for memory_type in Memory.Type.values:
            if memory_type == Memory.Type.WORK_LOG:
                continue
            payload = valid_payload(
                candidates=[{"type": memory_type, "title": "T", "content": "C", "quote": "Q"}]
            )
            assert parse_candidates(payload)[0]["type"] == memory_type


class TestSchemaV2:
    """Phase 8A D9: chat extraction schema v2 -- a quote is required and a
    v1-shaped output is a schema error."""

    def test_v1_shaped_output_is_rejected(self):
        payload = json.dumps(
            {"version": 1, "candidates": [{"type": "semantic", "title": "T", "content": "C"}]}
        )
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)

    def test_v2_without_a_quote_is_rejected(self):
        payload = json.dumps(
            {"version": 2, "candidates": [{"type": "semantic", "title": "T", "content": "C"}]}
        )
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)

    @pytest.mark.parametrize("quote", ["", "   ", 5, None, "x" * 1001])
    def test_invalid_quote_is_rejected(self, quote):
        payload = json.dumps(
            {
                "version": 2,
                "candidates": [{"type": "semantic", "title": "T", "content": "C", "quote": quote}],
            }
        )
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)


class TestWorkLogTypeRejected:
    def test_work_log_candidate_is_rejected(self):
        from memory.models import Memory

        payload = valid_payload(
            candidates=[{"type": Memory.Type.WORK_LOG, "title": "T", "content": "C", "quote": "Q"}]
        )
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)


class TestFakeFixtureLineage:
    """The ai_core FakeAdapter's memory.extraction fixture is a hand-written
    literal (ai_core must not import the memory app) — this test is the
    lineage guard that keeps the two in sync."""

    def test_fake_adapter_fixture_parses_successfully(self):
        candidates = parse_candidates(FAKE_MEMORY_EXTRACTION_RESPONSE)
        assert len(candidates) == 1
        assert candidates[0]["type"] == "preference"


class TestMalformedInput:
    def test_invalid_json_is_terminal(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates("not json{")

    def test_non_object_top_level_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(json.dumps([1, 2, 3]))

    def test_wrong_schema_version_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(valid_payload(version=3))

    def test_missing_version_is_rejected(self):
        payload = json.dumps({"candidates": []})
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)

    def test_candidates_not_a_list_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(valid_payload(candidates={"type": "semantic"}))

    def test_too_many_candidates_is_rejected(self):
        candidates = [
            {"type": "semantic", "title": f"T{i}", "content": f"C{i}", "quote": "Q"}
            for i in range(MAX_CANDIDATES_PER_JOB + 1)
        ]
        with pytest.raises(ExtractionParseError):
            parse_candidates(valid_payload(candidates=candidates))

    def test_exactly_max_candidates_is_accepted(self):
        candidates = [
            {"type": "semantic", "title": f"T{i}", "content": f"C{i}", "quote": "Q"}
            for i in range(MAX_CANDIDATES_PER_JOB)
        ]
        assert len(parse_candidates(valid_payload(candidates=candidates))) == MAX_CANDIDATES_PER_JOB

    def test_candidate_not_an_object_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(valid_payload(candidates=["not an object"]))

    def test_missing_required_key_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(valid_payload(candidates=[{"type": "semantic", "title": "T"}]))

    def test_invalid_type_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[{"type": "bogus", "title": "T", "content": "C", "quote": "Q"}]
                )
            )

    def test_empty_title_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[{"type": "semantic", "title": "  ", "content": "C", "quote": "Q"}]
                )
            )

    def test_oversized_title_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[
                        {
                            "type": "semantic",
                            "title": "x" * (MAX_TITLE_CHARS + 1),
                            "content": "C",
                            "quote": "Q",
                        }
                    ]
                )
            )

    def test_empty_content_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(candidates=[{"type": "semantic", "title": "T", "content": ""}])
            )

    def test_oversized_content_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[
                        {
                            "type": "semantic",
                            "title": "T",
                            "content": "x" * (MAX_CONTENT_CHARS + 1),
                        }
                    ]
                )
            )

    def test_non_string_title_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[{"type": "semantic", "title": 5, "content": "C", "quote": "Q"}]
                )
            )

    def test_extra_keys_on_candidate_are_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[
                        {"type": "semantic", "title": "T", "content": "C", "confidence": 0.9}
                    ]
                )
            )

    def test_extra_top_level_keys_are_rejected(self):
        payload = json.dumps({"version": 2, "candidates": [], "note": "hi"})
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)

    def test_boolean_true_is_not_accepted_as_schema_version_one(self):
        """`True == 1` in Python — the parser must not let a JSON boolean
        silently pass for the integer schema version."""
        payload = json.dumps({"version": True, "candidates": []})
        with pytest.raises(ExtractionParseError):
            parse_candidates(payload)

    def test_list_type_value_is_rejected_not_a_typeerror(self):
        """A non-hashable `type` value must not escape as a raw TypeError —
        it must be reported as the same terminal ExtractionParseError."""
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[{"type": ["semantic"], "title": "T", "content": "C", "quote": "Q"}]
                )
            )

    def test_boolean_type_value_is_rejected(self):
        with pytest.raises(ExtractionParseError):
            parse_candidates(
                valid_payload(
                    candidates=[{"type": True, "title": "T", "content": "C", "quote": "Q"}]
                )
            )
