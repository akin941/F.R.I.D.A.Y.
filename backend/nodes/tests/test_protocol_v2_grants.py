"""Domain-separated signing and the invocation grant (Phase 10 brief D1,
D2): round trips; a flipped bit, a wrong key and an unpinned key id fail;
a v1 envelope signature presented as a grant and a grant signature
presented as an envelope fail; TTL > 120 is refused at build; field
binding fails for each shared field; and the committed v2 vectors are
byte-stable and verify."""

import json

import pytest

from nodes.protocol import validate
from nodes.protocol.canonical import canonicalize
from nodes.protocol.grants import (
    SHARED_FIELDS,
    GrantError,
    build_gated_invoke,
    build_grant,
    check_grant_binding,
    check_grant_ttl,
    sign_grant,
    verify_grant_signature,
)
from nodes.protocol.messages import MAX_GRANT_TTL_SECONDS
from nodes.protocol.signing import (
    GRANT_DOMAIN_V2,
    INVOKE_DOMAIN_V2,
    KeyRing,
    SignatureError,
    SigningKey,
    b64url_decode,
    b64url_encode,
    build_invoke,
    envelope_domain,
    sign_envelope,
    sign_with_domain,
    verify_envelope,
    verify_with_domain,
)
from nodes.tests import vectors_v2
from nodes.tests.vectors_v2 import (
    APPROVAL_ID,
    NODE_ID,
    PINNED,
    UNPINNED,
    WORKSPACE_ID,
    gated_envelope,
)

PIN = {PINNED.key_id: PINNED.public_key_bytes}


def _strip(signed):
    return {k: v for k, v in signed.items() if k not in ("sig", "key_id")}


def _flip(sig):
    raw = bytearray(b64url_decode(sig))
    raw[-1] ^= 0x80
    return b64url_encode(bytes(raw))


# --- domains ---


def test_the_domains_are_the_briefs():
    assert INVOKE_DOMAIN_V2 == b"friday-node/invoke/v2\n"
    assert GRANT_DOMAIN_V2 == b"friday-node/grant/v2\n"
    assert envelope_domain(1) == b""
    assert envelope_domain(2) == INVOKE_DOMAIN_V2
    for bad in (0, 3, True, "2", None):
        with pytest.raises(SignatureError):
            envelope_domain(bad)


def test_a_v2_envelope_is_signed_in_the_invoke_domain():
    envelope = vectors_v2.plain_envelope(
        "apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm"
    )
    body = {k: v for k, v in envelope.items() if k != "sig"}
    PINNED_PUBLIC = PINNED._private.public_key()
    PINNED_PUBLIC.verify(b64url_decode(envelope["sig"]), INVOKE_DOMAIN_V2 + canonicalize(body))
    verify_envelope(envelope, PIN)
    verify_envelope(envelope, PIN, version=2)
    with pytest.raises(SignatureError) as exc:
        verify_envelope(envelope, PIN, version=1)
    assert exc.value.code == "bad_signature"


def test_a_v1_envelope_signature_never_verifies_as_v2():
    v2 = build_invoke(
        node_id=NODE_ID,
        workspace_id=WORKSPACE_ID,
        capability="git.status",
        capability_version=1,
        args={"repo_path": "~/work/acme"},
        trace_id="t",
        issued_at=1_800_000_000,
        ttl_seconds=30,
        version=2,
    )
    # The same body signed the v1 way (no domain) is refused as v2...
    undomained = sign_with_domain(v2, PINNED, b"")
    with pytest.raises(SignatureError) as exc:
        verify_envelope(undomained, PIN)
    assert exc.value.code == "bad_signature"
    # ...and a real v1 envelope relabelled `v: 2` too.
    v1 = sign_envelope({**v2, "v": 1}, PINNED)
    verify_envelope(v1, PIN, version=1)
    with pytest.raises(SignatureError):
        verify_envelope({**v1, "v": 2}, PIN)


def test_a_v2_envelope_signature_never_verifies_as_v1():
    signed = sign_envelope(
        build_invoke(
            node_id=NODE_ID,
            workspace_id=WORKSPACE_ID,
            capability="git.status",
            capability_version=1,
            args={"repo_path": "~/work/acme"},
            trace_id="t",
            issued_at=1_800_000_000,
            ttl_seconds=30,
            version=2,
        ),
        PINNED,
    )
    with pytest.raises(SignatureError):
        verify_envelope({**signed, "v": 1}, PIN)


def test_build_invoke_refuses_unknown_versions():
    with pytest.raises(ValueError):
        build_invoke(
            node_id=NODE_ID,
            workspace_id=WORKSPACE_ID,
            capability="git.status",
            capability_version=1,
            args={},
            trace_id="t",
            issued_at=1,
            ttl_seconds=1,
            version=3,
        )


# --- grants: round trip and failures ---


def test_round_trip_gated_envelope():
    envelope = gated_envelope()
    validate(envelope, "server_to_node", 2)
    verify_envelope(envelope, PIN, version=2)
    grant = envelope["grant"]
    verify_grant_signature(grant, PIN)
    check_grant_binding(grant, envelope)
    check_grant_ttl(grant)
    assert grant["approval_id"] == APPROVAL_ID
    assert grant["class"] == envelope["class"] == "gate"
    assert grant["key_id"] == envelope["key_id"] == PINNED.key_id
    for name in SHARED_FIELDS:
        assert grant[name] == envelope[name]


def test_a_keyring_signs_with_its_active_key_only():
    retired = SigningKey.generate()
    ring = KeyRing(PINNED, [retired.public_key_bytes])
    envelope = gated_envelope(signer=ring)
    assert envelope["key_id"] == envelope["grant"]["key_id"] == PINNED.key_id
    with pytest.raises(TypeError):
        gated_envelope(signer="not a key")


def test_a_flipped_grant_bit_fails():
    grant = gated_envelope()["grant"]
    with pytest.raises(GrantError) as exc:
        verify_grant_signature({**grant, "sig": _flip(grant["sig"])}, PIN)
    assert exc.value.code == "bad_grant_signature"
    with pytest.raises(GrantError) as exc:
        verify_grant_signature({**grant, "approval_id": NODE_ID}, PIN)
    assert exc.value.code == "bad_grant_signature"


def test_a_wrong_key_fails():
    grant = gated_envelope(signer=UNPINNED)["grant"]
    with pytest.raises(GrantError) as exc:
        verify_grant_signature(grant, {UNPINNED.key_id: PINNED.public_key_bytes})
    assert exc.value.code == "bad_grant_signature"


def test_an_unpinned_key_id_fails():
    grant = gated_envelope(signer=UNPINNED)["grant"]
    with pytest.raises(GrantError) as exc:
        verify_grant_signature(grant, PIN)
    assert exc.value.code == "unknown_key"


def test_a_v1_envelope_signature_presented_as_a_grant_fails():
    grant = gated_envelope()["grant"]
    as_v1 = sign_with_domain(_strip(grant), PINNED, b"")
    as_envelope = sign_with_domain(_strip(grant), PINNED, INVOKE_DOMAIN_V2)
    for forged in (as_v1, as_envelope):
        with pytest.raises(GrantError) as exc:
            verify_grant_signature(forged, PIN)
        assert exc.value.code == "bad_grant_signature"


def test_a_grant_signature_presented_as_an_envelope_fails():
    envelope = gated_envelope()
    body = _strip(envelope)
    forged = sign_with_domain(body, PINNED, GRANT_DOMAIN_V2)
    with pytest.raises(SignatureError) as exc:
        verify_envelope(forged, PIN)
    assert exc.value.code == "bad_signature"
    # The grant's own signature as the envelope's: still no.
    with pytest.raises(SignatureError):
        verify_envelope({**envelope, "sig": envelope["grant"]["sig"]}, PIN)
    with pytest.raises(SignatureError):
        verify_with_domain(envelope["grant"], PIN, INVOKE_DOMAIN_V2)


def test_a_non_mapping_grant_fails():
    with pytest.raises(GrantError):
        verify_grant_signature(None, PIN)


# --- lifetime ---


@pytest.mark.parametrize("ttl", [121, 600, 0, -5])
def test_a_long_or_empty_ttl_is_refused_at_build(ttl):
    with pytest.raises(GrantError) as exc:
        gated_envelope(ttl_seconds=ttl)
    assert exc.value.code == "ttl_too_long"
    with pytest.raises(GrantError):
        build_grant(
            approval_id=APPROVAL_ID,
            node_id=NODE_ID,
            workspace_id=WORKSPACE_ID,
            capability="repo.command.run",
            capability_version=1,
            args_sha256="0" * 64,
            nonce="A" * 43,
            issued_at=1_800_000_000,
            expires_at=1_800_000_000 + ttl,
        )


def test_the_ceiling_itself_is_allowed():
    envelope = gated_envelope(ttl_seconds=MAX_GRANT_TTL_SECONDS)
    check_grant_ttl(envelope["grant"])
    for bad in (True, 1.5, "90"):
        with pytest.raises(GrantError):
            gated_envelope(ttl_seconds=bad)


def test_check_grant_ttl_refuses_long_and_non_integer_times():
    grant = gated_envelope()["grant"]
    with pytest.raises(GrantError):
        check_grant_ttl({**grant, "expires_at": grant["issued_at"] + 121})
    with pytest.raises(GrantError):
        check_grant_ttl({**grant, "expires_at": True})
    with pytest.raises(GrantError):
        check_grant_ttl(grant, max_ttl=60)


def test_a_malformed_grant_is_never_signed():
    grant = build_grant(
        approval_id="not-a-uuid",
        node_id=NODE_ID,
        workspace_id=WORKSPACE_ID,
        capability="repo.command.run",
        capability_version=1,
        args_sha256="0" * 64,
        nonce="A" * 43,
        issued_at=1_800_000_000,
        expires_at=1_800_000_090,
    )
    with pytest.raises(Exception) as exc:
        sign_grant(grant, PINNED)
    assert getattr(exc.value, "code", None) == "schema"


# --- binding ---


@pytest.mark.parametrize("name", SHARED_FIELDS)
def test_binding_fails_for_each_shared_field(name):
    envelope = gated_envelope()
    grant = envelope["grant"]
    other = vectors_v2._other_value(name, envelope[name])
    with pytest.raises(GrantError) as exc:
        check_grant_binding(grant, {**envelope, name: other})
    assert exc.value.code == "grant_mismatch"
    with pytest.raises(GrantError):
        check_grant_binding({**grant, name: other}, envelope)
    missing = {k: v for k, v in envelope.items() if k != name}
    with pytest.raises(GrantError):
        check_grant_binding(grant, missing)


def test_binding_is_type_strict_and_requires_gate():
    envelope = gated_envelope()
    grant = envelope["grant"]
    with pytest.raises(GrantError):
        check_grant_binding(
            {**grant, "capability_version": True}, {**envelope, "capability_version": 1}
        )
    with pytest.raises(GrantError):
        check_grant_binding(grant, {**envelope, "class": "confirm"})
    with pytest.raises(GrantError):
        check_grant_binding({**grant, "class": "confirm"}, envelope)
    with pytest.raises(GrantError):
        check_grant_binding({**grant, "v": 1}, envelope)


# --- the committed vectors ---


@pytest.mark.parametrize("name", sorted(vectors_v2.FILES))
def test_the_committed_v2_vectors_are_byte_stable(name):
    committed = (vectors_v2.VECTORS_DIR / name).read_text(encoding="utf-8")
    assert committed == vectors_v2.render(vectors_v2.FILES[name]()), (
        f"{name} is stale: run `uv run python -m nodes.tests.vectors_v2 --write`"
    )


def test_the_signing_v2_vector_verifies_and_its_negatives_do_not():
    doc = json.loads((vectors_v2.VECTORS_DIR / "signing_v2.json").read_text("utf-8"))
    pin = {doc["key_id"]: PINNED.public_key_bytes}
    for key in ("read_envelope", "confirm_envelope"):
        envelope = doc[key]
        validate(envelope, "server_to_node", 2)
        verify_envelope(envelope, pin, version=2)
        body = {k: v for k, v in envelope.items() if k != "sig"}
        assert doc[f"{key}_signed_message"].encode() == INVOKE_DOMAIN_V2 + canonicalize(body)
    for case in doc["must_not_verify"]:
        with pytest.raises(SignatureError):
            verify_envelope(case["envelope"], pin, version=2)


def test_the_grants_vector_verifies():
    doc = json.loads((vectors_v2.VECTORS_DIR / "grants.json").read_text("utf-8"))
    envelope = doc["gated_envelope"]
    pin = {doc["key_id"]: PINNED.public_key_bytes}
    verify_envelope(envelope, pin, version=2)
    verify_grant_signature(envelope["grant"], pin)
    check_grant_binding(envelope["grant"], envelope)
    grant_body = {k: v for k, v in envelope["grant"].items() if k != "sig"}
    assert doc["grant_signed_message"].encode() == GRANT_DOMAIN_V2 + canonicalize(grant_body)
    assert doc["unsigned_grant"] == _strip(envelope["grant"])
    assert doc["unpinned_key_id"] == UNPINNED.key_id != doc["key_id"]
    assert doc["shared_fields"] == list(SHARED_FIELDS)


def test_the_vector_keys_are_refused_as_server_keys():
    from nodes.keys import published_test_key_ids

    published_test_key_ids.cache_clear()
    assert {PINNED.key_id, UNPINNED.key_id} <= published_test_key_ids()


def test_build_gated_invoke_takes_the_nonce_it_is_given_or_a_fresh_one():
    a = build_gated_invoke(
        signer=PINNED,
        approval_id=APPROVAL_ID,
        node_id=NODE_ID,
        workspace_id=WORKSPACE_ID,
        capability="repo.command.run",
        capability_version=1,
        args={"repo_path": "~/w/a", "command_kind": "lint"},
        trace_id="t",
        issued_at=1_800_000_000,
        ttl_seconds=90,
    )
    b = gated_envelope(nonce=None)
    assert a["nonce"] != b["nonce"] and a["grant"]["nonce"] == a["nonce"]
    assert a["grant"]["grant_id"] != b["grant"]["grant_id"]
