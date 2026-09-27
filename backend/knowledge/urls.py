from django.urls import path

from knowledge.views import (
    KnowledgeDocumentDetailView,
    KnowledgeDocumentListView,
    KnowledgeDocumentRestoreView,
    KnowledgeDocumentTrashListView,
    KnowledgeFileUploadCreateView,
    KnowledgePastedTextCreateView,
    KnowledgeUrlCreateView,
)

urlpatterns = [
    path(
        "knowledge/documents",
        KnowledgeDocumentListView.as_view(),
        name="knowledge-document-list",
    ),
    path(
        "knowledge/documents/pasted-text",
        KnowledgePastedTextCreateView.as_view(),
        name="knowledge-document-create-pasted-text",
    ),
    path(
        "knowledge/documents/file-upload",
        KnowledgeFileUploadCreateView.as_view(),
        name="knowledge-document-create-file-upload",
    ),
    path(
        "knowledge/documents/url",
        KnowledgeUrlCreateView.as_view(),
        name="knowledge-document-create-url",
    ),
    path(
        "knowledge/documents/trash",
        KnowledgeDocumentTrashListView.as_view(),
        name="knowledge-document-trash",
    ),
    path(
        "knowledge/documents/<uuid:document_id>/restore",
        KnowledgeDocumentRestoreView.as_view(),
        name="knowledge-document-restore",
    ),
    path(
        "knowledge/documents/<uuid:document_id>",
        KnowledgeDocumentDetailView.as_view(),
        name="knowledge-document-detail",
    ),
]
