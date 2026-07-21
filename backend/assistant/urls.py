from django.urls import path

from assistant.views import (
    ConversationDetailView,
    ConversationListCreateView,
    ConversationMessageListView,
)

urlpatterns = [
    path(
        "conversations",
        ConversationListCreateView.as_view(),
        name="assistant-conversation-list-create",
    ),
    path(
        "conversations/<uuid:conversation_id>",
        ConversationDetailView.as_view(),
        name="assistant-conversation-detail",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages",
        ConversationMessageListView.as_view(),
        name="assistant-conversation-messages",
    ),
]
