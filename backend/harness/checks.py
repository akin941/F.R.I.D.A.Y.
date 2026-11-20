"""Boot-time check of `settings.HARNESS_AGENT_POLICY` (Phase 5 D11).

An Error-level check stops `runserver`, `migrate` and every other command
that runs system checks, so a self-pairing (ADR-008 decision 3) or a
reviewer-less implementer can never boot into service.

`check_self_repo_identifiers_renamed` (Warning, harness.W001) flags an owner
`.env` written before the Jarvis -> Friday rename: a non-empty
`HARNESS_SELF_REPO_IDENTIFIERS` with no entry naming the renamed remote.
The guard itself already refuses the canonical remote (built-in
identifiers in `core.repo_identity`), so this is a nudge to fix the
configuration, not the guard."""

from django.conf import settings
from django.core.checks import Error, Warning, register

from core.repo_identity import CANONICAL_SELF_REPO, canonicalize_git_url
from harness.models import HarnessImplementationJob, HarnessJob, HarnessReview
from harness.policy import policy_errors


@register()
def check_harness_agent_policy(app_configs=None, **kwargs):
    known_agents = (
        set(HarnessJob.Agent.values)
        | set(HarnessImplementationJob.Agent.values)
        | set(HarnessReview.Agent.values)
    )
    return [
        Error(message, id="harness.E001")
        for message in policy_errors(
            getattr(settings, "HARNESS_AGENT_POLICY", None), known_agents=known_agents
        )
    ]


@register()
def check_self_repo_identifiers_renamed(app_configs=None, **kwargs):
    identifiers = getattr(settings, "HARNESS_SELF_REPO_IDENTIFIERS", None) or []
    if not identifiers:
        # Empty is the separate fail-closed case (every write job refused).
        return []
    canonical = canonicalize_git_url(CANONICAL_SELF_REPO)
    if any(canonicalize_git_url(identifier) == canonical for identifier in identifiers):
        return []
    return [
        Warning(
            "HARNESS_SELF_REPO_IDENTIFIERS lists no identifier for this installation's "
            f"renamed repository ({CANONICAL_SELF_REPO}).",
            hint=(
                "Add the renamed remote, e.g. "
                "HARNESS_SELF_REPO_IDENTIFIERS=git@github.com:akin941/F.R.I.D.A.Y..git,"
                "https://github.com/akin941/F.R.I.D.A.Y. -- see "
                "docs/runbooks/rename-jarvis-to-friday.md."
            ),
            id="harness.W001",
        )
    ]
