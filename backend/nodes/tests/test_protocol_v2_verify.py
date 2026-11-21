"""The protocol-level node verification order (Phase 10 brief D5 checks
1-21; Phase 9 D5 for v1). Branch 1's "done when": a v2 gated envelope with
a grant built by `nodes.protocol` verifies with the pinned key and fails
each D5 v2 check 13-20 when its field is tampered with -- including by a
compromised server that re-signs the tampered envelope."""

import json

import pytest

from nodes.protocol.messages import NODE_CHECKS_V2
from nodes.protocol.signing import sign_envelope
from nodes.protocol.verify import NonceCache, Refusal, verify_invoke
from nodes.tests import vectors_v2
from nodes.tests.vectors_v2 import (
    NOW,
    PINNED,
    gated_envelope,
    node_state,
    plain_envelope,
    refusal_cases,
)


def _resign(envelope):
    return sign_envelope({k: v for k, v in envelope.items() if k not in ("sig", "key_id")}, PINNED)


def _refused(frame, *, version=2, state=None, now=NOW):
    with pytest.raises(Refusal) as exc:
        verify_invoke(frame, version=version, state=state or node_state(), now=now)
    return exc.value.code, exc.value.check


# --- the done-when ---


def test_a_gated_envelope_verifies_with_the_pinned_key():
    envelope = gated_envelope()
    assert verify_invoke(envelope, version=2, state=node_state(), now=NOW) == envelope
    raw = json.dumps(envelope)
    assert verify_invoke(raw, version=2, state=node_state(), now=NOW) == envelope


@pytest.mark.parametrize(
    ("name", "frame", "code", "check"),
    refusal_cases(),
    ids=[case[0] for case in refusal_cases()],
)
def test_each_tampered_field_is_refused_at_its_check(name, frame, code, check):
    assert _refused(frame) == (code, check)


def test_the_refusal_vectors_cover_checks_13_to_20():
    checks = {check for _, _, _, check in refusal_cases()}
    assert {13, 14, 15, 16, 17, 19, 20} <= checks
    # Check 18 (grant TTL) is unreachable once 10 and 17 pass (equal times,
    # envelope TTL <= 120); a long grant is caught at 10 with the same code.
    assert ("gated_envelope_ttl_121", "ttl_too_long", 10) in {
        (name, code, check) for name, _, code, check in refusal_cases()
    }
    codes = {code for _, codes in NODE_CHECKS_V2 if 13 <= _ <= 20 for code in codes}
    assert codes <= {code for _, _, code, _ in refusal_cases()}


def test_the_committed_refusal_vectors_match_the_verifier():
    doc = json.loads((vectors_v2.VECTORS_DIR / "grants.json").read_text("utf-8"))
    assert doc["node_state"]["now"] == NOW
    assert len(doc["refusals"]) == len(refusal_cases())
    for case in doc["refusals"]:
        assert _refused(case["envelope"]) == (case["code"], case["check"]), case["name"]


# --- the other protocol checks, v2 ---


def test_frame_errors_are_checks_1_to_4():
    assert _refused(b"x" * (256 * 1024 + 1)) == ("too_large", 1)
    assert _refused("{nope") == ("malformed", 2)
    assert _refused({"v": 2, "type": "policy.set", "id": "x"}) == ("unknown_type", 3)
    assert _refused({**gated_envelope(), "extra": 1}) == ("schema", 4)
    # A v1 envelope on a v2 session is a schema error, not a downgrade.
    v1 = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read", version=1)
    assert _refused(v1) == ("schema", 4)
    with pytest.raises(Refusal) as exc:
        verify_invoke("{nope", version=2, state=node_state(), now=NOW)
    assert exc.value.frame_error


def test_signature_node_time_and_nonce_checks():
    envelope = gated_envelope()
    assert _refused(gated_envelope(signer=vectors_v2.UNPINNED)) == ("unknown_key", 5)
    flipped = {**envelope, "args": {"repo_path": "~/work/x", "command_kind": "test"}}
    assert _refused(flipped) == ("bad_signature", 6)
    assert _refused(envelope, now=envelope["issued_at"] - 6) == ("not_yet_valid", 8)
    assert _refused(envelope, now=envelope["expires_at"]) == ("expired", 9)
    confirm = plain_envelope("apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm", ttl=121)
    assert _refused(confirm) == ("ttl_too_long", 10)
    read = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read", ttl=31)
    assert _refused(read) == ("ttl_too_long", 10)
    state = node_state()
    verify_invoke(envelope, version=2, state=state, now=NOW)
    assert _refused(envelope, state=state) == ("replay", 11)
    # The same grant on a new envelope (new id, same nonce) is a replay too.
    fresh = _resign({**envelope, "id": "2b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d"})
    assert _refused(fresh, state=state) == ("replay", 11)
    full = node_state(nonce_cache=NonceCache(capacity=0))
    assert _refused(gated_envelope(), state=full) == ("nonce_cache_full", 11)
    unknown = gated_envelope(capability="shell.exec")
    assert _refused(unknown) == ("unknown_capability", 12)


def test_a_nonce_is_released_after_expiry_plus_skew():
    cache = NonceCache(capacity=1)
    cache.check_and_add("n", 100, 50)
    assert "n" in cache and len(cache) == 1
    with pytest.raises(Refusal):
        cache.check_and_add("m", 100, 104)  # still full until 105
    cache.check_and_add("m", 200, 105)
    assert "n" not in cache


def test_not_granted_is_check_21():
    state = node_state(availability="not_granted")
    assert _refused(gated_envelope(), state=state) == ("not_granted", 21)
    confirm = plain_envelope("apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm", ttl=90)
    assert _refused(confirm, state=node_state(availability="not_granted")) == ("not_granted", 21)


def test_confirm_and_read_invokes_verify_without_a_grant():
    confirm = plain_envelope("apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm", ttl=90)
    verify_invoke(confirm, version=2, state=node_state(), now=NOW)
    read = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read")
    verify_invoke(read, version=2, state=node_state(), now=NOW)


def test_recipe_params_are_checked_against_the_recipe_schema_at_check_20():
    ok = gated_envelope(
        capability="applescript.recipe.run",
        args={"recipe": "system.volume_set", "params": {"level": 40}},
    )
    verify_invoke(ok, version=2, state=node_state(), now=NOW)
    for params in ({"level": 101}, {"level": 40, "extra": 1}, {}):
        bad = gated_envelope(
            capability="applescript.recipe.run",
            args={"recipe": "system.volume_set", "params": params},
        )
        assert _refused(bad) == ("args_schema", 20), params
    other = gated_envelope(
        capability="applescript.recipe.run",
        args={"recipe": "music.play_pause", "params": {"level": 1}},
    )
    assert _refused(other) == ("args_schema", 20)


def test_a_non_invoke_frame_passes_through():
    bye = {"v": 2, "type": "bye", "id": "2b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d", "reason": "revoked"}
    assert verify_invoke(bye, version=2, state=node_state(), now=NOW) == bye


def test_the_first_failing_check_wins():
    # Wrong node AND no grant: check 7 wins over check 14.
    envelope = _resign(
        {**gated_envelope(), "grant": None, "node_id": "7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f71"}
    )
    assert _refused(envelope) == ("wrong_node", 7)


# --- v1 sessions keep the Phase 9 order ---


def test_a_v1_session_refuses_every_non_read_class_at_check_13():
    v1_read = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read", version=1)
    verify_invoke(v1_read, version=1, state=node_state(), now=NOW)
    v1_confirm = plain_envelope(
        "apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm", version=1
    )
    assert _refused(v1_confirm, version=1) == ("class_not_allowed", 13)
    # A v2 grant-carrying envelope on a v1 session is a schema error.
    assert _refused(gated_envelope(), version=1) == ("schema", 4)


def test_v1_args_and_availability_checks():
    bad_hash = _resign_v1(
        plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read", version=1),
        args={"repo_path": "~/work/other"},
    )
    assert _refused(bad_hash, version=1) == ("args_hash_mismatch", 14)
    outside = plain_envelope("git.status", {"repo_path": "relative"}, "read", version=1)
    assert _refused(outside, version=1) == ("args_schema", 15)
    granted = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read", version=1)
    assert _refused(granted, version=1, state=node_state(availability="not_granted")) == (
        "not_granted",
        16,
    )
    long = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read", ttl=31, version=1)
    assert _refused(long, version=1) == ("ttl_too_long", 10)


def _resign_v1(envelope, **changes):
    return sign_envelope(
        {k: v for k, v in {**envelope, **changes}.items() if k not in ("sig", "key_id")}, PINNED
    )
