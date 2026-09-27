"""Cross-app notification: when a Gate A/B `ApprovalRequest` is rejected or
expires, the linked `HarnessImplementationJob` is failed closed (Phase 3
branch 2, brief §boundary). Mirrors `agents/signals.py`'s receiver exactly
-- `harness` may import `approvals` (this branch's new one-way dependency,
pyproject.toml); `approvals` never imports or knows about `harness` at all.
Connected in `harness.apps.HarnessConfig.ready()`, never imported at
module-load time elsewhere.

An `approved` decision is NOT handled here -- `approvals.services.
approve_request` already calls `tool.create_execution()`/`tool.dispatch()`
synchronously for the approved case (see `harness/tools.py`); a
rejected/expired decision has no `dispatch` to run at all, which is
exactly the gap this receiver exists to cover -- without it, a rejected or
expired gate would leave the job stuck in `pending_gate_a`/`pending_gate_b`
forever.
"""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from approvals.models import ApprovalRequest


@receiver(post_save, sender=ApprovalRequest)
def _fail_implementation_job_on_gate_decision(sender, instance, **kwargs):  # noqa: ARG001
    if instance.status not in (ApprovalRequest.Status.REJECTED, ApprovalRequest.Status.EXPIRED):
        return
    # Mirrors `agents/signals.py`/`tools.export.describe_vault_export_execution`'s
    # identical getattr-with-default pattern for a OneToOneField reverse
    # accessor -- Django's RelatedObjectDoesNotExist subclasses
    # AttributeError specifically so this works, no exception needs
    # catching here.
    gate_a_job = getattr(instance, "harness_implementation_job_gate_a", None)
    gate_b_job = getattr(instance, "harness_implementation_job_gate_b", None)
    status = instance.status
    if gate_a_job is not None:
        from harness.services import fail_implementation_job_for_gate_a_decision

        transaction.on_commit(
            lambda: fail_implementation_job_for_gate_a_decision(
                gate_a_job.id, decision_status=status
            )
        )
    elif gate_b_job is not None:
        from harness.services import fail_implementation_job_for_gate_b_decision

        transaction.on_commit(
            lambda: fail_implementation_job_for_gate_b_decision(
                gate_b_job.id, decision_status=status
            )
        )
