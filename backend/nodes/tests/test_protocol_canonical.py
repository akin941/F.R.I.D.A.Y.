"""Canonical JSON (Phase 9 brief D5): the shared vectors, UTF-16 key
order, escapes, non-ASCII, and the refusals (floats above all)."""

import hashlib
import json
from pathlib import Path

import pytest

from nodes.protocol.canonical import (
    MAX_SAFE_INTEGER,
    CanonicalizationError,
    canonicalize,
    parse_strict,
    sha256_hex,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "protocol/vectors/canonical.json").read_text("utf-8")
)


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda v: v["name"])
def test_valid_vectors(vector):
    data = canonicalize(vector["input"])
    assert data == vector["canonical"].encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == vector["sha256"] == sha256_hex(vector["input"])


@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda v: v["name"])
def test_invalid_vectors_are_refused(vector):
    raw = bytes.fromhex(vector["hex"]) if "hex" in vector else vector["json"]
    with pytest.raises(CanonicalizationError):
        canonicalize(parse_strict(raw))


def test_the_vector_file_covers_the_brief_cases():
    names = " ".join(v["name"] for v in VECTORS["valid"] + VECTORS["invalid"])
    for needle in ("UTF-16", "escapes", "non-ASCII", "float", "duplicate key"):
        assert needle in names


def test_keys_sort_by_utf16_code_units_not_code_points():
    # U+1F600 is the surrogate pair D83D DE00, which sorts BEFORE U+FB33 in
    # UTF-16 although its code point is higher.
    hebrew, emoji = chr(0xFB33), chr(0x1F600)
    assert canonicalize({hebrew: 1, emoji: 2}) == f'{{"{emoji}":2,"{hebrew}":1}}'.encode()


def test_floats_are_refused_everywhere():
    for value in (1.5, 1.0, float("nan"), float("inf"), {"a": [0.1]}):
        with pytest.raises(CanonicalizationError):
            canonicalize(value)


def test_integer_bounds_and_bool_are_distinct():
    assert canonicalize([True, 1, False, 0]) == b"[true,1,false,0]"
    assert canonicalize(MAX_SAFE_INTEGER) == str(MAX_SAFE_INTEGER).encode()
    with pytest.raises(CanonicalizationError):
        canonicalize(MAX_SAFE_INTEGER + 1)


def test_unsupported_values_are_refused():
    for value in ({1: "a"}, {"a": object()}, b"bytes", {"a": {1, 2}}, "\ud800"):
        with pytest.raises(CanonicalizationError):
            canonicalize(value)


def test_control_characters_and_escapes():
    expected = b'"\\u0000\\b\\t\\n\\u000b\\f\\r\\u001f\x7f"'
    assert canonicalize("\u0000\b\t\n\x0b\f\r\x1f\x7f") == expected
    assert canonicalize('a"b\\c/d') == b'"a\\"b\\\\c/d"'


def test_no_whitespace_and_nested_order():
    assert canonicalize({"b": [1, {"d": None, "c": True}], "a": "x"}) == (
        b'{"a":"x","b":[1,{"c":true,"d":null}]}'
    )


def test_parse_strict_round_trip_and_refusals():
    assert parse_strict(b'{"b":1,"a":[true,null,"\\u00e7"]}') == {"b": 1, "a": [True, None, "ç"]}
    for raw in ('{"a":1,"a":1}', "[1e2]", "[NaN]", "[-Infinity]", "[1", b"\xff", "[1.0]"):
        with pytest.raises(CanonicalizationError):
            parse_strict(raw)
