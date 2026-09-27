"""Connector bindings API (Phase 5 branch 4, brief D3): the installation
owner, over a trusted device's web session only, lists the connectors a
workspace may bind and switches them on or off. Default authentication only
(no PAT allowlist): a CLI or facade token never reaches these views."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from connectors.manifest import get_manifest
from connectors.models import ConnectorBinding
from connectors.serializers import (
    BindingQuerySerializer,
    BindingUpdateSerializer,
    serialize_binding,
)
from connectors.services import (
    ConnectorInvalidTransition,
    ConnectorPermissionDenied,
    list_bindings,
    set_binding_enabled,
)
from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from workspaces.models import Workspace


def _service_error_response(exc):
    if isinstance(exc, ConnectorPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, ConnectorInvalidTransition):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _workspace(request, workspace_id):
    return Workspace.objects.for_actor(request.user).filter(id=workspace_id).first()


class ConnectorBindingListView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def get(self, request):
        query = BindingQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rows = list_bindings(workspace=workspace, actor=request.user)
        except ConnectorPermissionDenied as exc:
            return _service_error_response(exc)
        return Response([serialize_binding(m, b, workspace) for m, b in rows])


class ConnectorBindingDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def put(self, request, connector):
        body = BindingUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        workspace = _workspace(request, body.validated_data["workspace"])
        manifest = get_manifest(connector)
        if workspace is None or manifest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            set_binding_enabled(
                workspace=workspace,
                actor=request.user,
                connector=connector,
                enabled=body.validated_data["enabled"],
                channel=request_channel(request),
            )
        except (ConnectorPermissionDenied, ConnectorInvalidTransition) as exc:
            return _service_error_response(exc)
        binding = ConnectorBinding.objects.get(workspace=workspace, connector=connector)
        return Response(serialize_binding(manifest, binding, workspace))
