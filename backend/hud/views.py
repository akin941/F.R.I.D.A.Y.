"""`GET /api/v1/hud/events?workspace=<uuid>[&date=YYYY-MM-DD]` -- the HUD's
one event stream (Phase 6 branch 5, brief A8).

Server-Sent Events, `text/event-stream`, one JSON object per `data:` line:

- `{"type": "snapshot", "workspace", "interval_seconds", "approvals",
  "tasks", "cost", "health", "terminal", "notices", "digest"}` first
  (Phase 12 branch 2 adds `notices` and `digest`, read through
  `proactive.api`);
- `{"type": "delta", <changed sections only>}` when something changed
  since the previous tick; a `: keepalive` comment otherwise;
- `{"type": "end", "reason"}` when the stream closes on purpose
  (`max_age` after `HUD_STREAM_MAX_SECONDS`, `scope_lost` when the actor
  is no longer a member of the workspace). The client reconnects after the
  `retry:` hint, which re-runs authentication and every permission check:
  a stream never outlives a revoked device or a lost membership by more
  than one bounded window, and a sync worker is never held indefinitely
  (Phase 4 R4b concurrency budget).

Principals: the owner's trusted-device web session only (`IsWebChannel` +
`IsOwner`), like the voice endpoints. Authentication classes are injected
by the composition root (`config/urls.py` -> `hud.urls.build_urlpatterns`)
so a PAT reaches the view and gets a deliberate 403 rather than an
incidental 401; `hud` may not import `accounts`. The workspace is resolved
through `for_actor()` -- another workspace is a 404 before any read.
Voice state is client-side; this stream carries approval, task, cost,
health and (Phase 10) terminal-summary deltas only, and writes nothing.

Phase 10 branch 5 (D12) adds `GET /api/v1/hud/terminal/<execution_id>/
stream`: the same principals and bounds, the scrubbed output of one of the
actor's own node action executions from the Redis stream, then its final
status.
"""

import json
import time

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import StreamingHttpResponse
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsOwner, IsWebChannel
from core.throttling import ScopedRateThrottle
from hud.services import build_sections, changed_sections, execution_summary
from nodes import bus as node_bus
from nodes import output as node_output
from nodes.models import EXECUTION_TERMINAL_STATUSES, NodeActionExecution
from workspaces.models import Workspace

# Seams for the tests (fake clock, no real waiting).
sleep = time.sleep
monotonic = time.monotonic


class HudEventsQuerySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    # The client's local date for "today's tasks" (the /today rule).
    date = serializers.DateField(required=False)


def _frame(payload):
    return f"data: {json.dumps(payload, cls=DjangoJSONEncoder, separators=(',', ':'))}\n\n"


def _member(actor, workspace_id):
    return Workspace.objects.for_actor(actor, workspace=workspace_id).first()


def event_stream(actor, workspace, *, today=None):
    interval = max(1, int(settings.HUD_STREAM_INTERVAL_SECONDS))
    max_seconds = max(interval, int(settings.HUD_STREAM_MAX_SECONDS))
    yield f"retry: {int(settings.HUD_STREAM_RETRY_MS)}\n\n"
    current = build_sections(actor, workspace, today=today)
    yield _frame(
        {
            "type": "snapshot",
            "workspace": str(workspace.id),
            "interval_seconds": interval,
            **current,
        }
    )
    started = monotonic()
    while True:
        sleep(interval)
        if monotonic() - started >= max_seconds:
            yield _frame({"type": "end", "reason": "max_age"})
            return
        if _member(actor, workspace.id) is None:
            yield _frame({"type": "end", "reason": "scope_lost"})
            return
        latest = build_sections(actor, workspace, today=today)
        delta = changed_sections(current, latest)
        if delta:
            current = latest
            yield _frame({"type": "delta", **delta})
        else:
            yield ": keepalive\n\n"


def _sse(payload, *, event_id=None):
    head = f"id: {event_id}\n" if event_id else ""
    return head + _frame(payload)


def _final(execution, *, stream_truncated=False):
    return {
        "type": "end",
        "status": execution.status,
        "exit_code": execution.exit_code,
        "truncated": bool(stream_truncated or execution.truncated),
    }


def _output_entries(execution, after):
    """The stream entries after `after`; Redis down reads as nothing (the
    final status still comes from the row)."""
    if execution.invocation_id is None:
        return [], False
    try:
        return node_output.read_output(execution.invocation_id, after=after), False
    except node_bus.BusUnavailable:
        return [], True


def terminal_stream(actor, workspace, execution, *, after="0-0"):
    """The terminal panel's SSE body (D12): `start`, then one `chunk`
    `{seq, stream, text}` per Redis stream entry (each with its entry id as
    the SSE `id:`, so a reconnect resumes after it), then one final `end`
    `{status, exit_code, truncated}` read from the execution row. Bounded by
    `HUD_STREAM_MAX_SECONDS` (`end` with `reason: max_age`) and re-checks the
    actor's membership every poll (`scope_lost`)."""
    poll = max(0.05, float(settings.HUD_TERMINAL_POLL_SECONDS))
    max_seconds = max(1, int(settings.HUD_STREAM_MAX_SECONDS))
    yield f"retry: {int(settings.HUD_STREAM_RETRY_MS)}\n\n"
    yield _sse({"type": "start", **execution_summary(execution)})
    started = monotonic()
    stream_truncated = False
    while True:
        entries, unavailable = _output_entries(execution, after)
        ended = False
        for entry in entries:
            after = entry["id"]
            if entry.get("kind") == node_output.ENTRY_END:
                stream_truncated = stream_truncated or entry.get("truncated") == "1"
                ended = True
                continue
            if entry.get("kind") != node_output.ENTRY_CHUNK:
                continue
            try:
                seq = int(entry.get("seq", "0"))
            except ValueError:
                continue
            # Already scrubbed and sanitised by the gateway; re-sanitised
            # here so nothing but plain text ever reaches the panel.
            text = node_output.sanitize_terminal_text(entry.get("text", ""))
            yield _sse(
                {"type": "chunk", "seq": seq, "stream": entry.get("stream", ""), "text": text},
                event_id=after,
            )
        execution.refresh_from_db()
        if ended or (execution.status in EXECUTION_TERMINAL_STATUSES and not entries):
            yield _sse(
                {
                    **_final(execution, stream_truncated=stream_truncated),
                    "output_unavailable": unavailable,
                },
                event_id=after if after != "0-0" else None,
            )
            return
        if entries:
            continue  # drain a backlog before waiting
        sleep(poll)
        if monotonic() - started >= max_seconds:
            yield _frame({"type": "end", "reason": "max_age"})
            return
        if _member(actor, workspace.id) is None:
            yield _frame({"type": "end", "reason": "scope_lost"})
            return


class HudTerminalStreamView(APIView):
    """`GET /api/v1/hud/terminal/<execution_id>/stream` (Phase 10 D12): the
    owner's trusted-device web session only; the execution is resolved
    through `for_actor()` and must be the actor's own -- anything else is a
    404 before any read. The output is untrusted, scrubbed text; the panel
    renders it as text nodes only."""

    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "hud_terminal"

    def perform_content_negotiation(self, request, force=False):
        return (JSONRenderer(), "application/json")

    def get(self, request, execution_id):
        execution = (
            NodeActionExecution.objects.for_actor(request.user)
            .filter(pk=execution_id, actor=request.user)
            .select_related("workspace", "approval")
            .first()
        )
        if execution is None or _member(request.user, execution.workspace_id) is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        after = request.headers.get("Last-Event-ID", "")
        if not node_output.valid_stream_id(after):
            after = "0-0"
        response = StreamingHttpResponse(
            terminal_stream(request.user, execution.workspace, execution, after=after),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache, no-store"
        response["X-Accel-Buffering"] = "no"
        return response


class HudEventsView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "hud_events"

    def perform_content_negotiation(self, request, force=False):
        # The success body is a hand-built event stream, never rendered by
        # DRF; every DRF `Response` (errors) is JSON whatever the client's
        # Accept header says (the chat view's pattern) -- an EventSource
        # sending `Accept: text/event-stream` would otherwise get a 406.
        return (JSONRenderer(), "application/json")

    def get(self, request):
        query = HudEventsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _member(request.user, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        response = StreamingHttpResponse(
            event_stream(request.user, workspace, today=query.validated_data.get("date")),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache, no-store"
        response["X-Accel-Buffering"] = "no"
        return response
