from django.contrib import admin

from accounts.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["email", "name", "is_owner", "is_agent", "is_active", "is_staff", "created_at"]
    list_filter = ["is_owner", "is_agent", "is_active", "is_staff"]
    search_fields = ["email", "name"]
    ordering = ["-created_at"]
    # Password changes go through createsuperuser/CLI in dev; the hash is not editable here.
    exclude = ["password"]
    # Ownership moves only through `manage.py set_owner`, which revokes the
    # outgoing owner's devices and remote key; editing the flag here would
    # skip that and leave their trusted devices live.
    readonly_fields = ["is_owner", "last_login", "created_at", "updated_at"]
