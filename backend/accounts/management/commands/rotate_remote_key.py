"""Create/rotate or revoke the owner's remote access key (Phase 4 branch 2).

The key (`jrk_...`) enrolls a device from any network, reusable until
rotated; every use is audited. There is none by default.

    manage.py rotate_remote_key            # create or rotate, prints the key once
    manage.py rotate_remote_key --revoke   # turn remote enrollment off
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.devices import DeviceTrustError, get_owner, revoke_remote_key, rotate_remote_key


class Command(BaseCommand):
    help = "Create/rotate (default) or revoke the owner's remote access key."

    def add_arguments(self, parser):
        parser.add_argument("--revoke", action="store_true")

    def handle(self, *args, **options):
        try:
            if options["revoke"]:
                revoked = revoke_remote_key(owner=get_owner(), via="management_command")
                self.stdout.write("Remote access key revoked." if revoked else "No active key.")
                return
            raw, _ = rotate_remote_key(owner=get_owner(), via="management_command")
        except DeviceTrustError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(raw)
