"""Render or check the committed conformance files (Phase 9 brief D5,
Phase 10 brief branch 2).

    manage.py build_node_conformance          # rewrite cases/ and vectors/
    manage.py build_node_conformance --check  # CI: non-zero exit on drift

Covers `nodes/conformance/cases/v1/`, `cases/v2/`,
`vectors/sandbox.json`, `vectors/scrub_stream.json`, `vectors/scrub.json` and
`vectors/owner_turns.json`, and the vendored
node scrubber (`nodes/reference/_scrubber_vendored.py`)."""

from django.core.management.base import BaseCommand, CommandError

from nodes.conformance import build
from nodes.reference import vendor_scrubber


class Command(BaseCommand):
    help = "Render (or --check) the node conformance case and vector files."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="fail when a file drifts")

    def handle(self, *args, **options):
        if options["check"]:
            drift = build.check()
            if vendor_scrubber.main(["--check"]) != 0:
                drift.append("reference/_scrubber_vendored.py")
            if drift:
                raise CommandError(
                    "node conformance files are stale (run build_node_conformance): "
                    + ", ".join(drift)
                )
            self.stdout.write("node conformance files are current")
            return
        vendor_scrubber.main([])
        written = build.write()
        self.stdout.write(f"wrote {len(written)} node conformance file(s)")
