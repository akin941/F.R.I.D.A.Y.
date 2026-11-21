"""Friday Node protocol v1 (Phase 9 brief D5, ADR-013).

Pure Python: this package imports neither Django nor any project package
outside `nodes.protocol` (a subprocess test and an import-linter contract
enforce it). Together with the schema files and test vectors next to it, it
is the shared spec the Swift node app (`node/friday-node`, Mac session M1)
mirrors.

- `canonical`: the RFC 8785 (JCS) subset our schemas allow, and strict JSON
  parsing.
- `signing`: Ed25519 keys, key ids, signed `invoke` envelopes.
- `messages`: the frozen message-type registry and `validate`.
"""

from nodes.protocol.canonical import (
    CanonicalizationError,
    canonicalize,
    parse_strict,
    sha256_hex,
)
from nodes.protocol.messages import (
    MESSAGE_TYPES,
    NODE_REFUSAL_CODES,
    NODE_TO_SERVER,
    PROTOCOL_VERSION,
    SERVER_TO_NODE,
    ProtocolError,
    catalogue_sha256,
    validate,
)
from nodes.protocol.signing import (
    KeyRing,
    SignatureError,
    SigningKey,
    build_invoke,
    key_id_for,
    sign_envelope,
    verify_envelope,
)

__all__ = [
    "MESSAGE_TYPES",
    "NODE_REFUSAL_CODES",
    "NODE_TO_SERVER",
    "PROTOCOL_VERSION",
    "SERVER_TO_NODE",
    "CanonicalizationError",
    "KeyRing",
    "ProtocolError",
    "SignatureError",
    "SigningKey",
    "build_invoke",
    "canonicalize",
    "catalogue_sha256",
    "key_id_for",
    "parse_strict",
    "sha256_hex",
    "sign_envelope",
    "validate",
    "verify_envelope",
]
