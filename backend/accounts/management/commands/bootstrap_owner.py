"""Create THE owner account of this single-owner installation (ADR-008).

With signup permanently off (ADR-008 decision 2) this command is the one
way in: it refuses to run if any user already exists -- it bootstraps the
single owner, it is not a user-creation tool. Goes through the same
`register_user()` service the signup view used, so the personal workspace
and the `user.signup` audit row are created identically.

The no-user check and the create run inside one transaction holding a
Postgres advisory xact lock (Codex phase-0 code-review P1): a bare
`exists()` + create pair is a classic phantom race -- two parallel
bootstrap runs with different emails could both see "no user" and create
two owners, and the email unique constraint would not stop them. The
advisory lock serializes the whole check-then-create critical section;
it is released automatically at commit/rollback.

The password is read from the JARVIS_OWNER_PASSWORD env var, or prompted
for interactively -- never accepted as a CLI argument (argv leaks into
shell history and process listings). It must pass AUTH_PASSWORD_VALIDATORS
(Phase 4 branch 1: Django's four, 12 characters minimum).
"""

import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from accounts.models import User
from accounts.services import register_user

# Arbitrary but stable app-wide key for pg_advisory_xact_lock; only
# bootstrap_owner uses it, so it serializes exactly this critical section.
_BOOTSTRAP_LOCK_KEY = 0x1A47_0001


class Command(BaseCommand):
    help = "Create the single owner account (refuses to run if any user exists)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="")

    def handle(self, *args, **options):
        password = os.environ.get("JARVIS_OWNER_PASSWORD") or ""
        if not password:
            password = getpass.getpass("Owner password: ")
        if not password.strip():
            raise CommandError("A non-empty password is required.")
        try:
            # An unsaved User so UserAttributeSimilarityValidator can compare
            # against the email/name the owner is about to get.
            validate_password(password, user=User(email=options["email"], name=options["name"]))
        except ValidationError as exc:
            raise CommandError("Password rejected: " + " ".join(exc.messages)) from exc

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_BOOTSTRAP_LOCK_KEY])
            if User.objects.exists():
                raise CommandError(
                    "A user already exists; bootstrap_owner only creates the first "
                    "(single) owner account. It never adds users to an existing "
                    "installation (ADR-008)."
                )
            user = register_user(email=options["email"], password=password, name=options["name"])
        self.stdout.write(self.style.SUCCESS(f"Owner account created: {user.email}"))
