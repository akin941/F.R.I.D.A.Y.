"""The hostile fake gateway on localhost (Phase 9 brief D5, Phase 10 brief
branch 2; Mac session M1 live conformance).

    manage.py node_conformance_server [--protocol 1|2] [--case NAME ...]
    manage.py node_conformance_server --external --port 8765 --tls-self-signed \\
        --protocol 2 --hook-socket /tmp/friday-conformance-hook.sock

Without `--external` it plays every case against the Python reference node
(spawned in this process, over a real localhost `wsproto` socket) and prints
a pass/fail table. With `--external` it waits for a separately running node
(the Swift `Conformance` build): the node connects to the hook socket, and
for each case the gateway sends `prepare` over the hook, accepts the node's
WebSocket connection and plays the case (see `nodes.conformance.gateway`).
`--protocol` limits the run to one version's cases (both by default). Exits
non-zero unless every case passes. Uses only the conformance key pair; no
database, no real gateway, no network beyond localhost."""

import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from nodes.conformance.cases import all_cases
from nodes.conformance.gateway import (
    ConformanceGateway,
    ExternalHook,
    play_external,
    run_case_over_socket,
    self_signed_context,
)
from nodes.conformance.runner import format_table
from nodes.reference.fixtures.factory import build_fixture_home


class Command(BaseCommand):
    help = "Play the node conformance suite as a hostile fake gateway on localhost."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=0)
        parser.add_argument("--protocol", type=int, choices=(1, 2), default=None)
        parser.add_argument("--case", action="append", default=[])
        parser.add_argument("--tls-self-signed", action="store_true")
        parser.add_argument("--external", action="store_true")
        parser.add_argument("--hook-socket", default=None)

    def _cases(self, options):
        versions = (options["protocol"],) if options["protocol"] else (1, 2)
        cases = [case for version in versions for case in all_cases()[version]]
        if options["case"]:
            wanted = set(options["case"])
            cases = [case for case in cases if case["name"] in wanted]
            missing = wanted - {case["name"] for case in cases}
            if missing:
                raise CommandError(f"unknown case(s): {', '.join(sorted(missing))}")
        return cases

    def handle(self, *args, **options):
        cases = self._cases(options)
        server_ssl = client_ssl = None
        if options["tls_self_signed"]:
            server_ssl, client_ssl, cert_path = self_signed_context()
            self.stdout.write(f"TLS certificate (pin it in the conformance build): {cert_path}")
        gateway = ConformanceGateway(port=options["port"], ssl_context=server_ssl)
        self.stdout.write(f"fake gateway listening on 127.0.0.1:{gateway.port}")
        results = []
        try:
            if options["external"]:
                hook_path = options["hook_socket"] or str(
                    Path(tempfile.mkdtemp()) / "friday-conformance-hook.sock"
                )
                hook = ExternalHook(hook_path)
                self.stdout.write(f"waiting for the node on the hook socket {hook_path}")
                hook.wait_for_node()
                try:
                    results = [play_external(gateway, hook, case) for case in cases]
                finally:
                    hook.close()
            else:
                base = Path(tempfile.mkdtemp(prefix="friday-conformance-"))
                home = build_fixture_home(base)
                worktrees = base / "worktrees"
                worktrees.mkdir()
                for case in cases:
                    results.append(
                        run_case_over_socket(
                            gateway, case, home=home, worktree_base=worktrees, client_ssl=client_ssl
                        )
                    )
        finally:
            gateway.close()
        self.stdout.write(format_table(results))
        if not results or not all(result.passed for result in results):
            raise CommandError("node conformance failed")
