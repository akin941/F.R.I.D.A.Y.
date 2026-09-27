"""Revoke every trusted device of the owner (Phase 4 branch 2) -- the
response to a lost or stolen device when no other trusted device is at
hand. Every web session ends with its next request; re-enroll with
`manage.py issue_pairing_code`."""

from django.core.management.base import BaseCommand, CommandError

from accounts.devices import get_owner, revoke_all_devices


class Command(BaseCommand):
    help = "Revoke every trusted device of the owner."

    def handle(self, *args, **options):
        owner = get_owner()
        if owner is None:
            raise CommandError("No owner account is configured.")
        count = revoke_all_devices(user=owner)
        self.stdout.write(self.style.SUCCESS(f"Revoked {count} device(s)."))
