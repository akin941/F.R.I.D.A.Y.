"""Ed25519 keys and signed `invoke` envelopes (brief D4, D5; ADR-013
decision 4).

- A server signing key is an Ed25519 private key given as base64 of its raw
  32 bytes (`NODE_SIGNING_KEY`). Its key id is the first 16 hex characters
  of sha256 of the raw 32-byte public key.
- `sig` is Ed25519 over the canonical JSON of the envelope **without**
  `sig`; `key_id` is part of the signed body. Signatures and nonces are
  base64url without padding.
- A `KeyRing` holds at most one active key, which signs, and any number of
  retired public keys, which verify but can never sign (they are public
  keys only; rollover means re-pairing in Phase 9, Q3).

Node side, the verifier is `verify_envelope(envelope, pinned)` with the keys
pinned at pairing. The full D5 verification order (size, parse, type,
schema, key, signature, node, time, nonce, catalogue, class, args) is the
reference node's (branch 3); this module is its signature step.
"""

import base64
import binascii
import hashlib
import re
import secrets
import uuid
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from nodes.protocol.canonical import canonicalize, sha256_hex

ALG = "Ed25519"
KEY_BYTES = 32
SIGNATURE_BYTES = 64
NONCE_BYTES = 32
KEY_ID_HEX_CHARS = 16

_B64URL = re.compile(r"[A-Za-z0-9_-]*")
_B64STD = re.compile(r"[A-Za-z0-9+/]*={0,2}")


class SignatureError(ValueError):
    """A key or envelope failed a check. `code` is the D5 refusal code
    (`unknown_key`, `bad_signature`) or `invalid_key` for key material."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Strict base64url without padding: only the URL-safe alphabet, and
    the canonical encoding of the decoded bytes (no stray trailing bits)."""
    if not isinstance(text, str) or not _B64URL.fullmatch(text):
        raise ValueError("not base64url")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64url_encode(data) != text:
        raise ValueError("non-canonical base64url")
    return data


def _b64std_decode(text: str) -> bytes:
    if not isinstance(text, str) or not _B64STD.fullmatch(text.strip()):
        raise ValueError("not base64")
    try:
        return base64.b64decode(text.strip(), validate=True)
    except binascii.Error as exc:
        raise ValueError("not base64") from exc


def key_id_for(public_key_bytes: bytes) -> str:
    """The key id: first 16 hex chars of sha256 of the raw public key."""
    return hashlib.sha256(public_key_bytes).hexdigest()[:KEY_ID_HEX_CHARS]


def load_public_key_b64(text: str) -> bytes:
    """A raw 32-byte Ed25519 public key from standard base64."""
    try:
        raw = _b64std_decode(text)
    except ValueError as exc:
        raise SignatureError("invalid_key", "public key is not base64") from exc
    if len(raw) != KEY_BYTES:
        raise SignatureError("invalid_key", "public key must be 32 raw bytes")
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise SignatureError("invalid_key", "not an Ed25519 public key") from exc
    return raw


class SigningKey:
    """An Ed25519 private key that signs envelopes."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private = private_key
        self.public_key_bytes = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.key_id = key_id_for(self.public_key_bytes)

    @classmethod
    def generate(cls):
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_b64(cls, text: str):
        """From base64 of the raw 32-byte private key (`NODE_SIGNING_KEY`).
        Raises SignatureError("invalid_key") on anything else; the message
        never contains key material."""
        try:
            raw = _b64std_decode(text or "")
        except ValueError as exc:
            raise SignatureError("invalid_key", "signing key is not base64") from exc
        if len(raw) != KEY_BYTES:
            raise SignatureError("invalid_key", "signing key must be 32 raw bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    def private_b64(self) -> str:
        """Base64 of the raw private key (for key generation tooling only)."""
        raw = self._private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return base64.b64encode(raw).decode("ascii")

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key_bytes).decode("ascii")

    def public_entry(self) -> dict:
        """The `server_signing_keys` entry the pairing response hands out."""
        return {"key_id": self.key_id, "alg": ALG, "public_key_b64": self.public_key_b64}

    def sign(self, data: bytes) -> bytes:
        return self._private.sign(data)

    def __repr__(self):  # never print key material
        return f"<SigningKey {self.key_id}>"


def sign_envelope(envelope: Mapping, key: SigningKey) -> dict:
    """Return `envelope` with `key_id` set to `key`'s id and `sig` added.
    The input must not already carry `sig` (a signature is never over a
    body that contains one)."""
    if "sig" in envelope:
        raise SignatureError("bad_signature", "the signed body must not contain sig")
    body = {**envelope, "key_id": key.key_id}
    return {**body, "sig": b64url_encode(key.sign(canonicalize(body)))}


def verify_envelope(envelope: Mapping, pinned: Mapping[str, bytes]) -> None:
    """Check `envelope`'s signature against the pinned public keys
    (`{key_id: raw public key}`). Raises SignatureError with `unknown_key`
    (D5 check 5) or `bad_signature` (check 6)."""
    key_id = envelope.get("key_id")
    if not isinstance(key_id, str) or key_id not in pinned:
        raise SignatureError("unknown_key")
    sig = envelope.get("sig")
    try:
        signature = b64url_decode(sig)
    except ValueError as exc:
        raise SignatureError("bad_signature") from exc
    if len(signature) != SIGNATURE_BYTES:
        raise SignatureError("bad_signature")
    body = {name: value for name, value in envelope.items() if name != "sig"}
    try:
        Ed25519PublicKey.from_public_bytes(pinned[key_id]).verify(signature, canonicalize(body))
    except (InvalidSignature, ValueError) as exc:
        raise SignatureError("bad_signature") from exc


class KeyRing:
    """The server's signing configuration: one active key (signs and
    verifies) plus retired public keys (verify only)."""

    def __init__(self, active: SigningKey, retired_public_keys=()):
        self.active = active
        retired = {}
        for raw in retired_public_keys:
            retired[key_id_for(raw)] = bytes(raw)
        retired.pop(active.key_id, None)
        self._retired = retired

    @property
    def key_id(self) -> str:
        return self.active.key_id

    @property
    def retired_key_ids(self):
        return frozenset(self._retired)

    def verification_keys(self) -> dict:
        """`{key_id: raw public key}` of every key that verifies."""
        return {**self._retired, self.active.key_id: self.active.public_key_bytes}

    def sign(self, envelope: Mapping) -> dict:
        """Sign with the active key -- the only key that ever signs."""
        return sign_envelope(envelope, self.active)

    def verify(self, envelope: Mapping) -> None:
        verify_envelope(envelope, self.verification_keys())


def new_nonce() -> str:
    """32 random bytes, base64url without padding (43 characters)."""
    return b64url_encode(secrets.token_bytes(NONCE_BYTES))


def build_invoke(
    *,
    node_id,
    workspace_id,
    capability,
    capability_version,
    args,
    trace_id,
    issued_at,
    ttl_seconds,
    capability_class="read",
    invocation_id=None,
    message_id=None,
    nonce=None,
) -> dict:
    """An unsigned v1 `invoke` envelope (sign it with `KeyRing.sign` or
    `sign_envelope`). Times are integer Unix seconds; `args_sha256` is the
    sha256 of the canonical JSON of `args`; `grant` is reserved for Phase 10
    and always null in v1."""
    return {
        "v": 1,
        "type": "invoke",
        "id": str(message_id or uuid.uuid4()),
        "invocation_id": str(invocation_id or uuid.uuid4()),
        "node_id": str(node_id),
        "workspace_id": str(workspace_id),
        "capability": capability,
        "capability_version": capability_version,
        "class": capability_class,
        "args": args,
        "args_sha256": sha256_hex(args),
        "nonce": nonce or new_nonce(),
        "issued_at": int(issued_at),
        "expires_at": int(issued_at) + int(ttl_seconds),
        "trace_id": trace_id,
        "grant": None,
    }
