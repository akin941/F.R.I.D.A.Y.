# ruff: noqa: F811 -- pytest fixtures imported from the helper module are redefined as arguments
"""Phase 12 D2/D17: inside the distill run every effect but the proposal
write is denied in its owning service, audited once through the
`DenialRecorder` (source `skills.playbook_distill`); the run ends `denied`
and its sources stay unused."""

import pytest

from ai_core.gateway import InferenceGateway
from approvals import services as approval_services
from approvals.models import ApprovalRequest
from approvals.tasks import sweep_expired_approval_requests
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION
from connectors import reads as connector_reads
from harness import services as harness_services
from memory import services as memory_services
from memory.models import Memory
from skills import playbooks, services
from skills.models import InstructionSkillVersion, PlaybookDistillRun, PlaybookSource
from skills.tests.helpers import propose
from skills.tests.playbook_helpers import (  # noqa: F401
    distill_prompt,
    distiller_answer,
    fake_provider,
    good_draft,
    owner,
    record_sources,
    workspace,
)
from tasks import services as task_services

pytestmark = pytest.mark.django_db

EFFECTS = {
    "approval_request": lambda ws, owner: approval_services.create_request(
        workspace=ws,
        actor=owner,
        tool_name="vault_export",
        payload={},
        origin=ApprovalRequest.Origin.HUMAN,
        channel="web",
    ),
    "task_write": lambda ws, owner: task_services.create_task(
        workspace=ws, actor=owner, title="t"
    ),
    "memory_write": lambda ws, owner: memory_services.create_memory(
        workspace=ws,
        actor=owner,
        title="t",
        content="c",
        memory_type=Memory.Type.SEMANTIC,
        sensitivity=Memory.Sensitivity.NORMAL,
    ),
    "connector_read": lambda ws, owner: connector_reads.perform_read(
        None, None, workspace=ws, actor=owner, args={}, channel="web"
    ),
    "harness_job": lambda ws, owner: harness_services.create_harness_implementation_job(
        workspace=ws, actor=owner, repo=None, goal="g"
    ),
    "gateway_call": lambda ws, owner: InferenceGateway().complete(
        "chat", [{"role": "user", "content": "x"}], workspace=ws
    ),
    "celery_dispatch": lambda ws, owner: sweep_expired_approval_requests.apply_async(),
    "skill_decision": lambda ws, owner: services.approve_version(
        actor=owner,
        channel="web",
        version=InstructionSkillVersion.objects.get(),
        expected_sha256=InstructionSkillVersion.objects.get().content_sha256,
    ),
    "skill_write": lambda ws, owner: propose(ws, owner),
}


@pytest.mark.parametrize("effect", sorted(EFFECTS))
def test_every_effect_is_denied_inside_the_distill_run(
    owner, workspace, distill_prompt, fake_provider, monkeypatch, effect
):
    if effect == "skill_decision":
        propose(workspace, owner)
    pending_before = InstructionSkillVersion.objects.count()
    record_sources(workspace, 3)

    def hostile(**kwargs):
        EFFECTS[effect](workspace, owner)
        pytest.fail("the effect ran")

    monkeypatch.setattr(playbooks, "_call_model", hostile)
    playbooks.distill_playbooks()
    run = PlaybookDistillRun.objects.get()
    assert run.outcome == PlaybookDistillRun.Outcome.DENIED
    denials = AuditLog.objects.filter(action=DENIAL_ACTION)
    assert denials.count() == 1
    assert denials.get().metadata["source"] == "skills.playbook_distill"
    assert denials.get().metadata["step"] == "distill"
    assert InstructionSkillVersion.objects.count() == pending_before
    assert PlaybookSource.objects.filter(distill_run__isnull=True).count() == 3
    assert not ApprovalRequest.objects.exists()


def test_the_proposal_write_is_the_one_allowed_effect(
    workspace, distill_prompt, fake_provider, distiller_answer
):
    sources = record_sources(workspace, 3)
    distiller_answer(good_draft(sources))
    playbooks.distill_playbooks()
    assert PlaybookDistillRun.objects.get().outcome == PlaybookDistillRun.Outcome.PROPOSED
    assert not AuditLog.objects.filter(action=DENIAL_ACTION).exists()
    assert AuditLog.objects.filter(action="skills.version.proposed").count() == 1
