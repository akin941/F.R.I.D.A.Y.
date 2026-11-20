"""Deploy API (Phase 5 branch 6, brief D10). Owner-only throughout.

- targets (list/create/detail/edit, status refresh): web only;
- runs (list/detail): web or CLI; cancel: web only;
- requests (`POST`, mints the `deploy` gate): web or CLI.

Authentication classes are injected by the composition root
(`config/urls.py` -> `deploys.urls.build_urlpatterns`): `deploys` may not
import `accounts` (ADR-010 contracts), yet its CLI surfaces need the PAT
authenticator ahead of the device session -- and every surface here takes
it, so a CLI token on a web-only endpoint gets a deliberate 403
(`channel_forbidden`) instead of an incidental 401, the approvals pattern.
An `openai` token is refused by that authenticator itself
(`token_kind_forbidden`).

Workspace scoping: every lookup goes through `scoped_for_actor` (a PAT is
forced to its bound workspace), so an out-of-scope row is a 404.
"""

import uuid

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.serializers import ApprovalRequestSerializer
from approvals.services import ApprovalPermissionDenied, InvalidTransition
from core.api_errors import service_error_response
from core.channels import CHANNEL_CLI, CHANNEL_WEB, request_channel, scoped_for_actor
from core.permissions import IsOwner, IsWebChannel
from deploys.models import DeployRun, DeployTarget
from deploys.serializers import (
    DeployRequestCreateSerializer,
    DeployRunDetailSerializer,
    DeployRunSerializer,
    DeployTargetCreateSerializer,
    DeployTargetEditSerializer,
    DeployTargetSerializer,
)
from deploys.services import (
    DeployInvalidTransition,
    DeployPermissionDenied,
    cancel_deploy_run,
    create_target,
    edit_target,
    request_deploy,
    request_target_status,
)
from workspaces.models import Workspace


class IsWebOrCliChannel(BasePermission):
    """The two channels a deploy may be requested (and runs read) over;
    never Telegram, the OpenAI-compatible facade or an unknown channel."""

    message = "This action is available only from the web app or the CLI."
    code = "channel_forbidden"

    def has_permission(self, request, view):
        return request_channel(request) in (CHANNEL_WEB, CHANNEL_CLI)


_WEB_OWNER = [IsAuthenticated, IsOwner, IsWebChannel]
_WEB_OR_CLI_OWNER = [IsAuthenticated, IsOwner, IsWebOrCliChannel]


def _service_error_response(exc):
    if isinstance(exc, DeployPermissionDenied | ApprovalPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, DeployInvalidTransition | InvalidTransition):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _workspace_param(request):
    """(workspace, error). The `workspace` query param -- required on the
    web channel, implied on the CLI, whose PAT `scoped_for_actor` forces
    to its bound workspace whatever is supplied."""
    raw = request.query_params.get("workspace")
    workspace_uuid = None
    if raw:
        try:
            workspace_uuid = uuid.UUID(raw)
        except ValueError:
            return None, Response(
                {"detail": "workspace must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
            )
    elif request_channel(request) == CHANNEL_WEB:
        return None, Response(
            {"detail": "workspace query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    workspace = scoped_for_actor(request, Workspace.objects, workspace=workspace_uuid).first()
    if workspace is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return workspace, None


class DeployTargetListCreateView(APIView):
    permission_classes = _WEB_OWNER

    def get(self, request):
        workspace, error = _workspace_param(request)
        if error is not None:
            return error
        targets = DeployTarget.objects.for_actor(request.user, workspace=workspace).order_by("name")
        return Response(DeployTargetSerializer(targets, many=True).data)

    def post(self, request):
        serializer = DeployTargetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=fields.pop("workspace")
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            target = create_target(
                workspace=workspace, actor=request.user, channel=request_channel(request), **fields
            )
        except (DeployPermissionDenied, DeployInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(DeployTargetSerializer(target).data, status=status.HTTP_201_CREATED)


def _target(request, target_id):
    return scoped_for_actor(request, DeployTarget.objects).filter(id=target_id).first()


class DeployTargetDetailView(APIView):
    permission_classes = _WEB_OWNER

    def get(self, request, target_id):
        target = _target(request, target_id)
        if target is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(DeployTargetSerializer(target).data)

    def patch(self, request, target_id):
        target = _target(request, target_id)
        if target is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = DeployTargetEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            target = edit_target(
                target=target,
                actor=request.user,
                channel=request_channel(request),
                **serializer.validated_data,
            )
        except (DeployPermissionDenied, DeployInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(DeployTargetSerializer(target).data)


class DeployTargetStatusView(APIView):
    """`POST .../targets/<id>/status` -- queue a read-only `status` verb on
    the runner; the result lands on the target (`current_sha`,
    `last_status_at`, `last_status_error`)."""

    permission_classes = _WEB_OWNER

    def post(self, request, target_id):
        target = _target(request, target_id)
        if target is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            request_target_status(
                target=target, actor=request.user, channel=request_channel(request)
            )
        except (DeployPermissionDenied, DeployInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(status=status.HTTP_202_ACCEPTED)


class DeployRunListView(APIView):
    permission_classes = _WEB_OR_CLI_OWNER

    def get(self, request):
        workspace, error = _workspace_param(request)
        if error is not None:
            return error
        runs = scoped_for_actor(request, DeployRun.objects, workspace=workspace)
        target_id = request.query_params.get("target")
        if target_id:
            try:
                runs = runs.filter(target_id=uuid.UUID(target_id))
            except ValueError:
                return Response(
                    {"detail": "target must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
                )
        runs = runs.select_related("target").order_by("-created_at")[:100]
        return Response(DeployRunSerializer(runs, many=True).data)


def _run(request, run_id):
    return (
        scoped_for_actor(request, DeployRun.objects)
        .filter(id=run_id)
        .select_related("target", "workspace")
        .first()
    )


class DeployRunDetailView(APIView):
    permission_classes = _WEB_OR_CLI_OWNER

    def get(self, request, run_id):
        run = _run(request, run_id)
        if run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(DeployRunDetailSerializer(run).data)


class DeployRunCancelView(APIView):
    permission_classes = _WEB_OWNER

    def post(self, request, run_id):
        run = _run(request, run_id)
        if run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            run = cancel_deploy_run(run=run, actor=request.user, channel=request_channel(request))
        except (DeployPermissionDenied, DeployInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(DeployRunDetailSerializer(run).data)


class DeployRequestCreateView(APIView):
    """`POST /deploys/requests {target, sha}` -- the owner's "Deploy"
    click (or `jarvis deploy`): mints the pending `deploy` gate through
    `deploys.services.request_deploy`. The decision is a separate web-only
    step on the approvals surface."""

    permission_classes = _WEB_OR_CLI_OWNER

    def post(self, request):
        serializer = DeployRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = _target(request, serializer.validated_data["target"])
        if target is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            approval_request = request_deploy(
                workspace=target.workspace,
                actor=request.user,
                target=target,
                sha=serializer.validated_data["sha"],
                channel=request_channel(request),
            )
        except (
            DeployPermissionDenied,
            DeployInvalidTransition,
            ApprovalPermissionDenied,
            InvalidTransition,
        ) as exc:
            return _service_error_response(exc)
        return Response(
            ApprovalRequestSerializer(approval_request, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
