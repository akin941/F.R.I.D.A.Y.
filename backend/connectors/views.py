"""Connector bindings API (Phase 5 branch 4, brief D3): the installation
owner, over a trusted device's web session only, lists the connectors a
workspace may bind and switches them on or off. Default authentication only
(no PAT allowlist): a CLI or facade token never reaches these views."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from connectors import home_entities
from connectors.manifest import get_manifest
from connectors.models import HOME_ENTITY_LABEL_MAX_CHARS, ConnectorBinding, HomeEntity
from connectors.serializers import (
    BindingQuerySerializer,
    BindingUpdateSerializer,
    HomeEntityQuerySerializer,
    HomeEntitySerializer,
    HomeEntityWriteSerializer,
    RecentReadsQuerySerializer,
    serialize_binding,
)
from connectors.services import (
    ConnectorInvalidTransition,
    ConnectorPermissionDenied,
    list_bindings,
    list_recent_reads,
    set_binding_enabled,
)
from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from workspaces.models import (
    LABEL_ALIAS_MAX_CHARS,
    LABEL_ALIAS_MIN_CHARS,
    MAX_LABEL_ALIASES,
    Workspace,
)
from workspaces.rooms import LabelError


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


class ConnectorRecentReadsView(APIView):
    """`GET /connectors/reads?workspace=<id>[&connector=<name>]` -- the
    Settings "Bağlantılar" page's recent-reads list (Phase 5 branch 9):
    bookkeeping from the `connector.read` audit rows only (tool, outcome,
    size, duration, channel, data class), never arguments or content."""

    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def get(self, request):
        query = RecentReadsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rows = list_recent_reads(
                workspace=workspace,
                actor=request.user,
                connector=query.validated_data.get("connector"),
            )
        except ConnectorPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(rows)


# --- the Home Assistant entity allowlist (Phase 13 D11) ---


def _personal_workspace(request, workspace_id):
    """The actor's PERSONAL workspace through `for_actor()`, or None: a firm
    and another user's workspace both answer 404."""
    return (
        Workspace.objects.for_actor(request.user, workspace=workspace_id)
        .filter(type=Workspace.Type.PERSONAL)
        .first()
    )


_REFUSALS = (home_entities.HomeEntityError, LabelError, ConnectorPermissionDenied)


def _entity_refusal(exc):
    if isinstance(exc, ConnectorPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    return service_error_response(exc, exc.http_status)


class HomeEntityListView(APIView):
    """`GET /api/v1/connectors/home-assistant/entities?workspace=<id>` lists
    the allowlist with the editor's limits and the services it may offer
    per domain (the code maximum narrowed by `HA_ALLOWED_DOMAINS`); `POST`
    allowlists one entity. Owner, human, web channel only (the service
    re-checks `require_owner_web`); a firm or another user's workspace is
    404."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request):
        query = HomeEntityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _personal_workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            home_entities.require_entities_owner_web(
                actor=request.user, workspace=workspace, channel=request_channel(request)
            )
        except _REFUSALS as exc:
            return _entity_refusal(exc)
        rows = home_entities.list_entities(actor=request.user, workspace=workspace)
        return Response(
            {
                "workspace": str(workspace.pk),
                "limits": {
                    "aliases": MAX_LABEL_ALIASES,
                    "alias_min_chars": LABEL_ALIAS_MIN_CHARS,
                    "alias_max_chars": LABEL_ALIAS_MAX_CHARS,
                    "label_max_chars": HOME_ENTITY_LABEL_MAX_CHARS,
                },
                "services": home_entities.code_maximum(),
                "results": HomeEntitySerializer(rows, many=True).data,
            }
        )

    def post(self, request):
        body = HomeEntityWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        workspace = _personal_workspace(request, data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            entity = home_entities.create_entity(
                actor=request.user,
                workspace=workspace,
                channel=request_channel(request),
                entity_id=data["entity_id"],
                label=data["label"],
                services=data["services"],
                room_id=data.get("room_id"),
                aliases=data.get("aliases"),
                enabled=data.get("enabled", True),
            )
        except _REFUSALS as exc:
            return _entity_refusal(exc)
        return Response(HomeEntitySerializer(entity).data, status=status.HTTP_201_CREATED)


class HomeEntityDetailView(APIView):
    """`PATCH` / `DELETE /api/v1/connectors/home-assistant/entities/<id>`.
    The row is found through `for_actor()` and must belong to a personal
    workspace; anything else is 404."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def _entity(self, request, entity_pk):
        return (
            HomeEntity.objects.for_actor(request.user)
            .select_related("workspace", "room")
            .filter(pk=entity_pk, workspace__type=Workspace.Type.PERSONAL)
            .first()
        )

    def patch(self, request, entity_pk):
        entity = self._entity(request, entity_pk)
        if entity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = HomeEntityWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        unset = home_entities.UNSET
        try:
            entity = home_entities.update_entity(
                actor=request.user,
                entity=entity,
                channel=request_channel(request),
                label=data.get("label", unset),
                room_id=data.get("room_id", unset),
                aliases=data.get("aliases", unset),
                services=data.get("services", unset),
                enabled=data.get("enabled", unset),
            )
        except _REFUSALS as exc:
            return _entity_refusal(exc)
        return Response(HomeEntitySerializer(entity).data)

    def delete(self, request, entity_pk):
        entity = self._entity(request, entity_pk)
        if entity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            home_entities.delete_entity(
                actor=request.user, entity=entity, channel=request_channel(request)
            )
        except _REFUSALS as exc:
            return _entity_refusal(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class HomeEntityCandidatesView(APIView):
    """`GET /api/v1/connectors/home-assistant/candidates?workspace=<id>`:
    the editor's pick list from one live, audited `ha_states` read. Reads
    only; nothing is allowlisted until the owner `POST`s an entity."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request):
        query = HomeEntityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _personal_workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            body = home_entities.entity_candidates(
                actor=request.user, workspace=workspace, channel=request_channel(request)
            )
        except _REFUSALS as exc:
            return _entity_refusal(exc)
        return Response(body)
