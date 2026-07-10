from django.contrib import admin

from workspaces.models import Workspace, WorkspaceMember


class WorkspaceMemberInline(admin.TabularInline):
    model = WorkspaceMember
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "owner", "created_at"]
    list_filter = ["type"]
    search_fields = ["name", "owner__email"]
    inlines = [WorkspaceMemberInline]


@admin.register(WorkspaceMember)
class WorkspaceMemberAdmin(admin.ModelAdmin):
    list_display = ["workspace", "user", "role", "created_at"]
    list_filter = ["role"]
    search_fields = ["workspace__name", "user__email"]
    autocomplete_fields = ["workspace", "user"]
