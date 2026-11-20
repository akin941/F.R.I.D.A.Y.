"""The per-turn voice trace id (Phase 6 A5).

The client generates one UUID per spoken turn and sends it as
`X-Friday-Trace` on the transcription, message and speech requests; each of
the turn's `ModelUsage` rows records it in `context_report["trace_id"]`, so
the stages of one turn can be lined up afterwards. The header is a
correlation id only: never trust-bearing, never a key to anything. A missing
or malformed value is ignored (the turn simply has no trace), never stored
and never an error.
"""

import uuid

TRACE_HEADER = "X-Friday-Trace"
TRACE_REPORT_KEY = "trace_id"
_TRACE_META_KEY = "HTTP_X_FRIDAY_TRACE"
# A canonical UUID is 36 characters; anything much longer is not one, and is
# rejected before parsing.
_MAX_RAW_LENGTH = 64


def request_trace_id(request):
    """The request's trace id as a canonical lowercase UUID string, or ""
    when the header is absent or not a UUID."""
    meta = getattr(request, "META", None) or {}
    raw = meta.get(_TRACE_META_KEY, "")
    if not isinstance(raw, str) or not raw or len(raw) > _MAX_RAW_LENGTH:
        return ""
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        return ""


def trace_report(trace_id):
    """The `context_report` fragment for a trace id (`{}` without one)."""
    return {TRACE_REPORT_KEY: trace_id} if trace_id else {}
