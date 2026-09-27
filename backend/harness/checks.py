"""Boot-time check of `settings.HARNESS_AGENT_POLICY` (Phase 5 D11).

An Error-level check stops `runserver`, `migrate` and every other command
that runs system checks, so a self-pairing (ADR-008 decision 3) or a
reviewer-less implementer can never boot into service."""

from django.conf import settings
from django.core.checks import Error, register

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
