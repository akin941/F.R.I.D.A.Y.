"""Dev/local bootstrapping for a CLI PersonalAccessToken (MVP 9 §2) -- lets
the CLI channel be exercised without needing the frontend settings UI (a
later branch) first. Mirrors ai_core's `seed_ai_defaults`: a dev-only
convenience command, never run in production deploy automation.
"""

import secrets

from django.core.management.base import BaseCommand, CommandError

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from workspaces.models import Workspace


class Command(BaseCommand):
    help = "Mint a dev PersonalAccessToken for <email>, bound to their workspace <workspace-name>."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("workspace_name")

    def handle(self, *args, **options):
        email = options["email"]
        workspace_name = options["workspace_name"]

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {email!r}.") from exc

        # Membership-filtered, not a bare name lookup: a workspace name is
        # not globally unique, and this must never mint a token bound to a
        # workspace the user doesn't actually belong to.
        workspace = Workspace.objects.filter(name=workspace_name, members__user=user).first()
        if workspace is None:
            raise CommandError(
                f"{email!r} is not a member of a workspace named {workspace_name!r}."
            )

        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            user=user,
            workspace=workspace,
            name="dev cli",
            token_hash=hash_token(raw_token),
        )
        # Raw token only ever appears here -- same one-time-display
        # discipline as the mint endpoint (accounts/views.py).
        self.stdout.write(raw_token)
