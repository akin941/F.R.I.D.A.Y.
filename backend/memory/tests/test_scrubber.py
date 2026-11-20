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
from pathlib import Path

import pytest

from memory.ingestion import scrubber
from memory.ingestion.scrubber import (
    iban_is_valid,
    luhn_is_valid,
    scrub_text,
    scrub_value,
    tckn_is_valid,
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


SECRETS = _load("secrets.jsonl")
PII_ITEMS = _load("pii.jsonl")
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
    allowed = {"math", "re", "collections", "dataclasses"}
    assert set(imports) <= allowed, set(imports) - allowed
