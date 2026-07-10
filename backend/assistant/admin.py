from django.contrib import admin

from assistant.models import Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "workspace", "created_by", "created_at"]
    list_filter = ["workspace"]
    search_fields = ["title", "created_by__email", "workspace__name"]
    autocomplete_fields = ["workspace", "created_by"]
    ordering = ["-created_at"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "conversation",
        "workspace",
        "role",
        "status",
        "model_usage",
        "created_by",
        "created_at",
    ]
    list_filter = ["role", "status", "workspace"]
    search_fields = ["content", "conversation__title", "created_by__email"]
    autocomplete_fields = ["conversation", "workspace", "created_by", "model_usage"]
    ordering = ["-created_at"]
