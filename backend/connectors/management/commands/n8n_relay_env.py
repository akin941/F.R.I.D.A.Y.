"""`manage.py n8n_relay_env` (Phase 12 D10): print the `n8n-relay` sidecar's
`BRIDGE_ALLOWED_TOOLS` line from the committed n8n allowlist
(`connectors.manifests.n8n.WORKFLOWS`), for `deploy/docker/.env`.

The relay's ceiling is generated from the same table the manifest is
built from, so the second ceiling can never list a workflow the first one
does not. Prints names only -- no URL, no credential."""

from django.core.management.base import BaseCommand

from connectors.manifests.n8n import relay_allowed_tools


class Command(BaseCommand):
    help = "Print the n8n relay sidecar's BRIDGE_ALLOWED_TOOLS line from the n8n allowlist."

    def handle(self, *args, **options):
        self.stdout.write(f"BRIDGE_ALLOWED_TOOLS={relay_allowed_tools()}")
