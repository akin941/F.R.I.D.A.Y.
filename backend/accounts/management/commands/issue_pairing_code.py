"""Print a one-time device pairing code (Phase 4 branch 2).

Host shell access is the proof of ownership: this is how the first device
is enrolled, and how the owner recovers when every device is lost. The code
is printed once, audited, never throttled by the web enroll limits.

    manage.py issue_pairing_code            # 8-digit PIN, 5 minutes, LAN only
    manage.py issue_pairing_code --strong   # strong code, 30 minutes, any network
    manage.py issue_pairing_code --plain    # just the code (scripts, e2e)
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.devices import DeviceTrustError, get_owner, issue_pairing_code
from accounts.models import DevicePairingCode


class Command(BaseCommand):
    help = "Issue a one-time device pairing code for the owner."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strong",
            action="store_true",
            help="A strong code (accepted from any network) instead of a LAN-only PIN.",
        )
        parser.add_argument("--plain", action="store_true", help="Print only the code.")

    def handle(self, *args, **options):
        kind = DevicePairingCode.Kind.STRONG if options["strong"] else DevicePairingCode.Kind.PIN
        try:
            raw, code = issue_pairing_code(
                owner=get_owner(),
                kind=kind,
                issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
            )
        except DeviceTrustError as exc:
            raise CommandError(str(exc)) from exc
        if options["plain"]:
            self.stdout.write(raw)
            return
        self.stdout.write(f"Pairing code: {raw}")
        self.stdout.write(
            f"Valid until {code.expires_at:%Y-%m-%d %H:%M:%S %Z}, single use. "
            "Enter it at /enroll on the device you want to trust."
        )
