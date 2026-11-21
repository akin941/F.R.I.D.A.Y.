"""Walk the hash-chained node invocation audit (Phase 9 brief D8).

    manage.py verify_node_audit_chain

Exits 0 and prints the row count when the chain is intact; exits non-zero
at the first break, printing its `seq` and the reason. Preflight runs it
(`node_audit_chain`, closeout)."""

from django.core.management.base import BaseCommand, CommandError

from nodes.audit_chain import verify_chain


class Command(BaseCommand):
    help = "Verify the hash-chained node invocation audit; non-zero exit at the first break."

    def handle(self, *args, **options):
        result = verify_chain()
        if not result.ok:
            raise CommandError(
                f"node audit chain broken at seq {result.broken_seq} ({result.reason}); "
                f"{result.rows} row(s) verified before it"
            )
        self.stdout.write(f"node audit chain OK: {result.rows} row(s)")
