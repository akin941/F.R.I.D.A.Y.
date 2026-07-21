from django.contrib import admin

from assistant.admin import ConversationAdmin, MessageAdmin
from assistant.models import Conversation, Message


def test_conversation_registered_with_expected_config():
    assert admin.site.is_registered(Conversation)
    model_admin = admin.site._registry[Conversation]
    assert isinstance(model_admin, ConversationAdmin)
    assert "title" in model_admin.list_display
    assert "workspace" in model_admin.list_filter
    assert "title" in model_admin.search_fields
    assert "workspace" in model_admin.autocomplete_fields


def test_message_registered_with_expected_config():
    assert admin.site.is_registered(Message)
    model_admin = admin.site._registry[Message]
    assert isinstance(model_admin, MessageAdmin)
    assert "role" in model_admin.list_filter
    assert "status" in model_admin.list_filter
    assert "content" in model_admin.search_fields
    assert "conversation" in model_admin.autocomplete_fields
