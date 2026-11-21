"""Learning loop: the morning card API (Phase 8A brief D10).

Owner only, trusted web session only, default authentication (a PAT is
refused by authentication itself -> 401), `for_actor()` + owner-role lookups
(another workspace's item -> 404). Decisions reuse the memory and core-block
services one item at a time (`memory.ingestion.digest`); bulk approval
exists only for `work_log` items. Phase 8B: fact proposals (kind `fact`) are
approved, edited or rejected through the graph services; a supersede whose
target changed or would run backwards is a 409 with the graph's stable code.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from memory.graph import services as graph_services
from memory.graph.serializers import FactEditSerializer, serialize_fact
from memory.ingestion import digest
from memory.serializers import (
    LearningBulkApproveSerializer,
    LearningDigestQuerySerializer,
    MemoryCurateSerializer,
    MemorySerializer,
)
from memory.services import (
    CoreBlockInvalid,
    DuplicateMemory,
    InvalidTransition,
    MemoryPermissionDenied,
)

_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]
_ERRORS = (
    MemoryPermissionDenied,
    InvalidTransition,
    DuplicateMemory,
    CoreBlockInvalid,
    graph_services.GraphError,
)
_CONFLICTS = (graph_services.SupersedeOrder, graph_services.SupersedeTargetChanged)


def _error(exc):
    if isinstance(exc, MemoryPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, _CONFLICTS):
        return service_error_response(exc, status.HTTP_409_CONFLICT)
    if isinstance(exc, graph_services.SystemRowImmutable):
        return service_error_response(exc, status.HTTP_405_METHOD_NOT_ALLOWED)
    return service_error_response(exc, status.HTTP_400_BAD_REQUEST)


def _item_payload(kind, row):
    if kind == digest.KIND_MEMORY:
        return MemorySerializer(row).data
    if kind == digest.KIND_FACT:
        return serialize_fact(row)
    return {
        "id": str(row.id),
        "label": row.label,
        "status": row.status,
        "proposed_content": row.proposed_content,
        "provenance_quote": row.provenance_quote,
        "base_version": row.base_version,
    }


class LearningDigestView(APIView):
    """GET /memory/learning/digest[?days=]: the morning card."""

    permission_classes = _PERMISSIONS

    def get(self, request):
        query = LearningDigestQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            payload = digest.build_digest(
                actor=request.user,
                channel=request_channel(request),
                days=query.validated_data.get("days"),
            )
        except _ERRORS as exc:
            return _error(exc)
        return Response(payload)


class LearningItemActionView(APIView):
    """POST /memory/learning/proposals/<kind>/<id>/<action>: approve | edit |
    reject for a memory proposal (body: optional curated fields), approve |
    reject for a core-block proposal (no body)."""

    permission_classes = _PERMISSIONS

    def post(self, request, kind, item_id, action):
        if kind == digest.KIND_MEMORY:
            allowed = digest.MEMORY_ACTIONS
        elif kind == digest.KIND_CORE_BLOCK:
            allowed = digest.CORE_BLOCK_ACTIONS
        elif kind == digest.KIND_FACT:
            allowed = digest.FACT_ACTIONS
        else:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if action not in allowed:
            return Response(status=status.HTTP_404_NOT_FOUND)
        item = digest.find_item(request.user, kind, item_id)
        if item is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        fields = {}
        if kind == digest.KIND_MEMORY and action in ("approve", "edit"):
            serializer = MemoryCurateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            fields = serializer.validated_data
        elif kind == digest.KIND_FACT and action == "edit":
            serializer = FactEditSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            fields = serializer.validated_data
        try:
            row = digest.decide(
                actor=request.user,
                channel=request_channel(request),
                kind=kind,
                item=item,
                action=action,
                fields=fields,
            )
        except _ERRORS as exc:
            return _error(exc)
        return Response(_item_payload(kind, row))


class LearningBulkApproveView(APIView):
    """POST /memory/learning/bulk-approve {"ids": [...]}: work_log only."""

    permission_classes = _PERMISSIONS

    def post(self, request):
        body = LearningBulkApproveSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            rows = digest.bulk_approve(
                actor=request.user,
                channel=request_channel(request),
                memory_ids=body.validated_data["ids"],
            )
        except _ERRORS as exc:
            return _error(exc)
        return Response({"approved": [str(r.id) for r in rows]})
