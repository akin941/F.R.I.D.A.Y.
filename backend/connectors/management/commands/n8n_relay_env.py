"""`manage.py n8n_relay_env` (Phase 12 D10): print the `n8n-relay` sidecar's
tool ceiling from the committed n8n allowlist
(`connectors.manifests.n8n.WORKFLOWS`), as the `deploy/docker/.env` line
compose reads: `N8N_RELAY_ALLOWED_TOOLS=...` becomes the sidecar's
`BRIDGE_ALLOWED_TOOLS` (and, names only, the web/worker setting
`manage.py preflight`'s `n8n_allowlist` row compares with the manifest).

The relay's ceiling is generated from the same table the manifest is
built from, so the second ceiling can never list a workflow the first one
does not. Prints names only -- no URL, no credential."""

from django.core.management.base import BaseCommand

from connectors.manifests.n8n import relay_allowed_tools


class Command(BaseCommand):
    help = "Print the N8N_RELAY_ALLOWED_TOOLS line (the relay ceiling) from the n8n allowlist."

    def handle(self, *args, **options):
        self.stdout.write(f"N8N_RELAY_ALLOWED_TOOLS={relay_allowed_tools()}")
