"""Owner APIs for ingestion (Phase 8A branch 1, brief D11): sources
(enable/pause/budget), mapping rules, scrub terms, forget/restore and the
audit-view reads.

The installation owner, over a trusted device's web session only
(`IsWebChannel` + `IsOwner`); default authentication only, so a CLI or
facade PAT never reaches these views. Every lookup goes through
`for_actor()`: another workspace's sources, rules and terms are 404, never
403. The services re-check the owner role and the channel.
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import record_audit
from core.api_errors import service_error_response
from core.channels import CHANNEL_HOOK, ChannelPrincipal, request_channel
from core.permissions import IsOwner, IsWebChannel
from core.throttling import ScopedRateThrottle
from memory.ingestion import hooks, services
from memory.ingestion.serializers import (
    RuleCreateSerializer,
    RuleUpdateSerializer,
    ScrubTermCreateSerializer,
    SessionQuerySerializer,
    SourceUpdateSerializer,
    WorkspaceQuerySerializer,
    serialize_audit,
    serialize_coding_session,
    serialize_rule,
    serialize_source,
    serialize_term,
)
from memory.models import (
    CodingSession,
    IngestionCounter,
    IngestionSource,
    ScrubTerm,
    WorkspaceMappingRule,
)
from workspaces.models import Workspace

_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]


def _error(exc):
    if isinstance(exc, services.IngestionPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, services.IngestionInvalid):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _workspace(request, workspace_id):
    return Workspace.objects.for_actor(request.user, workspace=workspace_id).first()


def _source(request, source_id):
    return (
        IngestionSource.objects.for_actor(request.user)
        .filter(id=source_id)
        .select_related("workspace")
        .first()
    )


class IngestionSourceListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        query = WorkspaceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.require_owner_read(request.user, workspace, request_channel(request))
        except services.IngestionError as exc:
            return _error(exc)
        sources = list(IngestionSource.objects.for_actor(request.user, workspace=workspace))
        today = {
            c.source_id: c
            for c in IngestionCounter.objects.filter(source__in=sources, day=timezone.localdate())
        }
        record_audit(
            action="memory.ingestion.viewed",
            actor=request.user,
            workspace=workspace,
            metadata={"view": "sources", "count": len(sources)},
        )
        return Response([serialize_source(s, today.get(s.id)) for s in sources])


class IngestionSourceDetailView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request, source_id):
        source = _source(request, source_id)
        if source is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            view = services.source_audit(
                source, actor=request.user, channel=request_channel(request)
            )
        except services.IngestionError as exc:
            return _error(exc)
        return Response(serialize_audit(view))

    def patch(self, request, source_id):
        source = _source(request, source_id)
        if source is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = SourceUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            source = services.update_source(
                source,
                actor=request.user,
                channel=request_channel(request),
                **body.validated_data,
            )
        except services.IngestionError as exc:
            return _error(exc)
        return Response(serialize_source(source))


class IngestionSourceActionView(APIView):
    """POST forget | restore (confirm-class, reversible, ADR-009)."""

    permission_classes = _PERMISSIONS
    _ACTIONS = {"forget": services.forget_source, "restore": services.restore_source}

    def post(self, request, source_id, action):
        handler = self._ACTIONS.get(action)
        source = _source(request, source_id)
        if handler is None or source is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            result = handler(source, actor=request.user, channel=request_channel(request))
        except services.IngestionError as exc:
            return _error(exc)
        source.refresh_from_db()
        return Response({**result, "source": serialize_source(source)})


class MappingRuleListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request, source_id):
        source = _source(request, source_id)
        if source is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.require_owner_read(request.user, source.workspace, request_channel(request))
        except services.IngestionError as exc:
            return _error(exc)
        rules = WorkspaceMappingRule.objects.for_actor(request.user).filter(source=source)
        return Response([serialize_rule(r) for r in rules])

    def post(self, request, source_id):
        source = _source(request, source_id)
        if source is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = RuleCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        target = _workspace(request, body.validated_data["target_workspace"])
        if target is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rule = services.create_mapping_rule(
                source,
                actor=request.user,
                channel=request_channel(request),
                rule_type=body.validated_data["rule_type"],
                pattern=body.validated_data["pattern"],
                target_workspace=target,
            )
        except services.IngestionError as exc:
            return _error(exc)
        return Response(serialize_rule(rule), status=status.HTTP_201_CREATED)


class MappingRuleDetailView(APIView):
    permission_classes = _PERMISSIONS

    def patch(self, request, rule_id):
        rule = (
            WorkspaceMappingRule.objects.for_actor(request.user)
            .filter(id=rule_id)
            .select_related("workspace", "source__workspace")
            .first()
        )
        if rule is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = RuleUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        target = None
        if "target_workspace" in body.validated_data:
            target = _workspace(request, body.validated_data["target_workspace"])
            if target is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rule = services.update_mapping_rule(
                rule,
                actor=request.user,
                channel=request_channel(request),
                enabled=body.validated_data.get("enabled"),
                target_workspace=target,
            )
        except services.IngestionError as exc:
            return _error(exc)
        return Response(serialize_rule(rule))


class ScrubTermListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        query = WorkspaceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.require_owner_read(request.user, workspace, request_channel(request))
        except services.IngestionError as exc:
            return _error(exc)
        terms = ScrubTerm.objects.for_actor(request.user, workspace=workspace)
        return Response([serialize_term(t) for t in terms])

    def post(self, request):
        body = ScrubTermCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        workspace = _workspace(request, body.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            term = services.create_scrub_term(
                workspace=workspace,
                actor=request.user,
                channel=request_channel(request),
                term=body.validated_data["term"],
            )
        except services.IngestionError as exc:
            return _error(exc)
        return Response(serialize_term(term), status=status.HTTP_201_CREATED)


class ScrubTermDetailView(APIView):
    permission_classes = _PERMISSIONS

    def delete(self, request, term_id):
        term = ScrubTerm.objects.for_actor(request.user).filter(id=term_id).first()
        if term is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.delete_scrub_term(term, actor=request.user, channel=request_channel(request))
        except services.IngestionError as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Claude Code hook relay (Phase 8A branch 3, D6/D12) ----------------------


class IsHookChannel(BasePermission):
    """Only a `hook`-kind PAT (CHANNEL_HOOK). The view's sole authenticator
    (`accounts.authentication.HookTokenAuthentication`, injected by
    `config/urls.py`) already refuses every other credential; this keeps the
    view closed if it ever gains another authenticator."""

    message = "This endpoint accepts only a hook token."
    code = "channel_forbidden"

    def has_permission(self, request, view):
        return request_channel(request) == CHANNEL_HOOK


def _too_large():
    return Response(
        {"detail": "Hook-event batch too large."},
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


class HookEventIngestView(APIView):
    """`POST /api/v1/memory/ingest/hook-events` -- append-only (D6).

    Accepts `{"events": [...]}` (<= 50 events, <= 64 KB body), keeps only
    the allow-listed events and fields, and hands each to the ingestion port
    (scrub, map by `cwd`, dedupe). Answers `202` with counts only: it never
    echoes a payload, lists nothing and reads nothing back. Authentication is
    injected (`build_hook_urlpatterns`) because `memory` does not import
    `accounts`; with none injected nobody is authenticated."""

    authentication_classes = []
    permission_classes = [IsAuthenticated, IsHookChannel]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "hook_ingest"

    def post(self, request):
        declared = request.META.get("CONTENT_LENGTH") or "0"
        try:
            declared = int(declared)
        except ValueError:
            declared = 0
        if declared > hooks.MAX_BODY_BYTES or len(request.body) > hooks.MAX_BODY_BYTES:
            return _too_large()
        data = request.data
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            return Response(
                {"detail": "Expected an object with an `events` list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(events) > hooks.MAX_EVENTS_PER_BATCH:
            return _too_large()
        auth = request.auth
        counts = hooks.ingest_hook_events(
            user=request.user,
            bound_workspace_id=auth.bound_workspace_id
            if isinstance(auth, ChannelPrincipal)
            else None,
            events=events,
        )
        return Response(counts, status=status.HTTP_202_ACCEPTED)


class CodingSessionListView(APIView):
    """`GET /api/v1/memory/sessions` (D12): the owner's coding sessions,
    newest first, through `for_actor()`. Owner, web session only; rows of a
    forgotten source are hidden until it is restored."""

    permission_classes = _PERMISSIONS

    def get(self, request):
        query = SessionQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace_id = query.validated_data.get("workspace")
        if workspace_id is not None and _workspace(request, workspace_id) is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        sessions = (
            CodingSession.objects.for_actor(request.user, workspace=workspace_id)
            .filter(source__forgotten_at__isnull=True)
            .select_related("workspace")
            .order_by("-last_event_at", "-id")[: query.validated_data["limit"]]
        )
        now = timezone.now()
        return Response(
            [serialize_coding_session(s, status=hooks.effective_status(s, now)) for s in sessions]
        )
