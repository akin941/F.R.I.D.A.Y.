"""`manage.py build_vector_indexes [--check]` (Phase 8B branch 4, brief D8).

Creates one partial HNSW expression index per active embedding provider
and table (`memory.retrieval.indexes`). Idempotent; also run by
`post_migrate`. `--check` writes nothing and exits non-zero when an index
is missing or stale; a provider above the HNSW dimension limit is reported
as `skipped_dimension` (a warning, not a failure: retrieval over it is an
exact scan).
"""

from django.core.management.base import BaseCommand, CommandError

from memory.retrieval.indexes import (
    MISSING,
    SKIPPED_DIMENSION,
    SKIPPED_NO_DIMENSION,
    STALE,
    build_vector_indexes,
    index_status,
)


class Command(BaseCommand):
    help = "Create the per-provider HNSW expression indexes (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true", help="Report only; exit 1 if any is missing."
        )

    def handle(self, *args, **options):
        entries = index_status() if options["check"] else build_vector_indexes()
        if not entries:
            self.stdout.write("No active embedding provider: nothing to index.")
        for entry in entries:
            line = (
                f"{entry.status:<20} {entry.table:<26} {entry.provider_name} "
                f"(dim {entry.dimension}) {entry.name}"
            )
            if entry.status in (SKIPPED_DIMENSION, SKIPPED_NO_DIMENSION):
                self.stderr.write(f"warning: {line}")
            else:
                self.stdout.write(line)
        if options["check"]:
            failing = [e for e in entries if e.status in (MISSING, STALE)]
            if failing:
                raise CommandError(f"{len(failing)} vector index(es) missing or stale.")
