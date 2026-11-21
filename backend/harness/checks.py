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


@register()
def check_harness_drivers(app_configs=None, **kwargs):
    """Phase 10 brief D10 "Selection" and "Pins": a non-`cli` driver is
    refused unless `HARNESS_DRIVERS_ENABLED` is true (harness.E002) and the
    owner's terms check (d4) is recorded as a date in
    `HARNESS_DRIVER_TERMS_CHECKED_ON` (harness.E003); an unknown value is
    refused (harness.E005); the Codex app-server pin must equal the
    Dockerfile ARG and the snapshot version (harness.E004). An enabled
    driver backed by a provisional snapshot is a warning here
    (harness.W002); the closeout preflight fails on it."""
    from harness.drivers import (
        DRIVER_CHOICES,
        DRIVER_CLI,
        DRIVER_SETTINGS,
        configured_driver,
        drivers_enabled,
        terms_checked_on,
    )
    from harness.drivers.schema import codex_pin_errors, load_codex_snapshot

    messages = []
    for agent, choices in DRIVER_CHOICES.items():
        value = configured_driver(agent)
        setting = DRIVER_SETTINGS[agent]
        if value not in choices:
            messages.append(
                Error(f"{setting}={value!r} is not one of {', '.join(choices)}.", id="harness.E005")
            )
            continue
        if value == DRIVER_CLI:
            continue
        if not drivers_enabled():
            messages.append(
                Error(
                    f"{setting}={value!r} needs HARNESS_DRIVERS_ENABLED=true.",
                    hint="Leave the driver on `cli` until drivers are enabled.",
                    id="harness.E002",
                )
            )
            continue
        if terms_checked_on() is None:
            messages.append(
                Error(
                    f"{setting}={value!r} is enabled but HARNESS_DRIVER_TERMS_CHECKED_ON "
                    "holds no date.",
                    hint=(
                        "Record the third-party terms check (owner step d4, "
                        "docs/prompts/phase-10-control/brief.md) as YYYY-MM-DD before enabling."
                    ),
                    id="harness.E003",
                )
            )
        if agent == "codex_cli":
            try:
                snapshot = load_codex_snapshot(settings.HARNESS_CODEX_APP_SERVER_VERSION)
            except (OSError, ValueError):
                snapshot = None
            if snapshot is not None and snapshot.provisional:
                messages.append(
                    Warning(
                        "The Codex app-server driver is enabled on a provisional schema snapshot.",
                        hint="Run harness/docker/generate_driver_schemas.sh for the pin.",
                        id="harness.W002",
                    )
                )
    messages.extend(Error(message, id="harness.E004") for message in codex_pin_errors())
    return messages
