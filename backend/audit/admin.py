from django.contrib import admin

from audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only viewer. Immutability itself is enforced by the DB trigger."""

    list_display = [
        "created_at",
        "actor",
        "actor_type",
        "workspace",
        "action",
        "resource_type",
        "resource_id",
        "request_id",
    ]
    list_filter = ["action", "actor_type", "workspace"]
    search_fields = ["action", "resource_type", "resource_id", "request_id"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
