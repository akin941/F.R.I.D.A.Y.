# ruff: noqa: F811 -- pytest fixtures imported from the fixture module are redefined as arguments
"""Phase 12 D2/D16 release gate for the curator: inside its run the curator
may write only skill proposals, archive suggestions and stale flags. An
approval, a task, a memory, a schedule, a connector call, another purpose,
a Celery dispatch or an owner skill decision (approving, archiving,
restoring, proposing as the owner) is refused by its owning service, with
one `proactive.unattended_denied` audit row each (source `skills.curator`),
and nothing is created."""

import pytest

from ai_core.gateway import InferenceGateway
from approvals import services as approval_services
from approvals.models import ApprovalRequest
from approvals.tasks import sweep_expired_approval_requests
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION
from connectors import reads as connector_reads
from core.channels import CHANNEL_WEB
from core.unattended import UnattendedDenied
from memory import services as memory_services
from memory.models import Memory
from proactive import services as proactive_services
from proactive.models import ProactiveSchedule
from skills import curator, services
from skills.models import InstructionSkill, InstructionSkillVersion, SkillCuratorRun
from skills.tests.curator_fixtures import (  # noqa: F401
    curate_answer,
    owner,
    provider,
    seeded,
    skill_md,
    workspace,
)
from skills.tests.helpers import active_skill, propose, skill_text
from tasks import services as task_services
from tasks.models import Task

pytestmark = pytest.mark.django_db


def _effects(workspace, owner, *, pending, archivable, archived):
    return {
        "approval_request": lambda: approval_services.create_request(
            workspace=workspace,
            actor=owner,
            tool_name="vault_export",
            payload={},
            origin=ApprovalRequest.Origin.AGENT,
            channel="system",
            system_created=True,
        ),
        "task_write": lambda: task_services.create_task(
            workspace=workspace, actor=owner, title="Küratör görevi"
        ),
        "memory_write": lambda: memory_services.create_memory(
            workspace=workspace,
            actor=owner,
            title="t",
            content="c",
            memory_type=Memory.Type.SEMANTIC,
            sensitivity=Memory.Sensitivity.NORMAL,
        ),
        "schedule_write": lambda: proactive_services.save_schedule(
            actor=owner,
            channel=CHANNEL_WEB,
            workspace=workspace,
            kind="morning_digest",
            enabled=True,
        ),
        "connector_read": lambda: connector_reads.perform_read(
            None, None, workspace=workspace, actor=owner, args={}, channel="web"
        ),
        "gateway_call": lambda: InferenceGateway().complete(
            "chat", [{"role": "user", "content": "x"}], workspace=workspace
        ),
        "celery_dispatch": lambda: sweep_expired_approval_requests.apply_async(),
        "skill_decision:approve": lambda: services.approve_version(
            actor=owner,
            channel=CHANNEL_WEB,
            version=pending,
            expected_sha256=pending.content_sha256,
        ),
        "skill_decision:reject": lambda: services.reject_version(
            actor=owner, channel=CHANNEL_WEB, version=pending
        ),
        "skill_decision:archive": lambda: services.archive_skill(
            actor=owner, channel=CHANNEL_WEB, skill=archivable
        ),
        "skill_decision:restore": lambda: services.restore_skill(
            actor=owner, channel=CHANNEL_WEB, skill=archived
        ),
        "skill_write": lambda: services.propose_from_owner(
            actor=owner,
            channel=CHANNEL_WEB,
            workspace=workspace,
            parsed=services.parse_text(skill_text(name="sahip-gibi")),
            origin="owner",
        ),
    }


@pytest.fixture
def setting(owner, workspace):
    active_skill(workspace, owner, skill_text(name="pr-yaz"))
    archivable = active_skill(workspace, owner, skill_text(name="arsivlik"))
    archived = active_skill(workspace, owner, skill_text(name="arsivde"))
    InstructionSkill.objects.filter(pk=archived.pk).update(status="archived")
    archived.refresh_from_db()
    # A pending version of another skill (the owner's), which the curator
    # must not be able to approve or reject.
    pending = propose(workspace, owner, skill_text(name="baska", body="Sahibin taslağı."))
    return {"pending": pending, "archivable": archivable, "archived": archived}


def _snapshot():
    return {
        "approvals": ApprovalRequest.objects.count(),
        "tasks": Task.all_objects.count(),
        "memories": Memory.objects.count(),
        "schedules": ProactiveSchedule.objects.count(),
        "skills": sorted(InstructionSkill.objects.values_list("name", "status")),
        "decisions": sorted(InstructionSkillVersion.objects.values_list("id", "decision")),
    }


def test_every_other_effect_is_denied_inside_the_curator_run(
    owner, workspace, seeded, provider, curate_answer, setting
):
    effects = _effects(workspace, owner, **setting)
    outcomes = {}

    def attempt_everything():
        for name, effect in effects.items():
            try:
                effect()
                outcomes[name] = "ran"
            except UnattendedDenied as exc:
                outcomes[name] = exc.effect
            except Exception as exc:  # pragma: no cover - the failure being tested
                outcomes[name] = f"error:{exc.__class__.__name__}"

    curate_answer.side_effect = attempt_everything
    before = _snapshot()
    run = curator.curate_workspace(workspace)

    assert outcomes == {name: name.split(":")[0] for name in effects}
    assert _snapshot() == before
    rows = AuditLog.objects.filter(action=DENIAL_ACTION)
    assert rows.count() == len(effects)
    assert {row.metadata["source"] for row in rows} == {"skills.curator"}
    assert {row.metadata["run_id"] for row in rows} == {str(run.pk)}
    assert {row.metadata["step"] for row in rows} == {"curate"}
    assert run.denials == len(effects)
    assert run.outcome == SkillCuratorRun.Outcome.QUIET


def test_an_uncaught_denial_ends_the_run_as_denied(
    owner, workspace, seeded, provider, curate_answer, setting
):
    curate_answer.side_effect = lambda: task_services.create_task(
        workspace=workspace, actor=owner, title="x"
    )
    curate_answer.answer([{"action": "archive", "skill": "pr-yaz", "reason": "unused"}])
    run = curator.curate_workspace(workspace)
    assert run.outcome == SkillCuratorRun.Outcome.DENIED
    assert run.denials == 1 and run.archive_suggestions == []
    assert AuditLog.objects.filter(action=DENIAL_ACTION).count() == 1
    assert Task.all_objects.count() == 0


def test_the_curator_itself_may_propose_and_mark_stale_inside_its_run(
    owner, workspace, seeded, provider, curate_answer, setting
):
    curate_answer.answer(
        [{"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("pr-yaz", "Yeni.")}]
    )
    run = curator.curate_workspace(workspace)
    assert run.outcome == SkillCuratorRun.Outcome.PROPOSALS and run.denials == 0
    assert not AuditLog.objects.filter(action=DENIAL_ACTION).exists()


def test_the_owner_services_work_again_after_the_run(
    owner, workspace, seeded, provider, curate_answer, setting
):
    curator.curate_workspace(workspace)
    pending = setting["pending"]
    services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=pending, expected_sha256=pending.content_sha256
    )
    pending.refresh_from_db()
    assert pending.decision == "approved"
