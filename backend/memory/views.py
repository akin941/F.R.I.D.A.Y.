import uuid

from django.http import JsonResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from memory.core_blocks import (
    CORE_BLOCK_CHAR_BUDGETS,
    CORE_BLOCK_ORDER,
    OWNER_EDITABLE_LABELS,
    labels_for_workspace,
)
from memory.models import CoreMemoryBlock, CoreMemoryBlockVersion, Memory
from memory.serializers import (
    CoreBlockRollbackSerializer,
    CoreBlockUpdateSerializer,
    MemoryCreateSerializer,
    MemoryCurateSerializer,
    MemorySerializer,
)
from memory.services import (
    CoreBlockInvalid,
    DuplicateMemory,
    InvalidTransition,
    MemoryPermissionDenied,
    approve_memory,
    create_memory,
    delete_memory,
    edit_memory,
    forget_memory,
    record_core_block_view,
    record_export,
    record_read,
    reject_memory,
    restore_memory,
    rollback_core_block,
    update_core_block,
)
from workspaces.models import Workspace

_TRANSITIONS = {
    "approve": approve_memory,
    "reject": reject_memory,
    "restore": restore_memory,
    "forget": forget_memory,
}


def _service_error_response(exc):
    if isinstance(exc, MemoryPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, (InvalidTransition, DuplicateMemory)):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _resolve_workspace(request):
    """Validate the `workspace` query param and return (workspace, error)."""
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        return None, Response(
            {"detail": "workspace query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
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


class MemoryListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        memories = Memory.objects.for_actor(request.user, workspace=workspace)
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in Memory.Status.values:
                return Response(
                    {"detail": "invalid status filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            memories = memories.filter(status=status_filter)
        type_filter = request.query_params.get("type")
        if type_filter:
            if type_filter not in Memory.Type.values:
                return Response(
                    {"detail": "invalid type filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            memories = memories.filter(memory_type=type_filter)
        memories = list(memories.order_by("-updated_at", "-id"))
        record_read(actor=request.user, workspace=workspace, memories=memories)
        return Response(MemorySerializer(memories, many=True).data)

    def post(self, request):
        serializer = MemoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            memory = create_memory(
                workspace=workspace,
                actor=request.user,
                title=serializer.validated_data["title"],
                content=serializer.validated_data["content"],
                memory_type=serializer.validated_data["memory_type"],
                sensitivity=serializer.validated_data["sensitivity"],
                proposal=serializer.validated_data["proposal"],
            )
        except (MemoryPermissionDenied, InvalidTransition, DuplicateMemory) as exc:
            return _service_error_response(exc)
        return Response(MemorySerializer(memory).data, status=status.HTTP_201_CREATED)


class MemoryDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def _get_memory(self, request, memory_id):
        # Out-of-scope rows → 404, never 403 (existence must not leak).
        return Memory.objects.for_actor(request.user).filter(id=memory_id).first()

    def get(self, request, memory_id):
        memory = self._get_memory(request, memory_id)
        if memory is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        record_read(actor=request.user, workspace=memory.workspace, memories=[memory])
        return Response(MemorySerializer(memory).data)

    def patch(self, request, memory_id):
        memory = self._get_memory(request, memory_id)
        if memory is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = MemoryCurateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            memory = edit_memory(memory=memory, actor=request.user, **serializer.validated_data)
        except (MemoryPermissionDenied, InvalidTransition, DuplicateMemory) as exc:
            return _service_error_response(exc)
        return Response(MemorySerializer(memory).data)

    def delete(self, request, memory_id):
        memory = self._get_memory(request, memory_id)
        if memory is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            delete_memory(memory=memory, actor=request.user, channel=request_channel(request))
        except (MemoryPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemoryTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, memory_id, action):
        transition = _TRANSITIONS.get(action)
        if transition is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        memory = Memory.objects.for_actor(request.user).filter(id=memory_id).first()
        if memory is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        kwargs = {}
        if action == "approve":
            serializer = MemoryCurateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            kwargs = serializer.validated_data
        try:
            memory = transition(memory=memory, actor=request.user, **kwargs)
        except (MemoryPermissionDenied, InvalidTransition, DuplicateMemory) as exc:
            return _service_error_response(exc)
        return Response(MemorySerializer(memory).data)


class MemoryExportView(APIView):
    """Portable export (ADR-005 "users can export everything"): exactly the
    rows the actor can read — every status, including private and archived.
    A scoped read returned in the HTTP response, so no approval gate applies.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        memories = list(
            Memory.objects.for_actor(request.user, workspace=workspace).order_by("created_at", "id")
        )
        record_export(actor=request.user, workspace=workspace, memories=memories)
        response = JsonResponse(
            {
                "workspace": str(workspace.id),
                "count": len(memories),
                "memories": MemorySerializer(memories, many=True).data,
            }
        )
        # Exports include private content; they must never be cached anywhere.
        response["Cache-Control"] = "no-store"
        response["Content-Disposition"] = 'attachment; filename="friday-memories.json"'
        return response


# --- Core memory blocks (Phase 8A D7) ---
#
# Owner only, trusted web session only (no PAT allowlist: a cli/openai
# token is refused by authentication itself), `for_actor()` workspace
# resolution (another workspace -> 404). `self` is listed read-only and can
# never be written here: its only writer is the deterministic assembler.

_CORE_BLOCK_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]


def _core_block_error_response(exc):
    if isinstance(exc, CoreBlockInvalid):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    return _service_error_response(exc)


def _block_payload(label, block):
    return {
        "label": label,
        "content": block.content if block else "",
        "version": block.version if block else 0,
        "sensitivity": block.sensitivity if block else Memory.Sensitivity.NORMAL,
        "updated_by_kind": block.updated_by_kind if block else None,
        "updated_at": block.updated_at.isoformat() if block else None,
        "char_budget": CORE_BLOCK_CHAR_BUDGETS[label],
        "editable": label in OWNER_EDITABLE_LABELS,
    }


class CoreBlockListView(APIView):
    permission_classes = _CORE_BLOCK_PERMISSIONS

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        blocks = {
            b.label: b for b in CoreMemoryBlock.objects.for_actor(request.user, workspace=workspace)
        }
        allowed = labels_for_workspace(workspace)
        labels = [label for label in CORE_BLOCK_ORDER if label in allowed]
        record_core_block_view(actor=request.user, workspace=workspace, labels=labels)
        return Response([_block_payload(label, blocks.get(label)) for label in labels])


class CoreBlockDetailView(APIView):
    permission_classes = _CORE_BLOCK_PERMISSIONS

    def get(self, request, label):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        if label not in labels_for_workspace(workspace):
            return Response(status=status.HTTP_404_NOT_FOUND)
        block = (
            CoreMemoryBlock.objects.for_actor(request.user, workspace=workspace)
            .filter(label=label)
            .first()
        )
        record_core_block_view(actor=request.user, workspace=workspace, labels=[label])
        return Response(_block_payload(label, block))

    def patch(self, request, label):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        serializer = CoreBlockUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            block = update_core_block(
                workspace=workspace,
                label=label,
                actor=request.user,
                channel=request_channel(request),
                **serializer.validated_data,
            )
        except (MemoryPermissionDenied, InvalidTransition, CoreBlockInvalid) as exc:
            return _core_block_error_response(exc)
        return Response(_block_payload(label, block))


class CoreBlockVersionListView(APIView):
    permission_classes = _CORE_BLOCK_PERMISSIONS

    def get(self, request, label):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        if label not in labels_for_workspace(workspace):
            return Response(status=status.HTTP_404_NOT_FOUND)
        versions = CoreMemoryBlockVersion.objects.for_actor(
            request.user, workspace=workspace
        ).filter(block__label=label)
        record_core_block_view(actor=request.user, workspace=workspace, labels=[label])
        return Response(
            [
                {
                    "version": v.version,
                    "content": v.content,
                    "sensitivity": v.sensitivity,
                    "updated_by_kind": v.updated_by_kind,
                    "created_at": v.created_at.isoformat(),
                }
                for v in versions.order_by("-version")
            ]
        )


class CoreBlockRollbackView(APIView):
    permission_classes = _CORE_BLOCK_PERMISSIONS

    def post(self, request, label):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        serializer = CoreBlockRollbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            block = rollback_core_block(
                workspace=workspace,
                label=label,
                version=serializer.validated_data["version"],
                actor=request.user,
                channel=request_channel(request),
            )
        except (MemoryPermissionDenied, InvalidTransition, CoreBlockInvalid) as exc:
            return _core_block_error_response(exc)
        return Response(_block_payload(label, block))
