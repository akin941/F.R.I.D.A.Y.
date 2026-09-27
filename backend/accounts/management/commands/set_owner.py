"""Mark an existing user as THE owner (Phase 4 branch 2).

The `0005_backfill_owner` migration marks the owner automatically only when
exactly one human user exists; any other database is ambiguous and needs
this command. Moving ownership away from an existing owner requires
`--replace` and revokes every device and remote key of the previous owner.
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.devices import DeviceTrustError, set_owner
from accounts.models import User


class Command(BaseCommand):
    help = "Mark an existing user as the installation owner."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Move ownership from the current owner (revokes their devices).",
        )

    def handle(self, *args, **options):
        user = User.objects.filter(email__iexact=options["email"]).first()
        if user is None:
            raise CommandError(f"No user with email {options['email']!r}.")
        try:
            set_owner(user=user, replace=options["replace"])
        except DeviceTrustError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Owner: {user.email}"))
