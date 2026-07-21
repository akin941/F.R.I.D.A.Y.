from django.contrib import admin

from accounts.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["email", "name", "is_agent", "is_active", "is_staff", "created_at"]
    list_filter = ["is_agent", "is_active", "is_staff"]
    search_fields = ["email", "name"]
    ordering = ["-created_at"]
    # Password changes go through createsuperuser/CLI in dev; the hash is not editable here.
    exclude = ["password"]
    readonly_fields = ["last_login", "created_at", "updated_at"]
