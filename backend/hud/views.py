"""`GET /api/v1/hud/events?workspace=<uuid>[&date=YYYY-MM-DD]` -- the HUD's
one event stream (Phase 6 branch 5, brief A8).

Server-Sent Events, `text/event-stream`, one JSON object per `data:` line:

- `{"type": "snapshot", "workspace", "interval_seconds", "approvals",
  "tasks", "cost", "health"}` first;
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
Voice state is client-side; this stream carries approval, task, cost and
health deltas only, and writes nothing.
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
from hud.services import build_sections, changed_sections
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
