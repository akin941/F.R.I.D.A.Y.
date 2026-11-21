"""The server's node signing key configuration (Phase 9 brief D4).

- `NODE_SIGNING_KEY`: an Ed25519 private key, base64 of its raw 32 bytes,
  from the environment only (`deploy/docker/gen-secrets.sh` generates one).
- The key id is derived: the first 16 hex characters of sha256 of the
  public key (`nodes.protocol.signing.key_id_for`).
- `NODE_SIGNING_KEYS_RETIRED`: base64 raw public keys that still verify
  but never sign (rollover = re-pairing in Phase 9, Q3).

A missing, malformed or published test key makes `signing_keyring()` raise
`SigningKeyUnavailable`: pairing and invokes refuse (503 /
`server_refused:signing_unavailable`) and preflight fails (closeout); no
other surface is affected. Error messages never carry key material.
"""

import json
from functools import cache
from pathlib import Path

from django.conf import settings

from nodes.protocol.signing import KeyRing, SignatureError, SigningKey, load_public_key_b64

_VECTORS_DIR = Path(__file__).resolve().parent / "protocol" / "vectors"


class SigningKeyUnavailable(Exception):
    """No usable server signing key. `reason`: `missing`, `invalid`,
    `test_key` (a published test key, never accepted in production) or
    `retired_invalid` (a malformed NODE_SIGNING_KEYS_RETIRED entry)."""

    def __init__(self, reason):
        super().__init__(f"node signing key unavailable: {reason}")
        self.reason = reason


@cache
def published_test_key_ids() -> frozenset:
    """Key ids of keys whose private half is public (the protocol signing
    vectors; branch 3 adds the conformance key pair). A server refuses to
    sign with any of them."""
    ids = set()
    signing_vectors = _VECTORS_DIR / "signing.json"
    if signing_vectors.exists():
        ids.add(json.loads(signing_vectors.read_text(encoding="utf-8"))["key_id"])
    return frozenset(ids)


def _retired_public_keys():
    raw = getattr(settings, "NODE_SIGNING_KEYS_RETIRED", None) or []
    if isinstance(raw, str):
        raw = [part for part in raw.split(",")]
    keys = []
    for entry in raw:
        entry = entry.strip()
        if not entry:
            continue
        try:
            keys.append(load_public_key_b64(entry))
        except SignatureError as exc:
            raise SigningKeyUnavailable("retired_invalid") from exc
    return keys


def signing_keyring() -> KeyRing:
    """The configured KeyRing, or SigningKeyUnavailable."""
    value = (getattr(settings, "NODE_SIGNING_KEY", "") or "").strip()
    if not value:
        raise SigningKeyUnavailable("missing")
    try:
        active = SigningKey.from_b64(value)
    except SignatureError as exc:
        raise SigningKeyUnavailable("invalid") from exc
    if active.key_id in published_test_key_ids():
        raise SigningKeyUnavailable("test_key")
    return KeyRing(active, _retired_public_keys())


def signing_key_id() -> str | None:
    """The configured key id, or None when no usable key is configured
    (for `/healthz` and preflight: the id only, never key material)."""
    try:
        return signing_keyring().key_id
    except SigningKeyUnavailable:
        return None
