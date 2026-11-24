"""Phase 12 D8/D20: the wake routes and their owner aliases (owner, web).

Default authentication (the trusted device's web session): a PAT, a node
secret or a node voice token is refused by authentication itself (401).
The permission classes refuse every other web principal (an agent, a
member, a non-owner: 403), and the alias services check the workspace
owner over the web again (`core.owner_web`). Workspaces are resolved
through `for_actor()`; another workspace is a 404. No voice turn, tool,
skill, model or connector reaches these views or their services.

- `GET    /api/v1/assistant/wake-routes[?workspace=]`
- `POST   /api/v1/assistant/wake-routes/aliases[?workspace=]` `{"route", "phrase"}`
- `PATCH  /api/v1/assistant/wake-routes/aliases/<id>` `{"phrase"}`
- `DELETE /api/v1/assistant/wake-routes/aliases/<id>`
"""

import uuid

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant import wake_routes
from assistant.models import WakeRouteAlias
from assistant.wake_aliases import (
    WakeAliasError,
    WakeAliasPermissionDenied,
    create_wake_alias,
    delete_wake_alias,
    update_wake_alias,
)
from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from workspaces.models import Workspace

_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]


class AliasCreateSerializer(serializers.Serializer):
    route = serializers.CharField(max_length=64)
    phrase = serializers.CharField(max_length=200, trim_whitespace=True)


class AliasUpdateSerializer(serializers.Serializer):
    phrase = serializers.CharField(max_length=200, trim_whitespace=True)


def alias_payload(alias):
    return {
        "id": str(alias.pk),
        "route": alias.route,
        "phrase": alias.phrase,
        "created_at": alias.created_at.isoformat(),
    }


def _workspace(request):
    raw = request.query_params.get("workspace")
    workspaces = Workspace.objects.for_actor(request.user)
    if raw:
        try:
            uuid.UUID(str(raw))
        except ValueError:
            return None
        return workspaces.filter(pk=raw).first()
    return (
        workspaces.filter(type=Workspace.Type.PERSONAL, owner=request.user)
        .order_by("created_at")
        .first()
    )


def _alias(request, alias_id):
    return (
        WakeRouteAlias.objects.for_actor(request.user)
        .select_related("workspace")
        .filter(pk=alias_id)
        .first()
    )


def _error(exc):
    if isinstance(exc, WakeAliasPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    return service_error_response(exc, status.HTTP_400_BAD_REQUEST)


def routes_payload(workspace):
    aliases = {}
    rows = WakeRouteAlias.objects.filter(workspace=workspace).order_by("created_at", "id")
    for row in rows:
        aliases.setdefault(row.route, []).append(alias_payload(row))
    return {
        "workspace": str(workspace.pk),
        "limits": {
            "aliases_per_route": wake_routes.MAX_ALIASES_PER_ROUTE,
            "min_chars": wake_routes.ALIAS_MIN_CHARS,
            "max_chars": wake_routes.ALIAS_MAX_CHARS,
        },
        "routes": [
            {
                "name": route.name,
                "effect": route.effect,
                "phrases": list(route.default_phrases),
                "aliases": aliases.get(route.name, []),
            }
            for route in wake_routes.ROUTES
        ]
        # Phase 12 branch 5: one `skill.<name>` route per usable skill,
        # aliases only (no default phrase), matched as a leading alias.
        + [
            {
                "name": name,
                "effect": wake_routes.EFFECT_READ,
                "phrases": [],
                "aliases": aliases.get(name, []),
            }
            for name in wake_routes.skill_routes(workspace.owner, workspace)
        ],
    }


class WakeRouteListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        workspace = _workspace(request)
        if workspace is None or str(workspace.owner_id) != str(request.user.pk):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(routes_payload(workspace))


class WakeAliasCreateView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request):
        workspace = _workspace(request)
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = AliasCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            alias = create_wake_alias(
                actor=request.user,
                channel=request_channel(request),
                workspace=workspace,
                route=serializer.validated_data["route"],
                phrase=serializer.validated_data["phrase"],
            )
        except (WakeAliasError, WakeAliasPermissionDenied) as exc:
            return _error(exc)
        return Response(alias_payload(alias), status=status.HTTP_201_CREATED)


class WakeAliasDetailView(APIView):
    permission_classes = _PERMISSIONS

    def patch(self, request, alias_id):
        alias = _alias(request, alias_id)
        if alias is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = AliasUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            alias = update_wake_alias(
                actor=request.user,
                channel=request_channel(request),
                alias=alias,
                phrase=serializer.validated_data["phrase"],
            )
        except (WakeAliasError, WakeAliasPermissionDenied) as exc:
            return _error(exc)
        return Response(alias_payload(alias))

    def delete(self, request, alias_id):
        alias = _alias(request, alias_id)
        if alias is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            delete_wake_alias(actor=request.user, channel=request_channel(request), alias=alias)
        except WakeAliasPermissionDenied as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)
