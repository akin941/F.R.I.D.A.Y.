"""Builds the protocol v2 shared vectors (Phase 10 brief D1, D2):
`nodes/protocol/vectors/signing_v2.json` and `grants.json`.

The files are committed and loaded by the Swift test target (Mac session
M1); `test_protocol_v2_vectors.py` regenerates them and asserts they are
byte-identical (Ed25519 is deterministic), so they cannot drift from the
code. To rewrite them after an intentional change:

    uv run python -m nodes.tests.vectors_v2 --write

TEST VECTORS ONLY: both private keys are public. The pinned one is the v1
vector key (every server refuses it as NODE_SIGNING_KEY); the unpinned one
is listed in `grants.json` as `unpinned_key_id` and refused the same way.
"""

import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nodes.capabilities import load_action_contracts, load_contracts
from nodes.protocol.canonical import canonicalize
from nodes.protocol.grants import SHARED_FIELDS, build_gated_invoke, build_grant
from nodes.protocol.signing import (
    ENVELOPE_DOMAINS,
    GRANT_DOMAIN_V2,
    INVOKE_DOMAIN_V2,
    SigningKey,
    b64url_decode,
    b64url_encode,
    build_invoke,
    sign_envelope,
    sign_with_domain,
)

VECTORS_DIR = Path(__file__).resolve().parents[1] / "protocol" / "vectors"
V1_VECTOR = json.loads((VECTORS_DIR / "signing.json").read_text(encoding="utf-8"))

NODE_ID = "7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f70"
WORKSPACE_ID = "0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b"
APPROVAL_ID = "3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b0a"
GRANT_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
MESSAGE_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
INVOCATION_ID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
NONCE = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
OTHER_NONCE = "HxwdHBsaGRgXFhUUExIREA8ODQwLCgkIBwYFBAMCAQA"
ISSUED_AT = 1_795_000_000
NOW = ISSUED_AT + 5
GATE_TTL = 90
UNPINNED_PRIVATE = bytes(range(32, 64))

PINNED = SigningKey.from_b64(V1_VECTOR["private_key_b64"])
UNPINNED = SigningKey(Ed25519PrivateKey.from_private_bytes(UNPINNED_PRIVATE))

GATE_ARGS = {"repo_path": "~/work/acme", "command_kind": "test"}


def _message(domain: bytes, body) -> str:
    return (domain + canonicalize(body)).decode("utf-8")


def _strip(signed):
    return {k: v for k, v in signed.items() if k not in ("sig", "key_id")}


def _resign(envelope, key=PINNED):
    """A compromised server re-signs whatever it changed: the envelope
    signature is valid, so the node's grant checks must catch it."""
    return sign_envelope(_strip(envelope), key)


def gated_envelope(**overrides):
    fields = {
        "signer": PINNED,
        "approval_id": APPROVAL_ID,
        "node_id": NODE_ID,
        "workspace_id": WORKSPACE_ID,
        "capability": "repo.command.run",
        "capability_version": 1,
        "args": dict(GATE_ARGS),
        "trace_id": "trace-0002",
        "issued_at": ISSUED_AT,
        "ttl_seconds": GATE_TTL,
        "invocation_id": INVOCATION_ID,
        "message_id": MESSAGE_ID,
        "nonce": NONCE,
        "grant_id": GRANT_ID,
    }
    fields.update(overrides)
    return build_gated_invoke(**fields)


def plain_envelope(capability, args, capability_class, ttl=30, version=2):
    contract = {**load_contracts(), **load_action_contracts()}[capability]
    envelope = build_invoke(
        node_id=NODE_ID,
        workspace_id=WORKSPACE_ID,
        capability=capability,
        capability_version=contract.version,
        args=args,
        trace_id="trace-0001",
        issued_at=ISSUED_AT,
        ttl_seconds=ttl,
        capability_class=capability_class,
        invocation_id=INVOCATION_ID,
        message_id=MESSAGE_ID,
        nonce=NONCE,
        version=version,
    )
    return sign_envelope(envelope, PINNED)


def _flip(sig: str) -> str:
    raw = bytearray(b64url_decode(sig))
    raw[0] ^= 0x01
    return b64url_encode(bytes(raw))


def _with_grant(envelope, grant):
    return _resign({**envelope, "grant": grant})


def _other_value(name, value):
    if name in ("issued_at", "expires_at", "capability_version"):
        return value + 1
    if name == "args_sha256":
        return "0" * 64
    if name == "nonce":
        return OTHER_NONCE
    if name == "capability":
        return "ax.press"
    if name == "node_id":
        return "7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f71"
    if name == "workspace_id":
        return "0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5c"
    raise KeyError(name)


def refusal_cases():
    """(name, envelope, expected code, expected check) for D5 v2 checks
    13-20, each a frame a compromised server could sign. The node state is
    `node_state()`: the server's own v2 contracts as the node's catalogue,
    all granted, the pinned key only, at `NOW`."""
    base = gated_envelope()
    grant = base["grant"]
    cases = []

    confirm_as_gate = gated_envelope(
        capability="apps.open", args={"bundle_id": "com.apple.Terminal"}
    )
    cases.append(("gate_envelope_for_a_confirm_entry", confirm_as_gate, "class_mismatch", 13))
    gate_as_confirm = _resign({**base, "class": "confirm", "grant": None})
    cases.append(("confirm_envelope_for_a_gate_entry", gate_as_confirm, "class_mismatch", 13))

    cases.append(("gate_without_grant", _resign({**base, "grant": None}), "grant_missing", 14))
    confirm = plain_envelope("apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm", ttl=90)
    cases.append(("confirm_with_grant", _with_grant(confirm, grant), "grant_unexpected", 14))
    read = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read")
    cases.append(("read_with_grant", _with_grant(read, grant), "grant_unexpected", 14))

    unpinned = sign_with_domain(_strip(grant), UNPINNED, GRANT_DOMAIN_V2)
    cases.append(("grant_signed_by_unpinned_key", _with_grant(base, unpinned), "unknown_key", 15))

    flipped = {**grant, "sig": _flip(grant["sig"])}
    cases.append(
        ("grant_with_flipped_signature", _with_grant(base, flipped), "bad_grant_signature", 16)
    )
    envelope_domain = sign_with_domain(_strip(grant), PINNED, INVOKE_DOMAIN_V2)
    cases.append(
        (
            "grant_signed_in_the_envelope_domain",
            _with_grant(base, envelope_domain),
            "bad_grant_signature",
            16,
        )
    )
    v1_style = sign_with_domain(_strip(grant), PINNED, ENVELOPE_DOMAINS[1])
    cases.append(
        ("grant_signed_without_domain", _with_grant(base, v1_style), "bad_grant_signature", 16)
    )
    other_approval = {**_strip(grant), "approval_id": "3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b0b"}
    cases.append(
        (
            "grant_field_changed_after_signing",
            _with_grant(base, {**grant, **other_approval}),
            "bad_grant_signature",
            16,
        )
    )

    for name in SHARED_FIELDS:
        changed = {**base, name: _other_value(name, base[name])}
        if name == "capability":
            changed["args"] = {"bundle_id": "com.apple.Terminal", "role": "button", "title": "OK"}
        code, check = "grant_mismatch", 17
        if name == "node_id":
            code, check = "wrong_node", 7  # the node id check (7) comes first
        if name == "capability_version":
            code, check = "unknown_capability", 12  # no catalogue entry at v2
        cases.append((f"envelope_{name}_differs_from_grant", _resign(changed), code, check))
    for name in SHARED_FIELDS:
        rebound = sign_with_domain(
            {**_strip(grant), name: _other_value(name, grant[name])}, PINNED, GRANT_DOMAIN_V2
        )
        cases.append(
            (
                f"grant_{name}_differs_from_envelope",
                _with_grant(base, rebound),
                "grant_mismatch",
                17,
            )
        )

    long_ttl = _resign({**base, "expires_at": base["issued_at"] + 121})
    cases.append(("gated_envelope_ttl_121", long_ttl, "ttl_too_long", 10))

    other_args = {"repo_path": "~/work/other", "command_kind": "test"}
    cases.append(
        ("args_changed_hash_kept", _resign({**base, "args": other_args}), "args_hash_mismatch", 19)
    )
    outside = gated_envelope(args={"repo_path": "~/work/acme", "command_kind": "deploy"})
    cases.append(("args_outside_the_node_schema", outside, "args_schema", 20))
    return cases


def node_state(*, nonce_cache=None, availability="granted"):
    """The node state the refusal vectors assume (a fresh nonce cache)."""
    from nodes.capabilities import get_action_contract
    from nodes.protocol.verify import NodeState, NonceCache

    contracts = {**load_contracts(), **load_action_contracts()}
    recipe = get_action_contract("applescript.recipe.run")
    return NodeState(
        node_id=NODE_ID,
        pinned={PINNED.key_id: PINNED.public_key_bytes},
        catalogue={name: c.catalogue_entry(availability) for name, c in contracts.items()},
        nonce_cache=NonceCache() if nonce_cache is None else nonce_cache,
        extra_args_checks={
            recipe.name: lambda args: recipe.validate_args(args, code="args_schema")
        },
    )


def node_state_document():
    return {
        "node_id": NODE_ID,
        "now": NOW,
        "pinned_key_ids": [PINNED.key_id],
        "catalogue": "the server's own contracts (nodes/capabilities/v1 and v2), all granted",
    }


def signing_v2_document():
    read = plain_envelope(
        "claude.sessions.list",
        {"since_hours": 24, "limit": 10, "cwd_prefixes": ["~/work/acme"]},
        "read",
    )
    confirm = plain_envelope("apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm", ttl=90)
    read_body = {k: v for k, v in read.items() if k != "sig"}
    no_domain = b64url_encode(PINNED.sign(canonicalize(read_body)))
    grant_domain = b64url_encode(PINNED.sign(GRANT_DOMAIN_V2 + canonicalize(read_body)))
    v1_envelope = V1_VECTOR["envelope"]
    return {
        "description": (
            "Friday Node protocol v2 signing vectors (Phase 10 brief D1). TEST VECTOR ONLY: "
            "the private key is the published v1 vector key. A v2 envelope's sig is "
            "base64url-no-pad(Ed25519(invoke_domain + canonical JSON of the envelope without "
            "sig)); the domain bytes are the UTF-8 of invoke_domain, ending in one newline. "
            "`must_not_verify` signatures are over the same body in another domain."
        ),
        "private_key_b64": V1_VECTOR["private_key_b64"],
        "public_key_b64": V1_VECTOR["public_key_b64"],
        "key_id": PINNED.key_id,
        "invoke_domain": INVOKE_DOMAIN_V2.decode("ascii"),
        "grant_domain": GRANT_DOMAIN_V2.decode("ascii"),
        "read_envelope": read,
        "read_envelope_signed_message": _message(INVOKE_DOMAIN_V2, read_body),
        "confirm_envelope": confirm,
        "confirm_envelope_signed_message": _message(
            INVOKE_DOMAIN_V2, {k: v for k, v in confirm.items() if k != "sig"}
        ),
        "must_not_verify": [
            {
                "name": "v2 read envelope signed without a domain (v1 style)",
                "envelope": {**read, "sig": no_domain},
            },
            {
                "name": "v2 read envelope signed in the grant domain",
                "envelope": {**read, "sig": grant_domain},
            },
            {
                "name": "the v1 vector envelope relabelled v: 2",
                "envelope": {**v1_envelope, "v": 2},
            },
        ],
    }


def grants_document():
    envelope = gated_envelope()
    grant = envelope["grant"]
    return {
        "description": (
            "Friday Node protocol v2 grant vectors (Phase 10 brief D2, D5 checks 13-20). TEST "
            "VECTORS ONLY: both private keys are public; unpinned_key_id is never pinned. "
            "grant.sig = base64url-no-pad(Ed25519(grant_domain + canonical JSON of the grant "
            "without sig)); the envelope is then signed in the invoke/v2 domain with the grant "
            "embedded. Each refusal is a frame a compromised server could sign; a conforming "
            "node refuses it with `code` at `check` given `node_state` (checks 1-12 pass)."
        ),
        "private_key_b64": V1_VECTOR["private_key_b64"],
        "public_key_b64": V1_VECTOR["public_key_b64"],
        "key_id": PINNED.key_id,
        "unpinned_private_key_b64": base64.b64encode(UNPINNED_PRIVATE).decode("ascii"),
        "unpinned_key_id": UNPINNED.key_id,
        "grant_domain": GRANT_DOMAIN_V2.decode("ascii"),
        "invoke_domain": INVOKE_DOMAIN_V2.decode("ascii"),
        "shared_fields": list(SHARED_FIELDS),
        "max_grant_ttl_seconds": 120,
        "gated_envelope": envelope,
        "grant_signed_message": _message(
            GRANT_DOMAIN_V2, {k: v for k, v in grant.items() if k != "sig"}
        ),
        "envelope_signed_message": _message(
            INVOKE_DOMAIN_V2, {k: v for k, v in envelope.items() if k != "sig"}
        ),
        "unsigned_grant": build_grant(
            approval_id=APPROVAL_ID,
            grant_id=GRANT_ID,
            **{name: envelope[name] for name in SHARED_FIELDS},
        ),
        "node_state": node_state_document(),
        "refusals": [
            {"name": name, "envelope": frame, "code": code, "check": check}
            for name, frame, code, check in refusal_cases()
        ],
    }


def render(document) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


FILES = {"signing_v2.json": signing_v2_document, "grants.json": grants_document}


def main(argv):
    if argv[1:] != ["--write"]:
        print(__doc__)
        return 2
    for name, build in FILES.items():
        (VECTORS_DIR / name).write_text(render(build()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
