"""The node-side verification order, protocol-level part (Phase 9 brief D5
for v1, Phase 10 brief D5 for v2).

`verify_invoke(frame, version=..., state=..., now=...)` runs, in order,
every check that needs only the frame and node state the protocol defines
(pinned keys, node id, the node's own catalogue, its nonce cache):

- v1: checks 1-16 (`NODE_REFUSAL_CODES`), ending with availability;
- v2: checks 1-21 (`NODE_CHECKS_V2`): 1-12 as v1, then class (13), grant
  presence (14), grant key (15), grant signature (16), grant binding (17),
  grant TTL (18), args hash (19), the node's **own** args schema (20,
  plus any extra check the node registers, such as a recipe's params) and
  availability (21).

Checks 22-24 (local policy, the confirmation window, the post-click
re-check) need the node's local policy and its window; they live in the
reference node (Phase 10 branch 2) and the Swift app (Mac session M1),
which call this first. The first failing check wins and nothing after it
runs; a refusal never echoes the payload.

Pure Python: the reference node and the conformance suite run it without
Django.
"""

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from nodes.protocol.canonical import CanonicalizationError, sha256_hex
from nodes.protocol.grants import (
    GrantError,
    check_grant_binding,
    check_grant_ttl,
    verify_grant_signature,
)
from nodes.protocol.messages import (
    CLOCK_SKEW_SECONDS,
    MAX_INVOKE_TTL_SECONDS_V2,
    MAX_READ_INVOKE_TTL_SECONDS,
    NONCE_CACHE_SIZE,
    ProtocolError,
    decode_frame,
    validate,
    validate_instance,
)
from nodes.protocol.signing import SignatureError, verify_envelope


class Refusal(Exception):
    """One invoke refused at `check` with D5 refusal `code`."""

    def __init__(self, code, check):
        super().__init__(f"{code} (check {check})")
        self.code = code
        self.check = check

    @property
    def frame_error(self) -> bool:
        """Checks 1-4 are frame errors (an `error` result, not `refused`)."""
        return self.check <= 4


class NonceCache:
    """D5 check 11: nonces kept until `expires_at + skew`, bounded (a full
    cache refuses rather than evicting a live nonce)."""

    def __init__(self, capacity=NONCE_CACHE_SIZE, skew=CLOCK_SKEW_SECONDS):
        self.capacity = capacity
        self.skew = skew
        self._entries = OrderedDict()

    def __len__(self):
        return len(self._entries)

    def __contains__(self, nonce):
        return nonce in self._entries

    def purge(self, now):
        for nonce, keep_until in list(self._entries.items()):
            if keep_until <= now:
                del self._entries[nonce]

    def check_and_add(self, nonce, expires_at, now) -> None:
        """Raises Refusal(`replay` / `nonce_cache_full`, 11)."""
        self.purge(now)
        if nonce in self._entries:
            raise Refusal("replay", 11)
        if len(self._entries) >= self.capacity:
            raise Refusal("nonce_cache_full", 11)
        self._entries[nonce] = expires_at + self.skew


@dataclass
class NodeState:
    """What the node knows locally. `catalogue` is the node's **own**
    catalogue (`{name: entry}`, entries as in `hello`); `extra_args_checks`
    maps a capability to a callable that raises ProtocolError for args its
    own schema cannot express (the recipe params of
    `applescript.recipe.run`)."""

    node_id: str
    pinned: Mapping[str, bytes]
    catalogue: Mapping[str, Mapping]
    nonce_cache: NonceCache = field(default_factory=NonceCache)
    extra_args_checks: Mapping[str, Callable] = field(default_factory=dict)


def _refuse(code, check):
    raise Refusal(code, check)


def _decode(frame, version):
    try:
        if isinstance(frame, (str, bytes, bytearray)):
            return decode_frame(frame, "server_to_node", version)
        return validate(frame, "server_to_node", version)
    except ProtocolError as exc:
        check = {"too_large": 1, "malformed": 2, "unknown_type": 3}.get(exc.code, 4)
        raise Refusal(exc.code, check) from exc


def _signature(envelope, state, version):
    try:
        verify_envelope(envelope, state.pinned, version=version)
    except SignatureError as exc:
        raise Refusal(exc.code, 5 if exc.code == "unknown_key" else 6) from exc


def _times(envelope, now, max_ttl):
    if envelope["issued_at"] > now + CLOCK_SKEW_SECONDS:
        _refuse("not_yet_valid", 8)
    if envelope["expires_at"] <= now:
        _refuse("expired", 9)
    ttl = envelope["expires_at"] - envelope["issued_at"]
    if ttl <= 0 or ttl > max_ttl:
        _refuse("ttl_too_long", 10)


def _args(envelope, entry, state, *, hash_check, schema_check):
    try:
        args_hash = sha256_hex(envelope["args"])
    except CanonicalizationError:
        args_hash = None
    if args_hash != envelope["args_sha256"]:
        _refuse("args_hash_mismatch", hash_check)
    try:
        validate_instance(entry.get("args_schema") or {}, envelope["args"], code="args_schema")
        extra = state.extra_args_checks.get(envelope["capability"])
        if extra is not None:
            extra(envelope["args"])
    except ProtocolError as exc:
        raise Refusal("args_schema", schema_check) from exc


def verify_invoke(frame, *, version, state: NodeState, now: int) -> dict:
    """Run the protocol-level checks on one server -> node `invoke` frame
    (raw bytes/str, or an already parsed dict) for a session of `version`.
    Returns the envelope; raises Refusal. A non-`invoke` frame passes
    checks 1-4 and is returned untouched (it is not an invocation)."""
    envelope = _decode(frame, version)
    if envelope.get("type") != "invoke":
        return envelope
    _signature(envelope, state, version)
    if envelope["node_id"] != state.node_id:
        _refuse("wrong_node", 7)
    capability_class = envelope["class"]
    if version == 1:
        max_ttl = MAX_READ_INVOKE_TTL_SECONDS
    else:
        max_ttl = MAX_INVOKE_TTL_SECONDS_V2[capability_class]
    _times(envelope, now, max_ttl)
    state.nonce_cache.check_and_add(envelope["nonce"], envelope["expires_at"], now)
    entry = state.catalogue.get(envelope["capability"])
    if entry is None or entry.get("version") != envelope["capability_version"]:
        _refuse("unknown_capability", 12)

    if version == 1:
        if capability_class != "read" or envelope["grant"] is not None:
            _refuse("class_not_allowed", 13)
        _args(envelope, entry, state, hash_check=14, schema_check=15)
        if entry.get("availability") != "granted":
            _refuse("not_granted", 16)
        return envelope

    if capability_class != entry.get("class"):
        _refuse("class_mismatch", 13)
    grant = envelope["grant"]
    if capability_class == "gate":
        if grant is None:
            _refuse("grant_missing", 14)
        try:
            verify_grant_signature(grant, state.pinned)
        except GrantError as exc:
            raise Refusal(exc.code, 15 if exc.code == "unknown_key" else 16) from exc
        try:
            check_grant_binding(grant, envelope)
        except GrantError as exc:
            raise Refusal("grant_mismatch", 17) from exc
        try:
            check_grant_ttl(grant)
        except GrantError as exc:
            raise Refusal("ttl_too_long", 18) from exc
    elif grant is not None:
        _refuse("grant_unexpected", 14)
    _args(envelope, entry, state, hash_check=19, schema_check=20)
    if entry.get("availability") != "granted":
        _refuse("not_granted", 21)
    return envelope
