import uuid

from django.conf import settings
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsWebChannel
from core.throttling import CreateScopedRateThrottle
from core.uploads import MaxSizeUploadHandler
from knowledge.models import KnowledgeDocument
from knowledge.serializers import (
    FileUploadCreateSerializer,
    KnowledgeDocumentSerializer,
    PastedTextCreateSerializer,
    UrlCreateSerializer,
)
from knowledge.services import (
    DuplicateDocument,
    InvalidUpload,
    KnowledgePermissionDenied,
    create_file_upload_document,
    create_pasted_text_document,
    create_url_document,
    delete_document,
    record_read,
    restore_document,
)
from workspaces.models import Workspace


def _service_error_response(exc):
    if isinstance(exc, KnowledgePermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, (DuplicateDocument, InvalidUpload)):
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


class KnowledgeDocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        documents = KnowledgeDocument.objects.for_actor(request.user, workspace=workspace)
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in KnowledgeDocument.Status.values:
                return Response(
                    {"detail": "invalid status filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            documents = documents.filter(status=status_filter)
        documents = list(documents.order_by("-updated_at", "-id"))
        record_read(actor=request.user, workspace=workspace, documents=documents)
        return Response(KnowledgeDocumentSerializer(documents, many=True).data)


def _oversized_upload_response():
    return Response(
        {"detail": f"File exceeds the {settings.KNOWLEDGE_MAX_UPLOAD_BYTES}-byte upload limit."},
        status=status.HTTP_400_BAD_REQUEST,
    )


class KnowledgePastedTextCreateView(APIView):
    """No pre-parse Content-Length guard here, unlike the file-upload
    endpoint below — a prior attempt at one used a fixed byte allowance for
    JSON-escaping overhead, but `\\uXXXX`-escaped non-ASCII content (emoji,
    Unicode text) can legitimately expand 3-6x on the wire relative to its
    decoded size, so any fixed or naively-scaled threshold either lets
    oversized ASCII payloads through or falsely rejects valid non-ASCII ones
    (review finding). A JSON body has no equivalent to a multipart file's
    unbounded-disk-write risk — Django's global DATA_UPLOAD_MAX_MEMORY_SIZE
    already bounds it in memory — so the definitive, decoded-content check
    in create_pasted_text_document is enough on its own here.
    """

    permission_classes = [IsAuthenticated]
    # Ingestion embeds every chunk (cost-bearing).
    throttle_classes = [CreateScopedRateThrottle]
    throttle_scope = "cost"

    def post(self, request):
        serializer = PastedTextCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            document = create_pasted_text_document(
                workspace=workspace,
                actor=request.user,
                title=serializer.validated_data["title"],
                content=serializer.validated_data["content"],
                sensitivity=serializer.validated_data["sensitivity"],
            )
        except (KnowledgePermissionDenied, DuplicateDocument, InvalidUpload) as exc:
            return _service_error_response(exc)
        return Response(KnowledgeDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class KnowledgeUrlCreateView(APIView):
    """Creates a `pending` document with no content yet — the actual fetch
    (network I/O) happens in the branch-3 ingestion worker, never in this
    request/response path (brief §3: "a slow or hostile URL must not tie up
    a web worker"). Nothing here is a security boundary; `knowledge.url_fetch`
    re-validates everything from scratch in the worker."""

    permission_classes = [IsAuthenticated]
    # Ingestion embeds every chunk (cost-bearing).
    throttle_classes = [CreateScopedRateThrottle]
    throttle_scope = "cost"

    def post(self, request):
        serializer = UrlCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            document = create_url_document(
                workspace=workspace,
                actor=request.user,
                title=serializer.validated_data["title"],
                source_url=serializer.validated_data["source_url"],
                sensitivity=serializer.validated_data["sensitivity"],
            )
        except KnowledgePermissionDenied as exc:
            return _service_error_response(exc)
        return Response(KnowledgeDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class KnowledgeFileUploadCreateView(APIView):
    permission_classes = [IsAuthenticated]
    # Ingestion embeds every chunk (cost-bearing).
    throttle_classes = [CreateScopedRateThrottle]
    throttle_scope = "cost"
    parser_classes = [MultiPartParser]

    def post(self, request):
        size_guard = MaxSizeUploadHandler(request, max_size=settings.KNOWLEDGE_MAX_UPLOAD_BYTES)
        request.upload_handlers = [size_guard, *request.upload_handlers]

        serializer = FileUploadCreateSerializer(data=request.data)
        if size_guard.aborted:
            return _oversized_upload_response()
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        uploaded_file = serializer.validated_data["file"]
        try:
            document = create_file_upload_document(
                workspace=workspace,
                actor=request.user,
                title=serializer.validated_data["title"],
                file_bytes=uploaded_file.read(),
                mime_type=uploaded_file.content_type or "",
                filename=uploaded_file.name or "",
                sensitivity=serializer.validated_data["sensitivity"],
            )
        except (KnowledgePermissionDenied, DuplicateDocument, InvalidUpload) as exc:
            return _service_error_response(exc)
        return Response(KnowledgeDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class KnowledgeDocumentDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def _get_document(self, request, document_id):
        # Out-of-scope rows → 404, never 403 (existence must not leak).
        return KnowledgeDocument.objects.for_actor(request.user).filter(id=document_id).first()

    def get(self, request, document_id):
        document = self._get_document(request, document_id)
        if document is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        record_read(actor=request.user, workspace=document.workspace, documents=[document])
        return Response(KnowledgeDocumentSerializer(document).data)

    def delete(self, request, document_id):
        document = self._get_document(request, document_id)
        if document is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            delete_document(document=document, actor=request.user, channel=request_channel(request))
        except KnowledgePermissionDenied as exc:
            return _service_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class KnowledgeDocumentTrashListView(APIView):
    """`GET /api/v1/knowledge/documents/trash?workspace=` -- trashed
    documents ("Çöp kutusu", ADR-009), newest deletion first."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        documents = list(
            KnowledgeDocument.all_objects.for_actor(request.user, workspace=workspace)
            .filter(deleted_at__isnull=False)
            .order_by("-deleted_at", "-id")
        )
        record_read(actor=request.user, workspace=workspace, documents=documents)
        return Response(KnowledgeDocumentSerializer(documents, many=True).data)


class KnowledgeDocumentRestoreView(APIView):
    """`POST /api/v1/knowledge/documents/<id>/restore`."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def post(self, request, document_id):
        document = (
            KnowledgeDocument.all_objects.for_actor(request.user)
            .filter(id=document_id, deleted_at__isnull=False)
            .first()
        )
        if document is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            document = restore_document(
                document=document, actor=request.user, channel=request_channel(request)
            )
        except (KnowledgePermissionDenied, DuplicateDocument, InvalidUpload) as exc:
            return _service_error_response(exc)
        return Response(KnowledgeDocumentSerializer(document).data)
