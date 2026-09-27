import uuid

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from accounts.authentication import PersonalAccessTokenAuthentication
from approvals.models import ApprovalRequest
from approvals.serializers import ApprovalRequestCreateSerializer, ApprovalRequestSerializer
from approvals.services import (
    ApprovalPermissionDenied,
    InvalidTransition,
    approve_request,
    create_request,
    reject_request,
)
from core.api_errors import service_error_response
from core.channels import is_low_trust, request_channel, scoped_for_actor
from core.permissions import IsWebChannel
from workspaces.models import Workspace

_TRANSITIONS = {
    "approve": approve_request,
    "reject": reject_request,
}

# Allowlisted for PAT auth (MVP 9 §1/§2). The transition view is allowlisted
# too, specifically so a PAT-authenticated decide attempt gets the
# deliberate channel-restriction 403 (approvals.services) rather than a
# generic 401. PersonalAccessTokenAuthentication MUST precede
# JWTAuthentication -- see accounts/authentication.py's docstring for why.
_PAT_ALLOWLISTED_AUTHENTICATION_CLASSES = [
    PersonalAccessTokenAuthentication,
    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
]


def _service_error_response(exc):
    if isinstance(exc, ApprovalPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, InvalidTransition):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _resolve_workspace(request):
    """Validate the `workspace` query param and return (workspace, error).
    A CLI/PAT request has no workspace to supply -- the presence
    requirement is skipped entirely for a low-trust channel."""
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        if not is_low_trust(request):
            return None, Response(
                {"detail": "workspace query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace_uuid = None
    else:
        try:
            workspace_uuid = uuid.UUID(workspace_id)
        except ValueError:
            return None, Response(
                {"detail": "workspace must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
            )
    workspace = scoped_for_actor(request, Workspace.objects, workspace=workspace_uuid).first()
    if workspace is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return workspace, None


SCOPE_WORKSPACE = "workspace"
SCOPE_ALL = "all"

# Exact-match list filters (Phase 4 branch 5 inbox): query param -> the
# allowed values (`None` = free text, bounded like the model field).
_CHOICE_FILTERS = {
    "status": ApprovalRequest.Status.values,
    "origin": ApprovalRequest.Origin.values,
    "requested_channel": ApprovalRequest.RequestedChannel.values,
}
_TOOL_NAME_MAX_CHARS = ApprovalRequest._meta.get_field("tool_name").max_length


def _resolve_optional_workspace(request):
    """For `scope=all`: an optional `workspace` narrowing the inbox to one
    membership. (None, None) when absent; 404 for a workspace the actor is
    not a member of, exactly like the scoped list."""
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        return None, None
    try:
        workspace_uuid = uuid.UUID(workspace_id)
    except ValueError:
        return None, Response(
            {"detail": "workspace must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
        )
    workspace = Workspace.objects.for_actor(request.user, workspace=workspace_uuid).first()
    if workspace is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return workspace, None


def _apply_filters(request, requests):
    """Narrow an already actor-scoped queryset by the inbox filters: status,
    origin, requested channel (each validated against its choices) and
    tool. Returns (queryset, error)."""
    for param, allowed in _CHOICE_FILTERS.items():
        value = request.query_params.get(param)
        if not value:
            continue
        if value not in allowed:
            return None, Response(
                {"detail": f"invalid {param} filter."}, status=status.HTTP_400_BAD_REQUEST
            )
        requests = requests.filter(**{param: value})
    tool_name = request.query_params.get("tool")
    if tool_name:
        if len(tool_name) > _TOOL_NAME_MAX_CHARS:
            return None, Response(
                {"detail": "invalid tool filter."}, status=status.HTTP_400_BAD_REQUEST
            )
        requests = requests.filter(tool_name=tool_name)
    return requests, None


class ApprovalRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request):
        scope = request.query_params.get("scope", SCOPE_WORKSPACE)
        if scope not in (SCOPE_WORKSPACE, SCOPE_ALL):
            return Response(
                {"detail": "scope must be 'workspace' or 'all'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if scope == SCOPE_ALL and not is_low_trust(request):
            # The cross-workspace inbox (Phase 4 branch 5): every workspace
            # the actor belongs to -- `for_actor()` still limits the rows to
            # memberships; `workspace` narrows it to one of them. A human
            # surface: a machine account never gets the all-firms view.
            if getattr(request.user, "is_agent", False):
                return Response(
                    {"detail": "The cross-workspace inbox is for human owners only."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            workspace, error = _resolve_optional_workspace(request)
            if error is not None:
                return error
            requests = ApprovalRequest.objects.for_actor(request.user, workspace=workspace)
        else:
            # A low-trust channel sending `scope=all` lands here too: a PAT
            # stays forced to its bound workspace by `scoped_for_actor`,
            # whatever scope it asks for.
            workspace, error = _resolve_workspace(request)
            if error is not None:
                return error
            requests = scoped_for_actor(request, ApprovalRequest.objects, workspace=workspace)
        requests, error = _apply_filters(request, requests)
        if error is not None:
            return error
        # This list is unpaginated and the serializer's `execution` field
        # reads each tool's execution row per request (Codex review,
        # branch 4) -- select_related the one relation that exists today
        # so that read is a cache hit, not an N+1 query. registry.py's own
        # "extract once a second tool needs it" rule (see its module
        # docstring) applies here too: this stays a single hardcoded
        # relation name until a second describe_execution-backed tool
        # actually needs a generic batch-prefetch hook. `workspace` is read
        # per row by `display_summary`'s curator check.
        requests = requests.select_related("vault_export_run", "workspace").order_by(
            "-created_at", "-id"
        )
        return Response(
            ApprovalRequestSerializer(requests, many=True, context={"request": request}).data
        )

    def post(self, request):
        data = request.data
        if is_low_trust(request):
            # `.copy()` first: request.data is an immutable QueryDict for
            # form-encoded requests (Codex review, branch 1 round 1). Only
            # inject into an actual mapping -- a non-object body (e.g. a
            # JSON list) is left untouched for the serializer's own
            # validation to reject with its normal 400 (round 2).
            if isinstance(data, dict):
                data = data.copy()
                data["workspace"] = request.auth.bound_workspace_id
        serializer = ApprovalRequestCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        workspace = scoped_for_actor(
            request, Workspace.objects, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            approval_request = create_request(
                workspace=workspace,
                actor=request.user,
                tool_name=serializer.validated_data["tool_name"],
                payload=serializer.validated_data["payload"],
                # Explicit, never defaulted (ADR-008/phase-0 P2): this HTTP
                # endpoint is the human path; the serializer accepts no
                # `origin` from the body, so it cannot be spoofed here.
                origin=ApprovalRequest.Origin.HUMAN,
                # The real channel (web session or CLI PAT), from the
                # authenticator -- never from the body.
                channel=request_channel(request),
            )
        except (ApprovalPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(
            ApprovalRequestSerializer(approval_request, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ApprovalRequestDetailView(APIView):
    """`GET /api/v1/approvals/requests/<id>` -- one request, for the routed
    `/approvals/[requestId]` page (Phase 4 branch 5). Read-only. Same
    authentication as the list: a PAT reads only its bound workspace's
    requests (`scoped_for_actor`), anyone else only their memberships';
    everything else is a 404 that never leaks existence."""

    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request, request_id):
        approval_request = (
            scoped_for_actor(request, ApprovalRequest.objects)
            .filter(id=request_id)
            .select_related("vault_export_run", "workspace")
            .first()
        )
        if approval_request is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(
            ApprovalRequestSerializer(approval_request, context={"request": request}).data
        )


class ApprovalRequestTransitionView(APIView):
    # Decisions are web-only (ADR-008 decision 3). The view stays PAT-
    # allowlisted so a CLI decide attempt is a deliberate 403, not a 401;
    # the service's own channel check stays as the second line.
    permission_classes = [IsAuthenticated, IsWebChannel]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def post(self, request, request_id, action):
        transition = _TRANSITIONS.get(action)
        if transition is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # By-id lookup, no workspace parameter -- scoped_for_actor still
        # forces a channel-bound request to its bound workspace.
        approval_request = (
            scoped_for_actor(request, ApprovalRequest.objects).filter(id=request_id).first()
        )
        if approval_request is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            approval_request = transition(
                approval_request=approval_request,
                actor=request.user,
                channel=request_channel(request),
            )
        except (ApprovalPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(
            ApprovalRequestSerializer(approval_request, context={"request": request}).data
        )
