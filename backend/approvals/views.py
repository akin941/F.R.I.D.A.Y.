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
from core.channels import is_low_trust, request_channel, scoped_for_actor
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
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, InvalidTransition):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
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


class ApprovalRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        requests = scoped_for_actor(request, ApprovalRequest.objects, workspace=workspace)
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in ApprovalRequest.Status.values:
                return Response(
                    {"detail": "invalid status filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            requests = requests.filter(status=status_filter)
        # This list is unpaginated and the serializer's `execution` field
        # reads each tool's execution row per request (Codex review,
        # branch 4) -- select_related the one relation that exists today
        # so that read is a cache hit, not an N+1 query. registry.py's own
        # "extract once a second tool needs it" rule (see its module
        # docstring) applies here too: this stays a single hardcoded
        # relation name until a second describe_execution-backed tool
        # actually needs a generic batch-prefetch hook.
        requests = requests.select_related("vault_export_run").order_by("-created_at", "-id")
        return Response(ApprovalRequestSerializer(requests, many=True).data)

    def post(self, request):
        data = request.data
        if is_low_trust(request):
            # `.copy()` first: request.data is an immutable QueryDict for
            # form-encoded requests (Codex review, branch 1 round 1).
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
            )
        except (ApprovalPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(
            ApprovalRequestSerializer(approval_request).data, status=status.HTTP_201_CREATED
        )


class ApprovalRequestTransitionView(APIView):
    permission_classes = [IsAuthenticated]
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
        return Response(ApprovalRequestSerializer(approval_request).data)
