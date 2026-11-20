"""Dev/local bootstrapping for a CLI PersonalAccessToken (MVP 9 §2) -- lets
the CLI channel be exercised without needing the frontend settings UI (a
later branch) first. Mirrors ai_core's `seed_ai_defaults`: a dev-only
convenience command, never run in production deploy automation.
"""

import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from audit.services import record_audit
from workspaces.models import Workspace


class Command(BaseCommand):
    help = "Mint a dev PersonalAccessToken for <email>, bound to their workspace <workspace-name>."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("workspace_name")
        parser.add_argument(
            "--kind",
            choices=PersonalAccessToken.Kind.values,
            default=PersonalAccessToken.Kind.CLI,
            help=(
                "Token kind: cli (default), openai (the OpenAI-compatible facade) or "
                "hook (the Claude Code hook relay)."
            ),
        )

    def handle(self, *args, **options):
        # The docstring/help text call this dev-only, but nothing enforced
        # that -- an accidental invocation against a production deploy
        # would mint a real bearer credential for any email/workspace named
        # (Codex review, branch 2 round 3). Gate on DEBUG, mirroring
        # approvals.apps.ApprovalsConfig.ready()'s identical
        # dev-only fail-fast convention (mirrors skills' boot guard).
        if not settings.DEBUG:
            raise CommandError("create_dev_pat is a dev-only command and requires DEBUG=True.")

        email = options["email"]
        workspace_name = options["workspace_name"]

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {email!r}.") from exc

        # Membership-filtered, not a bare name lookup: a workspace name is
        # not globally unique, and this must never mint a token bound to a
        # workspace the user doesn't actually belong to.
        matches = list(Workspace.objects.filter(name=workspace_name, members__user=user))
        if not matches:
            raise CommandError(
                f"{email!r} is not a member of a workspace named {workspace_name!r}."
            )
        if len(matches) > 1:
            # Workspace names are not unique -- silently picking one of
            # several matches would bind the token to whichever row the
            # database happened to return first (Codex review, branch 2
            # round 1). Fail loud and let the operator disambiguate by id.
            ids = ", ".join(str(ws.id) for ws in matches)
            raise CommandError(
                f"{email!r} belongs to {len(matches)} workspaces named "
                f"{workspace_name!r} ({ids}) -- ambiguous, rename one or "
                f"mint the token another way."
            )
        workspace = matches[0]

        kind = options["kind"]
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = PersonalAccessToken.objects.create(
            user=user,
            workspace=workspace,
            name=f"dev {kind}",
            kind=kind,
            token_hash=hash_token(raw_token),
        )
        # Same audit row as the mint endpoint, marked as coming from a
        # management command (no request, so no web channel behind it).
        record_audit(
            action="accounts.pat.created",
            actor=user,
            workspace=workspace,
            resource=token,
            metadata={
                "via": "management_command",
                "kind": kind,
                "expires_at": token.expires_at.isoformat(),
            },
        )
        # Raw token only ever appears here -- same one-time-display
        # discipline as the mint endpoint (accounts/views.py).
        self.stdout.write(raw_token)
