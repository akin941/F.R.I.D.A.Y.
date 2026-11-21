"""The hash-chained node invocation audit (Phase 9 brief D8, ADR-013
decision 10).

`append(...)` is the only writer of `NodeInvocation`. It runs under
`select_for_update` on the single `NodeAuditHead` row, which serializes
the chain installation-wide: `seq` is contiguous from 1 and every row's
`prev_hash` is the previous row's `row_hash` (64 zeros for the genesis
row).

`row_hash` is sha256 of the canonical JSON (`nodes.protocol.canonical`) of
the fields in `HASHED_FIELDS`, `prev_hash` included. Foreign keys enter as
their id strings (`workspace_id`, `node_id`, `actor_id`; null for a system
call), `created_at` as a UTC ISO-8601 string with microseconds.

Rows hold hashes, sizes and codes only -- never args or output content.
They are append-only through a DB trigger (migration 0002, the audit 0002
pattern). `verify_chain()` walks the chain (`manage.py
verify_node_audit_chain`, preflight in the closeout) and reports the first
break. It reads the table unscoped: it is system code over hashes, not a
view (`for_actor()` applies to the API, branch 5).
"""

import re
from dataclasses import dataclass
from datetime import UTC

from django.db import transaction
from django.utils import timezone

from nodes.models import (
    GENESIS_HASH,
    NOT_SENT_STATUSES,
    TERMINAL_VOCABULARY,
    NodeAuditHead,
    NodeInvocation,
)
from nodes.protocol.canonical import sha256_hex

HASHED_FIELDS = (
    "id",
    "seq",
    "created_at",
    "workspace_id",
    "node_id",
    "capability",
    "capability_version",
    "args_sha256",
    "nonce",
    "status",
    "refusal_code",
    "output_sha256",
    "output_bytes",
    "truncated",
    "dropped_unmapped",
    "duration_ms",
    "trace_id",
    "actor_id",
    "channel",
    "prev_hash",
)

_HEX64 = re.compile(r"[0-9a-f]{64}")


class ChainWriteError(ValueError):
    """`append` was called with a value outside the chain's vocabulary."""


def _timestamp(value) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def hash_payload(row) -> dict:
    """The dict whose canonical JSON is hashed into `row_hash`."""
    payload = {}
    for name in HASHED_FIELDS:
        value = getattr(row, name)
        if name == "created_at":
            value = _timestamp(value)
        elif name in ("id", "workspace_id", "node_id", "actor_id"):
            value = None if value is None else str(value)
        payload[name] = value
    return payload


def compute_row_hash(row) -> str:
    return sha256_hex(hash_payload(row))


def _locked_head():
    head = NodeAuditHead.objects.select_for_update().filter(pk=NodeAuditHead.SINGLETON_PK).first()
    if head is not None:
        return head
    # First write (or a flushed test database): start from the chain's tip
    # so a missing head can never restart `seq` under existing rows. A
    # concurrent creator wins the insert; this one then waits on its lock.
    last = NodeInvocation.objects.order_by("-seq").first()
    NodeAuditHead.objects.bulk_create(
        [
            NodeAuditHead(
                pk=NodeAuditHead.SINGLETON_PK,
                last_seq=last.seq if last else 0,
                last_hash=last.row_hash if last else GENESIS_HASH,
            )
        ],
        ignore_conflicts=True,
    )
    return NodeAuditHead.objects.select_for_update().get(pk=NodeAuditHead.SINGLETON_PK)


def _check(*, status, refusal_code, nonce, output_sha256, output_bytes, args_sha256, trace_id):
    if status not in TERMINAL_VOCABULARY:
        raise ChainWriteError("unknown status")
    if refusal_code not in TERMINAL_VOCABULARY[status]:
        raise ChainWriteError("refusal code not allowed for this status")
    if status in NOT_SENT_STATUSES and (nonce or output_sha256 or output_bytes):
        raise ChainWriteError("a not-sent call has no nonce and no output")
    if not _HEX64.fullmatch(args_sha256 or ""):
        raise ChainWriteError("args_sha256 must be a sha256 hex digest")
    if output_sha256 and not _HEX64.fullmatch(output_sha256):
        raise ChainWriteError("output_sha256 must be empty or a sha256 hex digest")
    if not trace_id or len(trace_id) > 64:
        raise ChainWriteError("trace_id is required (at most 64 characters)")


def append(
    *,
    workspace,
    node,
    capability,
    capability_version,
    args_sha256,
    status,
    channel,
    trace_id,
    actor=None,
    nonce="",
    refusal_code="",
    output_sha256="",
    output_bytes=0,
    truncated=False,
    dropped_unmapped=0,
    duration_ms=0,
) -> NodeInvocation:
    """Append one chain row for a node call at its terminal state (D7).
    Returns the saved NodeInvocation."""
    _check(
        status=status,
        refusal_code=refusal_code,
        nonce=nonce,
        output_sha256=output_sha256,
        output_bytes=output_bytes,
        args_sha256=args_sha256,
        trace_id=trace_id,
    )
    with transaction.atomic():
        head = _locked_head()
        row = NodeInvocation(
            seq=head.last_seq + 1,
            created_at=timezone.now(),
            workspace=workspace,
            node=node,
            capability=capability,
            capability_version=capability_version,
            args_sha256=args_sha256,
            nonce=nonce,
            status=status,
            refusal_code=refusal_code,
            output_sha256=output_sha256,
            output_bytes=output_bytes,
            truncated=truncated,
            dropped_unmapped=dropped_unmapped,
            duration_ms=duration_ms,
            trace_id=trace_id,
            actor=actor,
            channel=channel,
            prev_hash=head.last_hash,
        )
        row.row_hash = compute_row_hash(row)
        row.save(force_insert=True)
        head.last_seq = row.seq
        head.last_hash = row.row_hash
        head.save(update_fields=["last_seq", "last_hash", "updated_at"])
    return row


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    rows: int
    broken_seq: int | None = None
    reason: str = ""


def verify_chain(chunk_size=1000) -> ChainVerification:
    """Walk the chain in `seq` order. The first break is reported with its
    `seq` and a reason: `seq_gap`, `prev_hash_mismatch`, `row_hash_mismatch`
    or, for the tip, `head_mismatch` / `head_missing`."""
    expected_seq = 1
    prev_hash = GENESIS_HASH
    rows = 0
    for row in NodeInvocation.objects.order_by("seq").iterator(chunk_size=chunk_size):
        if row.seq != expected_seq:
            return ChainVerification(False, rows, expected_seq, "seq_gap")
        if row.prev_hash != prev_hash:
            return ChainVerification(False, rows, row.seq, "prev_hash_mismatch")
        if row.row_hash != compute_row_hash(row):
            return ChainVerification(False, rows, row.seq, "row_hash_mismatch")
        prev_hash = row.row_hash
        expected_seq += 1
        rows += 1
    head = NodeAuditHead.objects.filter(pk=NodeAuditHead.SINGLETON_PK).first()
    if head is None:
        if rows:
            return ChainVerification(False, rows, rows, "head_missing")
        return ChainVerification(True, 0)
    if head.last_seq != rows or head.last_hash != prev_hash:
        return ChainVerification(False, rows, min(head.last_seq, rows + 1) or 1, "head_mismatch")
    return ChainVerification(True, rows)
