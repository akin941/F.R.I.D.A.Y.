"""Cross-app notification: when an `ApprovalRequest` tied to an `AgentStep`
is decided (approved/rejected), the agent runtime resumes automatically
(MVP 7 §3 -- the user-approved "Django signal" resume mechanism, chosen
over periodic polling for immediate resume with no added latency).

`agents` may import `approvals` (the one-way direction this MVP
establishes); `approvals` never imports or knows about `agents` at all --
Django's signal dispatch is what lets a receiver registered on the
RECEIVING side (here) react to the SENDING app's model save without the
sender needing any awareness of who's listening. Connected in
`agents.apps.AgentsConfig.ready()`, never imported at module-load time
elsewhere (signal receivers must be connected exactly once, at app
startup).
"""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from approvals.models import ApprovalRequest


@receiver(post_save, sender=ApprovalRequest)
def _resume_agent_on_approval_decision(sender, instance, **kwargs):  # noqa: ARG001
    if instance.status == ApprovalRequest.Status.PENDING:
        return
    # Mirrors tools.export.describe_vault_export_execution's identical
    # getattr-with-default pattern for a OneToOneField reverse accessor --
    # Django's RelatedObjectDoesNotExist subclasses AttributeError
    # specifically so this works, no exception needs catching here.
    agent_step = getattr(instance, "agent_step", None)
    if agent_step is None:
        return
    from agents.execution import resume_after_approval_decision

    approved = instance.status == ApprovalRequest.Status.APPROVED
    transaction.on_commit(
        lambda: resume_after_approval_decision(agent_step_id=agent_step.id, approved=approved)
    )
