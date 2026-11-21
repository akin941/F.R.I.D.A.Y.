"""Ed25519 envelopes (Phase 9 brief D4/D5): round trip, a flipped bit, a
wrong key, a `sig` inside the signed body, stable key ids, retired keys
verify but never sign, and the shared signing vector."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from nodes.protocol import canonicalize, sha256_hex, validate
from nodes.protocol.signing import (
    KeyRing,
    SignatureError,
    SigningKey,
    b64url_decode,
    b64url_encode,
    build_invoke,
    key_id_for,
    load_public_key_b64,
    new_nonce,
    sign_envelope,
    verify_envelope,
)

VECTOR = json.loads(
    (Path(__file__).resolve().parents[1] / "protocol/vectors/signing.json").read_text("utf-8")
)
NODE_ID = "7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f70"
WORKSPACE_ID = "0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b"


def _envelope(**overrides):
    fields = {
        "node_id": NODE_ID,
        "workspace_id": WORKSPACE_ID,
        "capability": "git.status",
        "capability_version": 1,
        "args": {"repo_path": "~/work/acme"},
        "trace_id": "t-1",
        "issued_at": 1_800_000_000,
        "ttl_seconds": 30,
    }
    fields.update(overrides)
    return build_invoke(**fields)


def test_round_trip_and_the_envelope_is_schema_valid():
    key = SigningKey.generate()
    signed = sign_envelope(_envelope(), key)
    verify_envelope(signed, {key.key_id: key.public_key_bytes})
    assert signed["key_id"] == key.key_id
    assert signed["grant"] is None and signed["class"] == "read"
    assert signed["args_sha256"] == sha256_hex({"repo_path": "~/work/acme"})
    assert signed["expires_at"] - signed["issued_at"] == 30
    validate(signed, "server_to_node")


def test_a_flipped_bit_fails():
    key = SigningKey.generate()
    signed = sign_envelope(_envelope(), key)
    pinned = {key.key_id: key.public_key_bytes}
    raw_sig = bytearray(b64url_decode(signed["sig"]))
    raw_sig[0] ^= 0x01
    with pytest.raises(SignatureError) as exc:
        verify_envelope({**signed, "sig": b64url_encode(bytes(raw_sig))}, pinned)
    assert exc.value.code == "bad_signature"
    # ...and a changed field under an intact signature (args after signing).
    tampered = {**signed, "args": {"repo_path": "~/work/other"}}
    with pytest.raises(SignatureError) as exc:
        verify_envelope(tampered, pinned)
    assert exc.value.code == "bad_signature"


def test_a_wrong_key_fails():
    key, other = SigningKey.generate(), SigningKey.generate()
    signed = sign_envelope(_envelope(), key)
    # The other key pinned under the signer's id: the signature does not verify.
    with pytest.raises(SignatureError) as exc:
        verify_envelope(signed, {key.key_id: other.public_key_bytes})
    assert exc.value.code == "bad_signature"
    # The signer's id not pinned at all: unknown_key (D5 check 5 before 6).
    with pytest.raises(SignatureError) as exc:
        verify_envelope(signed, {other.key_id: other.public_key_bytes})
    assert exc.value.code == "unknown_key"


def test_a_sig_field_inside_the_signed_body_fails():
    key = SigningKey.generate()
    with pytest.raises(SignatureError):
        sign_envelope({**_envelope(), "sig": "x"}, key)
    # A signature computed over a body that itself contained `sig` never
    # verifies: the verifier always strips `sig` before checking.
    body = {**_envelope(), "key_id": key.key_id, "sig": "A" * 86}
    forged = {**body, "sig": b64url_encode(key.sign(canonicalize(body)))}
    with pytest.raises(SignatureError) as exc:
        verify_envelope(forged, {key.key_id: key.public_key_bytes})
    assert exc.value.code == "bad_signature"


def test_malformed_signatures_fail():
    key = SigningKey.generate()
    signed = sign_envelope(_envelope(), key)
    pinned = {key.key_id: key.public_key_bytes}
    for sig in (None, "", "!!!", signed["sig"][:-2], signed["sig"] + "A", 7):
        with pytest.raises(SignatureError) as exc:
            verify_envelope({**signed, "sig": sig}, pinned)
        assert exc.value.code == "bad_signature"


def test_key_id_derivation_is_stable():
    key = SigningKey.from_b64(VECTOR["private_key_b64"])
    assert key.key_id == VECTOR["key_id"]
    assert key.key_id == hashlib.sha256(key.public_key_bytes).hexdigest()[:16]
    assert key_id_for(base64.b64decode(VECTOR["public_key_b64"])) == VECTOR["key_id"]
    assert SigningKey.from_b64(VECTOR["private_key_b64"]).key_id == key.key_id
    assert key.public_entry() == {
        "key_id": VECTOR["key_id"],
        "alg": "Ed25519",
        "public_key_b64": VECTOR["public_key_b64"],
    }


def test_the_shared_signing_vector():
    key = SigningKey.from_b64(VECTOR["private_key_b64"])
    envelope = VECTOR["envelope"]
    body = {k: v for k, v in envelope.items() if k != "sig"}
    assert canonicalize(body).decode() == VECTOR["signed_body_canonical"]
    assert canonicalize(envelope["args"]).decode() == VECTOR["args_canonical"]
    assert envelope["args_sha256"] == sha256_hex(envelope["args"])
    # Ed25519 is deterministic: re-signing reproduces the vector's sig.
    assert sign_envelope({k: v for k, v in body.items() if k != "key_id"}, key) == envelope
    verify_envelope(envelope, {VECTOR["key_id"]: load_public_key_b64(VECTOR["public_key_b64"])})
    validate(envelope, "server_to_node")


def test_retired_keys_verify_but_never_sign():
    old, new = SigningKey.generate(), SigningKey.generate()
    ring = KeyRing(new, [old.public_key_bytes])
    assert ring.retired_key_ids == {old.key_id}
    assert set(ring.verification_keys()) == {old.key_id, new.key_id}
    # An envelope signed (earlier) by the now-retired key still verifies...
    ring.verify(sign_envelope(_envelope(), old))
    # ...but the ring only ever signs with the active key.
    for _ in range(3):
        assert ring.sign(_envelope())["key_id"] == new.key_id
    # A retired key is public material only: nothing in the ring holds its
    # private half.
    assert not any(isinstance(v, SigningKey) for v in ring.verification_keys().values())


def test_the_active_key_is_never_also_listed_as_retired():
    key = SigningKey.generate()
    ring = KeyRing(key, [key.public_key_bytes])
    assert ring.retired_key_ids == frozenset()


@pytest.mark.parametrize(
    "value",
    ["", "not base64!", base64.b64encode(b"x" * 31).decode(), base64.b64encode(b"x" * 33).decode()],
)
def test_malformed_keys_are_refused_without_echoing_them(value):
    with pytest.raises(SignatureError) as exc:
        SigningKey.from_b64(value)
    assert exc.value.code == "invalid_key"
    assert value not in str(exc.value) or not value
    with pytest.raises(SignatureError):
        load_public_key_b64(value)


def test_nonces_are_32_bytes_and_unique():
    nonces = {new_nonce() for _ in range(100)}
    assert len(nonces) == 100
    assert all(len(b64url_decode(n)) == 32 and len(n) == 43 for n in nonces)


def test_repr_never_shows_key_material():
    key = SigningKey.generate()
    assert key.private_b64() not in repr(key)
    assert repr(key) == f"<SigningKey {key.key_id}>"
