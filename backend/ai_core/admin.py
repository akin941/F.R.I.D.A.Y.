from django.contrib import admin

from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion


@admin.register(ModelProvider)
class ModelProviderAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "provider_type",
        "capability",
        "dimension",
        "model_id",
        "priority",
        "is_active",
        "input_price_per_mtok",
        "output_price_per_mtok",
    ]
    list_filter = ["provider_type", "capability", "is_active"]
    search_fields = ["name", "model_id"]


class PromptVersionInline(admin.TabularInline):
    model = PromptVersion
    extra = 0
    fields = ["version", "content", "model_params", "changelog", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "purpose", "active_version"]
    search_fields = ["name", "purpose"]
    inlines = [PromptVersionInline]


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    """Prompt discipline: never edit a live version's content in place — add a
    new version and repoint active_version (see docs/prompts conventions)."""

    list_display = ["template", "version", "changelog", "created_at"]
    list_filter = ["template"]
    ordering = ["template", "-version"]


@admin.register(ModelUsage)
class ModelUsageAdmin(admin.ModelAdmin):
    """Fully read-only: usage is accounting data, never edited by hand."""

    list_display = [
        "created_at",
        "workspace",
        "user",
        "purpose",
        "provider",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "success",
        "error_type",
    ]
    list_filter = ["purpose", "success", "provider", "backend"]
    search_fields = ["purpose", "request_id", "error_type"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
