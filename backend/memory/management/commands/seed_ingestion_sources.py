"""`manage.py seed_ingestion_sources` (Phase 8A brief D2): create the four
default ingestion sources (git, harness, claude_code_hooks, calendar) in the
installation owner's personal workspace, **disabled**. Idempotent: existing
rows are never enabled, paused or otherwise changed. The owner enables each
source from the web (d5)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from memory.ingestion.services import ensure_default_sources
from workspaces.models import Workspace


class Command(BaseCommand):
    help = "Create the four Phase 8A ingestion sources (disabled) for the owner."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="The owner's e-mail (default: the installation owner).")

    def handle(self, *args, **options):
        owners = get_user_model().objects.filter(is_owner=True, is_agent=False, is_active=True)
        if options.get("email"):
            owners = owners.filter(email__iexact=options["email"])
        owner = owners.order_by("created_at").first()
        if owner is None:
            raise CommandError("No installation owner found (run bootstrap_owner first).")
        workspace = (
            Workspace.objects.filter(owner=owner, type=Workspace.Type.PERSONAL)
            .order_by("created_at")
            .first()
        )
        if workspace is None:
            raise CommandError("The owner has no personal workspace.")
        created = ensure_default_sources(workspace=workspace, created_by=owner)
        kinds = ", ".join(s.kind for s in created) or "none"
        self.stdout.write(f"Ingestion sources created (disabled): {kinds}.")
