"""The ingestion scrubber and its corpus gate (Phase 8A brief D3).

The corpus (`scrubber_corpus/`) is synthetic and labelled. Secret-class items
are written with a `<<>>` split marker inside their provider prefixes so no
secret scanner mistakes the committed file for a real leak; the loader
removes it.

Gates: 100% recall on the secrets class (a regression fails the build); the
PII class is held to 100% recall on this corpus too and its precision is
reported; benign items must come through unchanged.
"""

import json
import re
import time
from pathlib import Path

import pytest

from memory.ingestion import scrubber
from memory.ingestion.scrubber import (
    iban_is_valid,
    luhn_is_valid,
    scrub_text,
    scrub_value,
    tckn_is_valid,
    truncate_scrubbed,
)

CORPUS = Path(__file__).parent / "scrubber_corpus"
SPLIT_MARKER = "<<>>"


def _load(name):
    rows = []
    for line in (CORPUS / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["text"] = row["text"].replace(SPLIT_MARKER, "")
            row["needles"] = [n.replace(SPLIT_MARKER, "") for n in row.get("needles", [])]
            rows.append(row)
    return rows


# Phase 8B branch 8 (brief D14): synthetic Claude Code and Codex owner-turn
# fragments, labelled per class; the same 100% secrets-recall gate covers
# them, for this scrubber and the vendored node copy (test_transcript_corpus).
TRANSCRIPTS = _load("transcripts.jsonl")
SECRETS = _load("secrets.jsonl") + [r for r in TRANSCRIPTS if r["class"] == "secret"]
PII_ITEMS = _load("pii.jsonl") + [r for r in TRANSCRIPTS if r["class"] == "pii"]
BENIGN = _load("benign.jsonl")


def test_corpus_is_labelled():
    for row in SECRETS + PII_ITEMS:
        assert row["id"] and row["type"] and row["needles"], row
        for needle in row["needles"]:
            assert needle in row["text"], row["id"]
    assert len({row["id"] for row in SECRETS + PII_ITEMS + BENIGN}) == len(
        SECRETS + PII_ITEMS + BENIGN
    )


def _missed(rows):
    missed = []
    for row in rows:
        out = scrub_text(row["text"]).text
        if any(needle in out for needle in row["needles"]):
            missed.append(row["id"])
    return missed


def test_secrets_recall_is_100_percent():
    """The release gate (ADR-012 decision 3): every secret in the corpus is
    gone from the scrubbed text."""
    missed = _missed(SECRETS)
    assert missed == [], f"secrets recall regression: {missed}"


def test_pii_recall_and_precision_report(capsys):
    missed = _missed(PII_ITEMS)
    recall = 1 - len(missed) / len(PII_ITEMS)
    false_positives = [row["id"] for row in BENIGN if scrub_text(row["text"]).hits]
    precision_items = len(PII_ITEMS) - len(missed)
    precision = precision_items / max(1, precision_items + len(false_positives))
    with capsys.disabled():
        print(f"\n[scrubber corpus] PII recall={recall:.2%} precision={precision:.2%}")
    assert missed == [], f"PII recall regression: {missed}"


@pytest.mark.parametrize("row", SECRETS, ids=lambda row: row["id"])
def test_secret_items_become_typed_placeholders(row):
    result = scrub_text(row["text"])
    assert f"[SECRET:{row['type']}]" in result.text
    assert result.report.get(f"SECRET:{row['type']}", 0) >= 1


@pytest.mark.parametrize("row", PII_ITEMS, ids=lambda row: row["id"])
def test_pii_items_become_typed_placeholders(row):
    result = scrub_text(row["text"])
    assert f"[PII:{row['type']}]" in result.text
    assert result.report.get(f"PII:{row['type']}", 0) >= 1


@pytest.mark.parametrize("row", BENIGN, ids=lambda row: row["id"])
def test_benign_items_are_untouched(row):
    result = scrub_text(row["text"])
    assert result.text == row["text"]
    assert result.report == {}


@pytest.mark.parametrize("row", SECRETS + PII_ITEMS, ids=lambda row: row["id"])
def test_report_never_carries_matched_text(row):
    result = scrub_text(row["text"])
    dumped = json.dumps(result.report)
    for needle in row["needles"]:
        assert needle not in dumped
    for key, count in result.report.items():
        assert re.fullmatch(r"(SECRET|PII|TERM):[a-z0-9_]+", key)
        assert isinstance(count, int) and count > 0


@pytest.mark.parametrize("row", SECRETS + PII_ITEMS + BENIGN, ids=lambda row: row["id"])
def test_scrubbing_is_idempotent(row):
    once = scrub_text(row["text"]).text
    twice = scrub_text(once)
    assert twice.text == once
    assert twice.report == {}


def test_checksums():
    assert tckn_is_valid("10000000146")
    assert not tckn_is_valid("10000000147")
    assert not tckn_is_valid("01234567890")
    assert iban_is_valid("TR33 0006 1005 1978 6457 8413 26")
    assert not iban_is_valid("TR33 0006 1005 1978 6457 8413 27")
    assert luhn_is_valid("4111111111111111")
    assert not luhn_is_valid("4111111111111112")
    assert not luhn_is_valid("0000000000000000")


def test_valid_looking_invalid_numbers_are_not_hits():
    text = "ids 12345678901 and 4111 1111 1111 1112 and TR33 0006 1005 1978 6457 8413 27"
    assert scrub_text(text).report == {}


def test_scrub_terms_replace_case_insensitively_longest_first():
    terms = [("aaaa1111-0000", "Acme"), ("bbbb2222-0000", "Acme Holding")]
    result = scrub_text("Met ACME HOLDING and acme people.", terms=terms)
    assert result.text == "Met [TERM:bbbb22220000] and [TERM:aaaa11110000] people."
    assert result.report == {"TERM:bbbb22220000": 1, "TERM:aaaa11110000": 1}


def test_scrub_value_scrubs_keys_and_values_and_bounds_the_shape():
    nested = {"a": {"b": {"c": {"d": "too deep"}}}}
    value, report = scrub_value(
        {
            "who": "mail me at x@example.com",
            "n": 3,
            "ok": True,
            "list": list(range(80)),
            "long": "x" * 2000,
            **nested,
        }
    )
    assert value["who"] == "mail me at [PII:email]"
    assert value["n"] == 3 and value["ok"] is True
    assert len(value["list"]) == scrubber.METADATA_MAX_ITEMS
    assert len(value["long"]) == scrubber.METADATA_MAX_STRING
    assert value["a"]["b"]["c"] is None
    assert report == {"PII:email": 1}


def test_scrubber_is_standard_library_only():
    """Branch 3 vendors this module into the stdlib-only `friday-hook`."""
    source = Path(scrubber.__file__).read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+([a-zA-Z_][\w.]*)", source, re.MULTILINE)
    allowed = {"bisect", "math", "re", "collections", "dataclasses"}
    assert set(imports) <= allowed, set(imports) - allowed


# --- bounded work (review round 1: ReDoS / CPU DoS) --------------------------

# Generous for a slow CI box; the unbounded version took 15s+ on 20 KB.
SCRUB_TIME_BUDGET_S = 3.0


@pytest.mark.parametrize(
    "text",
    [
        "A" * 100_000,
        "ab" * 50_000,
        "0123456789abcdef" * 6_250,
        " ".join(f"user{i}@example.com" for i in range(6_000)),
        "a." * 50_000,
        "password" * 12_500,
        "pwd-" * 25_000,
        "password= " * 12_000,
        "-----BEGIN X-----\n" * 5_000,
        "x://" * 25_000,
    ],
    ids=[
        "run-A",
        "run-ab",
        "hex",
        "emails",
        "dots",
        "secret-word",
        "secret-dash",
        "assignments",
        "pem-headers",
        "schemes",
    ],
)
def test_hostile_input_is_scrubbed_within_a_time_budget(text):
    started = time.perf_counter()
    scrub_text(text)
    assert time.perf_counter() - started < SCRUB_TIME_BUDGET_S


def test_input_is_capped_before_scrubbing_at_whitespace():
    secret = "Zx81Kd02LmQpRt5UvWy7Ab9Cd3Ef4GhZx81Kd02"
    filler = "word " * ((scrubber.MAX_SCRUB_INPUT - 10) // 5)
    text = filler + secret + " tail"
    assert len(text) > scrubber.MAX_SCRUB_INPUT
    bounded = scrubber.bound_input(text)
    assert len(bounded) <= scrubber.MAX_SCRUB_INPUT
    # The token straddling the cap is dropped whole, never kept as a fragment.
    assert bounded == filler.rstrip()
    assert "Zx81" not in scrub_text(text).text


def test_input_without_whitespace_is_cut_hard_at_the_cap():
    assert len(scrubber.bound_input("A" * 200_000)) == scrubber.MAX_SCRUB_INPUT
    assert scrubber.bound_input("short") == "short"


def test_scrub_value_bounds_raw_strings_before_scrubbing():
    started = time.perf_counter()
    value, _ = scrub_value({"desc": "password" * 100_000})
    assert time.perf_counter() - started < SCRUB_TIME_BUDGET_S
    assert len(value["desc"]) == scrubber.METADATA_MAX_STRING


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"password": "hunter two three"}', '{"password": "[SECRET:assignment]"}'),
        ('pwd: "my long pass"', 'pwd: "[SECRET:assignment]"'),
        ("secret='a b c'", "secret='[SECRET:assignment]'"),
        ("token=abc def", "token=[SECRET:assignment] def"),
    ],
)
def test_quoted_secret_values_are_scrubbed_up_to_the_closing_quote(text, expected):
    result = scrub_text(text)
    assert result.text == expected
    assert result.report == {"SECRET:assignment": 1}
    assert scrub_text(result.text).text == result.text


# --- truncation never cuts a placeholder ------------------------------------


@pytest.mark.parametrize("limit", range(4, 26))
def test_truncate_scrubbed_drops_a_straddling_placeholder_whole(limit):
    text = "abc [SECRET:aws_key] def"
    out = truncate_scrubbed(text, limit)
    if 4 < limit < len("abc [SECRET:aws_key]"):
        assert out == "abc "
    else:
        assert out == text[:limit]
    assert scrub_text(out).text == out  # idempotent under a second scrub


def test_truncate_scrubbed_is_a_noop_under_the_limit():
    assert truncate_scrubbed("[PII:email]", 11) == "[PII:email]"
    assert truncate_scrubbed("short", 100) == "short"


def test_metadata_string_and_key_caps_never_split_a_placeholder():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    value = "x" * (scrubber.METADATA_MAX_STRING - 5) + " " + secret
    key = "k" * (scrubber.METADATA_MAX_KEY - 5) + " " + secret
    cleaned, report = scrub_value({key: value})
    [(out_key, out_value)] = cleaned.items()
    assert out_value == "x" * (scrubber.METADATA_MAX_STRING - 5) + " "
    assert out_key == "k" * (scrubber.METADATA_MAX_KEY - 5) + " "
    assert report == {"SECRET:aws_key": 2}


# --- UUIDs are not PII (regression: flaky harness outcome ingestion) ---------

# Real uuid4 values whose digit runs a PII rule used to read as a phone or a
# card number, corrupting a `job_id` in episode metadata.
_UUIDS_THAT_LOOK_LIKE_PII = [
    "23932636-83e3-4882-9336-af7bbc1efa76",
    "39028399-98fb-4577-bae1-ac182c54de0b",
    "17970375-8783-4471-a1fa-aec062311d6c",
]


@pytest.mark.parametrize("value", _UUIDS_THAT_LOOK_LIKE_PII)
def test_uuid_is_never_read_as_pii(value):
    assert scrubber.scrub_text(value).text == value
    assert scrubber.scrub_value({"job_id": value})[0] == {"job_id": value}


def test_random_uuids_survive_scrubbing():
    import uuid

    for _ in range(5000):
        value = str(uuid.uuid4())
        assert scrubber.scrub_text(value).text == value


def test_pii_next_to_a_uuid_is_still_scrubbed():
    text = f"job {_UUIDS_THAT_LOOK_LIKE_PII[0]} called 0532 123 45 67"
    result = scrubber.scrub_text(text)
    assert _UUIDS_THAT_LOOK_LIKE_PII[0] in result.text
    assert "[PII:phone]" in result.text
    assert "0532" not in result.text


# --- UUID shield cannot be used to smuggle PII (review 2026-11-25) ----------

_UUID_A = "23932636-83e3-4882-9336-af7bbc1efa76"


def test_iban_followed_by_a_uuid_is_still_scrubbed():
    for uuid_value in (_UUID_A, _UUID_A.upper()):
        result = scrubber.scrub_text(f"TR33 0006 1005 1978 6457 8413 26 {uuid_value}")
        assert "[PII:iban]" in result.text
        assert "6457" not in result.text


def test_tckn_inside_a_uuid_shaped_token_is_still_scrubbed():
    result = scrubber.scrub_text("00000000-0000-0000-0000-a10000000146")
    assert "[PII:tckn]" in result.text


def test_all_digit_uuid_shape_is_not_shielded():
    # A crafted, RFC-4122-valid but all-digit token gets no shield.
    value = "41111111-1111-4111-8111-111111111111"
    assert _uuid_shield_spans(value) == []


def test_non_rfc4122_uuid_shape_is_not_shielded():
    assert _uuid_shield_spans("23932636-83e3-0882-9336-af7bbc1efa76") == []  # version 0
    assert _uuid_shield_spans("23932636-83e3-4882-c336-af7bbc1efa76") == []  # variant c


def _uuid_shield_spans(text):
    return scrubber._uuid_spans(text)
