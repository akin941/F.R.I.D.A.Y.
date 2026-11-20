"""`manage.py build_self_sources [--check] [--root PATH] [--output PATH]`
(Phase 8A D8).

Writes `assistant/self_sources.json` from CLAUDE.md, the accepted ADRs and
the MASTER_PLAN "Friday 3.0 phases" table. `--check` writes nothing and
exits non-zero when the committed snapshot differs from what the documents
produce now (CI step, like `makemigrations --check`): the `self` block can
never describe a repo that no longer exists.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from assistant.self_sources import (
    SNAPSHOT_PATH,
    SnapshotError,
    build_snapshot,
    render_snapshot,
)


class Command(BaseCommand):
    help = "Build (or --check) the self block's source snapshot."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true")
        parser.add_argument(
            "--root",
            default=None,
            help="Repository root holding CLAUDE.md and docs/ (default: BASE_DIR's parent).",
        )
        parser.add_argument("--output", default=None, help="Snapshot path (default: committed).")

    def handle(self, *args, check=False, root=None, output=None, **options):
        repo_root = Path(root) if root else Path(settings.BASE_DIR).parent
        target = Path(output) if output else SNAPSHOT_PATH
        try:
            rendered = render_snapshot(build_snapshot(repo_root))
        except SnapshotError as exc:
            raise CommandError(f"self sources: {exc}") from exc
        if check:
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            if current != rendered:
                raise CommandError(
                    f"{target.name} is out of date with CLAUDE.md / docs/adr / MASTER_PLAN; "
                    "run `manage.py build_self_sources` and commit the result."
                )
            self.stdout.write("self sources up to date.")
            return
        target.write_text(rendered, encoding="utf-8")
        self.stdout.write(f"wrote {target}")
