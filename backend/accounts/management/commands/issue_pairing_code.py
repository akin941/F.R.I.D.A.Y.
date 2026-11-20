"""Print a one-time device pairing code (Phase 4 branch 2).

Host shell access is the proof of ownership: this is how the first device
is enrolled, and how the owner recovers when every device is lost. The code
is printed once, audited, never throttled by the web enroll limits.

    manage.py issue_pairing_code            # 8-digit PIN, 5 minutes, LAN only
    manage.py issue_pairing_code --strong   # strong code, 30 minutes, any network
    manage.py issue_pairing_code --plain    # just the code (scripts, e2e)
    manage.py issue_pairing_code --node [--strong]  # pair a Friday Node (Phase 9)
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.devices import DeviceTrustError, get_owner, issue_pairing_code
from accounts.models import DevicePairingCode, TrustedDevice


class Command(BaseCommand):
    help = "Issue a one-time device pairing code for the owner."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strong",
            action="store_true",
            help="A strong code (accepted from any network) instead of a LAN-only PIN.",
        )
        parser.add_argument(
            "--node",
            action="store_true",
            help="A code that pairs a Friday Node (the Mac app), not a browser.",
        )
        parser.add_argument("--plain", action="store_true", help="Print only the code.")

    def handle(self, *args, **options):
        kind = DevicePairingCode.Kind.STRONG if options["strong"] else DevicePairingCode.Kind.PIN
        device_kind = TrustedDevice.Kind.NODE if options["node"] else TrustedDevice.Kind.BROWSER
        try:
            raw, code = issue_pairing_code(
                owner=get_owner(),
                kind=kind,
                issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
                device_kind=device_kind,
            )
        except DeviceTrustError as exc:
            raise CommandError(str(exc)) from exc
        if options["plain"]:
            self.stdout.write(raw)
            return
        if options["node"]:
            self.stdout.write(f"Node pairing code: {raw}")
            self.stdout.write(
                f"Valid until {code.expires_at:%Y-%m-%d %H:%M:%S %Z}, single use. "
                "Enter it in the Friday Node app on the Mac you want to pair "
                "(it is refused at /enroll)."
            )
            return
        self.stdout.write(f"Pairing code: {raw}")
        self.stdout.write(
            f"Valid until {code.expires_at:%Y-%m-%d %H:%M:%S %Z}, single use. "
            "Enter it at /enroll on the device you want to trust."
        )
