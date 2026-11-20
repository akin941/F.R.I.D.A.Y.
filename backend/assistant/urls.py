from django.urls import path

from assistant.self_views import SelfSourcesView
from assistant.views import (
    ConversationDetailView,
    ConversationListCreateView,
    ConversationMessageListView,
)

urlpatterns = [
    # Phase 8A branch 6: the `self` block's source anchors (owner, web).
    path("self-sources", SelfSourcesView.as_view(), name="assistant-self-sources"),
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
