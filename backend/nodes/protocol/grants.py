"""The invocation grant of a gate-class invoke (Phase 10 brief D2; ADR-013
decision 4).

Shape (`schemas/v2/grant.schema.json`, embedded verbatim as
`invoke.grant`):

    {v: 2, grant_id (uuid4), approval_id (uuid), node_id, workspace_id,
     capability, capability_version, class: "gate", args_sha256, nonce,
     issued_at, expires_at, key_id, sig}

- **Signature.** Ed25519 with the same server key as the envelope
  (`NODE_SIGNING_KEY`, pinned at pairing), over
  `b"friday-node/grant/v2\\n" + canonical(grant without sig)`. Domain
  separation, not a second key, keeps grants and envelopes apart: a grant
  signature never verifies as an envelope and the reverse.
- **Binding.** Every shared field (`SHARED_FIELDS`) must be equal between
  grant and envelope, so one nonce, one cache entry and one expiry cover
  both. Equality is type-strict (`1` never equals `True`).
- **Lifetime.** `expires_at - issued_at <= MAX_GRANT_TTL_SECONDS` (120 s);
  `build_grant` refuses a longer one, and the node refuses it again.

This module builds, signs and checks grants. Whether a grant may be issued
at all (an approved, fresh, unused `node_action` approval by the node's
owner, D2) is the server's `nodes.grants.issue` (Phase 10 branch 3); the
order the node runs these checks in is `nodes.protocol.verify` (D5).
Pure Python, like the rest of `nodes.protocol`.
"""

import uuid
from collections.abc import Mapping

from nodes.protocol.messages import MAX_GRANT_TTL_SECONDS, validate_sub_schema
from nodes.protocol.signing import (
    GRANT_DOMAIN_V2,
    KeyRing,
    SignatureError,
    SigningKey,
    build_invoke,
    new_nonce,
    sign_envelope,
    sign_with_domain,
    verify_with_domain,
)

GRANT_VERSION = 2
SHARED_FIELDS = (
    "node_id",
    "workspace_id",
    "capability",
    "capability_version",
    "args_sha256",
    "nonce",
    "issued_at",
    "expires_at",
)


class GrantError(ValueError):
    """A grant failed a build-time or node-side check. `code` is a D5
    refusal code (`ttl_too_long`, `grant_mismatch`, `bad_grant_signature`,
    `unknown_key`). The message never carries grant content."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _signing_key(signer) -> SigningKey:
    if isinstance(signer, KeyRing):
        return signer.active  # only the active key ever signs
    if isinstance(signer, SigningKey):
        return signer
    raise TypeError("signer must be a SigningKey or a KeyRing")


def _ttl(issued_at, expires_at) -> int:
    for value in (issued_at, expires_at):
        if isinstance(value, bool) or not isinstance(value, int):
            raise GrantError("ttl_too_long", "times are integer Unix seconds")
    return expires_at - issued_at


def check_grant_ttl(grant: Mapping, max_ttl=MAX_GRANT_TTL_SECONDS) -> None:
    """D5 check 18: `0 < expires_at - issued_at <= 120`."""
    ttl = _ttl(grant.get("issued_at"), grant.get("expires_at"))
    if ttl <= 0 or ttl > min(max_ttl, MAX_GRANT_TTL_SECONDS):
        raise GrantError("ttl_too_long")


def build_grant(
    *,
    approval_id,
    node_id,
    workspace_id,
    capability,
    capability_version,
    args_sha256,
    nonce,
    issued_at,
    expires_at,
    grant_id=None,
) -> dict:
    """An unsigned grant. Raises GrantError("ttl_too_long") for a lifetime
    above `MAX_GRANT_TTL_SECONDS` (or not positive): a long grant is never
    even built."""
    grant = {
        "v": GRANT_VERSION,
        "grant_id": str(grant_id or uuid.uuid4()),
        "approval_id": str(approval_id),
        "node_id": str(node_id),
        "workspace_id": str(workspace_id),
        "capability": capability,
        "capability_version": capability_version,
        "class": "gate",
        "args_sha256": args_sha256,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    check_grant_ttl(grant)
    return grant


def sign_grant(grant: Mapping, signer) -> dict:
    """`grant` with `key_id` and `sig` in the grant/v2 domain. The result
    is checked against `grant.schema.json` so a malformed grant is never
    signed."""
    signed = sign_with_domain(grant, _signing_key(signer), GRANT_DOMAIN_V2)
    validate_sub_schema("grant", signed, 2)
    return signed


def verify_grant_signature(grant: Mapping, pinned: Mapping[str, bytes]) -> None:
    """D5 checks 15-16: the grant's `key_id` is pinned (`unknown_key`) and
    its signature verifies in the grant domain (`bad_grant_signature`)."""
    if not isinstance(grant, Mapping):
        raise GrantError("bad_grant_signature")
    try:
        verify_with_domain(
            grant,
            pinned,
            GRANT_DOMAIN_V2,
            unknown_key_code="unknown_key",
            bad_signature_code="bad_grant_signature",
        )
    except SignatureError as exc:
        raise GrantError(exc.code) from exc


def _same(left, right) -> bool:
    return type(left) is type(right) and left == right


def check_grant_binding(grant: Mapping, envelope: Mapping) -> None:
    """D5 check 17: every shared field equal between grant and envelope,
    the grant's class `gate` and its version 2."""
    if not _same(grant.get("v"), GRANT_VERSION) or grant.get("class") != "gate":
        raise GrantError("grant_mismatch")
    if envelope.get("class") != "gate":
        raise GrantError("grant_mismatch")
    for name in SHARED_FIELDS:
        if name not in grant or name not in envelope or not _same(grant[name], envelope[name]):
            raise GrantError("grant_mismatch")


def build_gated_invoke(
    *,
    signer,
    approval_id,
    node_id,
    workspace_id,
    capability,
    capability_version,
    args,
    trace_id,
    issued_at,
    ttl_seconds,
    invocation_id=None,
    message_id=None,
    nonce=None,
    grant_id=None,
) -> dict:
    """A signed v2 gate-class `invoke` carrying its signed grant. The
    grant takes its shared fields from the envelope (one nonce, one
    expiry), is signed in the grant domain, embedded, and then the envelope
    is signed in the invoke/v2 domain. Raises GrantError("ttl_too_long")
    above `MAX_GRANT_TTL_SECONDS`."""
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise GrantError("ttl_too_long", "ttl_seconds must be an integer")
    if not 0 < ttl_seconds <= MAX_GRANT_TTL_SECONDS:
        raise GrantError("ttl_too_long")
    key = _signing_key(signer)
    envelope = build_invoke(
        node_id=node_id,
        workspace_id=workspace_id,
        capability=capability,
        capability_version=capability_version,
        args=args,
        trace_id=trace_id,
        issued_at=issued_at,
        ttl_seconds=ttl_seconds,
        capability_class="gate",
        invocation_id=invocation_id,
        message_id=message_id,
        nonce=nonce or new_nonce(),
        version=2,
    )
    grant = build_grant(
        approval_id=approval_id,
        grant_id=grant_id,
        **{name: envelope[name] for name in SHARED_FIELDS},
    )
    envelope["grant"] = sign_grant(grant, key)
    return sign_envelope(envelope, key)
