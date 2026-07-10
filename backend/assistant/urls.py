from django.urls import path

from assistant.views import ConversationListCreateView, ConversationMessageListView

urlpatterns = [
    path(
        "conversations",
        ConversationListCreateView.as_view(),
        name="assistant-conversation-list-create",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages",
        ConversationMessageListView.as_view(),
        name="assistant-conversation-messages",
    ),
]
