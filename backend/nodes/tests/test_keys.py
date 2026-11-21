"""`NODE_SIGNING_KEY` loading and validation (Phase 9 brief D4)."""

import base64
import json
from pathlib import Path

import pytest

from nodes.keys import (
    SigningKeyUnavailable,
    published_test_key_ids,
    signing_key_id,
    signing_keyring,
)
from nodes.protocol.signing import SigningKey

VECTOR = json.loads(
    (Path(__file__).resolve().parents[1] / "protocol/vectors/signing.json").read_text("utf-8")
)


def test_the_configured_key_loads_and_its_id_is_derived(settings):
    key = SigningKey.generate()
    settings.NODE_SIGNING_KEY = key.private_b64()
    ring = signing_keyring()
    assert ring.key_id == key.key_id == signing_key_id()
    assert ring.retired_key_ids == frozenset()


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "missing"),
        ("   ", "missing"),
        (None, "missing"),
        ("not-base64!!", "invalid"),
        (base64.b64encode(b"\x01" * 16).decode(), "invalid"),
        (base64.b64encode(b"\x01" * 64).decode(), "invalid"),
    ],
)
def test_a_missing_or_malformed_key_is_unavailable(settings, value, reason):
    settings.NODE_SIGNING_KEY = value
    with pytest.raises(SigningKeyUnavailable) as exc:
        signing_keyring()
    assert exc.value.reason == reason
    assert signing_key_id() is None
    if value and value.strip():
        assert value not in str(exc.value)


def test_the_published_test_vector_key_is_refused(settings):
    assert VECTOR["key_id"] in published_test_key_ids()
    settings.NODE_SIGNING_KEY = VECTOR["private_key_b64"]
    with pytest.raises(SigningKeyUnavailable) as exc:
        signing_keyring()
    assert exc.value.reason == "test_key"


def test_retired_public_keys_verify_only(settings):
    old, new = SigningKey.generate(), SigningKey.generate()
    settings.NODE_SIGNING_KEY = new.private_b64()
    settings.NODE_SIGNING_KEYS_RETIRED = [old.public_key_b64, ""]
    ring = signing_keyring()
    assert ring.key_id == new.key_id
    assert ring.retired_key_ids == {old.key_id}


def test_a_malformed_retired_key_fails_closed(settings):
    settings.NODE_SIGNING_KEYS_RETIRED = ["garbage"]
    with pytest.raises(SigningKeyUnavailable) as exc:
        signing_keyring()
    assert exc.value.reason == "retired_invalid"


def test_the_retired_list_accepts_a_comma_separated_string(settings):
    a, b = SigningKey.generate(), SigningKey.generate()
    settings.NODE_SIGNING_KEYS_RETIRED = f"{a.public_key_b64}, {b.public_key_b64}"
    assert signing_keyring().retired_key_ids == {a.key_id, b.key_id}
