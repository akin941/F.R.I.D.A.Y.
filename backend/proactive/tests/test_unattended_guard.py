"""Phase 12 D2 release gate: inside an unattended run every effect is denied
in its owning service, before any row or network call, and each denial
leaves exactly one `proactive.unattended_denied` audit row once the
recorder flushes. Outside a run the services are unchanged (the rest of
the suite)."""

import uuid

import pytest
from django.db import transaction

from ai_core.gateway import InferenceGateway
from ai_core.models import ModelUsage
from approvals import services as approval_services
from approvals.models import ApprovalRequest
from approvals.tasks import sweep_expired_approval_requests
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION, recorded_unattended_run
from config.celery import app as celery_app
from connectors import reads as connector_reads
from connectors import tasks as connector_tasks
from connectors import writes as connector_writes
from core.unattended import UnattendedDenied
from harness import services as harness_services
from memory import services as memory_services
from memory.graph import services as graph_services
from memory.models import Memory
from nodes import services as node_services
from proactive.models import ProactiveRun
from proactive.runs import run_schedule
from proactive.tasks import prune
from proactive.tests.conftest import make_schedule
from tasks import services as task_services
from tasks.models import Task
from tools import registry

pytestmark = pytest.mark.django_db


def _denials():
    return list(AuditLog.objects.filter(action=DENIAL_ACTION))


def _effects():
    return [row.metadata["effect"] for row in _denials()]


EFFECTS = {
    "approval_request": lambda ws, owner: approval_services.create_request(
        workspace=ws,
        actor=owner,
        tool_name="vault_export",
        payload={},
        origin=ApprovalRequest.Origin.HUMAN,
        channel="web",
    ),
    "gated_dispatch:approve": lambda ws, owner: approval_services.approve_request(
        approval_request=None, actor=owner, channel="web"
    ),
    "gated_dispatch:reject": lambda ws, owner: approval_services.reject_request(
        approval_request=None, actor=owner, channel="web"
    ),
    "read_tool": lambda ws, owner: registry.run_read_tool(
        registry.ReadToolSpec(
            name="fake_read",
            description="",
            argument_serializer=None,
            data_class="personal",
            channels=frozenset({"web"}),
            execute=lambda **kw: pytest.fail("the read ran"),
            is_available=lambda w: True,
        ),
        workspace=ws,
        actor=owner,
        args={},
        channel="web",
    ),
    "connector_read": lambda ws, owner: connector_reads.perform_read(
        None, None, workspace=ws, actor=owner, args={}, channel="web"
    ),
    "connector_write:prepare": lambda ws, owner: connector_writes.prepare_write_payload(
        "github", "github_open_issue", ws, {"arguments": {}}
    ),
    "connector_write:run": lambda ws, owner: connector_tasks.run_connector_write(uuid.uuid4()),
    "task_write:create": lambda ws, owner: task_services.create_task(
        workspace=ws, actor=owner, title="Unattended task"
    ),
    "task_write:edit": lambda ws, owner: task_services.edit_task(task=None, actor=owner),
    "memory_write:create": lambda ws, owner: memory_services.create_memory(
        workspace=ws,
        actor=owner,
        title="t",
        content="c",
        memory_type=Memory.Type.SEMANTIC,
        sensitivity=Memory.Sensitivity.NORMAL,
    ),
    "memory_write:approve": lambda ws, owner: memory_services.approve_memory(
        memory=None, actor=owner
    ),
    "memory_write:work_log": lambda ws, owner: memory_services.record_work_log(
        workspace=ws, title="t", content="c"
    ),
    # Beyond the D2 minimum (review round 1): every other memory / task
    # writer refuses too, so a probe reusing one cannot slip past.
    "memory_write:reject": lambda ws, owner: memory_services.reject_memory(
        memory=None, actor=owner
    ),
    "memory_write:restore": lambda ws, owner: memory_services.restore_memory(
        memory=None, actor=owner
    ),
    "memory_write:forget": lambda ws, owner: memory_services.forget_memory(
        memory=None, actor=owner
    ),
    "memory_write:delete": lambda ws, owner: memory_services.delete_memory(
        memory=None, actor=owner, channel="web"
    ),
    "memory_write:privatize": lambda ws, owner: memory_services.privatize_conversation_memories(
        conversation=None, actor=owner
    ),
    "memory_write:self_block": lambda ws, owner: memory_services.set_assembled_self_block(
        workspace=ws, content="x"
    ),
    "memory_write:core_rollback": lambda ws, owner: memory_services.rollback_core_block(
        workspace=ws, label="human", version=1, actor=owner, channel="web"
    ),
    "memory_write:core_reject": lambda ws, owner: memory_services.reject_core_block_proposal(
        proposal=None, actor=owner, channel="web"
    ),
    "task_write:complete": lambda ws, owner: task_services.complete_task(task=None, actor=owner),
    "task_write:delete": lambda ws, owner: task_services.delete_task(
        task=None, actor=owner, channel="web"
    ),
    "task_write:restore": lambda ws, owner: task_services.restore_task(
        task=None, actor=owner, channel="web"
    ),
    "task_write:purge": lambda ws, owner: task_services.purge_trashed_task(
        None, reason="retention"
    ),
    "fact_write:propose": lambda ws, owner: graph_services.propose_fact(
        workspace=ws, subject=None, predicate="x", valid_from=None, provenance_quote="q"
    ),
    "fact_write:create": lambda ws, owner: graph_services.create_fact(
        workspace=ws,
        actor=owner,
        channel="web",
        subject_kind="person",
        subject_name="A",
        predicate="knows",
    ),
    "node_invocation:read": lambda ws, owner: node_services.invoke_read(
        node=None, capability="system.info", args={}, workspace=ws, actor=owner
    ),
    "node_invocation:confirm": lambda ws, owner: node_services.invoke_confirm(
        node_id=uuid.uuid4(), capability="x", args={}, actor=owner
    ),
    "node_invocation:gated": lambda ws, owner: node_services.create_gated_execution(None),
    "harness_job:research": lambda ws, owner: harness_services.create_harness_job(
        workspace=ws, actor=owner, repo=None, goal="g"
    ),
    "harness_job:implementation": lambda ws, owner: (
        harness_services.create_harness_implementation_job(
            workspace=ws, actor=owner, repo=None, goal="g"
        )
    ),
    "harness_job:push": lambda ws, owner: harness_services.request_harness_push(
        job=None, review=None
    ),
    "gateway_call:complete": lambda ws, owner: InferenceGateway().complete(
        "chat", [{"role": "user", "content": "x"}], workspace=ws
    ),
    "gateway_call:embed": lambda ws, owner: InferenceGateway().embed("memory.embed", ["x"]),
    "gateway_call:transcribe": lambda ws, owner: InferenceGateway().transcribe("voice.stt", b""),
    "gateway_call:synthesize": lambda ws, owner: InferenceGateway().synthesize(
        "voice.tts", "Merhaba."
    ),
    "celery_dispatch:eager": lambda ws, owner: sweep_expired_approval_requests.apply_async(),
}


@pytest.mark.parametrize("effect", sorted(EFFECTS))
def test_every_effect_is_denied_inside_a_run_with_one_audit_row(owner, workspace, effect):
    before = {
        "approvals": ApprovalRequest.objects.count(),
        "tasks": Task.all_objects.count(),
        "memories": Memory.objects.count(),
        "usage": ModelUsage.objects.count(),
    }
    with pytest.raises(UnattendedDenied) as excinfo:
        with recorded_unattended_run("run-x", workspace=workspace, source="test"):
            EFFECTS[effect](workspace, owner)
    assert excinfo.value.effect == effect.split(":")[0]
    assert _effects() == [effect.split(":")[0]]
    assert ApprovalRequest.objects.count() == before["approvals"]
    assert Memory.objects.count() == before["memories"]
    assert ModelUsage.objects.count() == before["usage"]
    assert Task.all_objects.count() == before["tasks"]


def test_outside_a_run_the_services_behave_as_before(owner, workspace):
    task = task_services.create_task(workspace=workspace, actor=owner, title="Attended")
    assert task.pk and _denials() == []


def test_the_allowlisted_purpose_passes_the_guard(owner, workspace):
    """`proactive.digest` inside a morning run is not refused by the guard
    (it may still fail for want of a provider, which is not a denial)."""
    with recorded_unattended_run(
        "run-d", allowed_purposes=frozenset({"proactive.digest"}), workspace=workspace
    ):
        try:
            InferenceGateway().complete(
                "proactive.digest", [{"role": "user", "content": "x"}], workspace=workspace
            )
        except UnattendedDenied:  # pragma: no cover - the failure being tested
            pytest.fail("an allowlisted purpose was denied")
        except Exception:  # noqa: BLE001 -- no provider configured here
            pass
    assert _denials() == []


@pytest.mark.filterwarnings("ignore::celery.exceptions.AlwaysEagerIgnored")
def test_a_real_broker_publish_is_refused_before_it_leaves(owner, workspace, monkeypatch):
    """The non-eager path: `before_task_publish` refuses the publish."""
    published = []
    monkeypatch.setattr(
        "kombu.messaging.Producer.publish", lambda self, *a, **kw: published.append(a)
    )
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("run-p", workspace=workspace):
            celery_app.send_task("memory.tasks.recover_stale_extraction_jobs")
    assert published == []
    assert _effects() == ["celery_dispatch"]


def test_the_proactive_tasks_themselves_may_be_dispatched(owner, workspace):
    with recorded_unattended_run("run-ok", workspace=workspace):
        prune.apply_async()
    assert _denials() == []


@pytest.mark.parametrize("name", registry.list_tool_names())
def test_every_registered_gated_tool_is_denied_inside_a_run(owner, workspace, name):
    """R5: walk every registered `ToolSpec`: no approval request for any of
    them can be created inside a run."""
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("run-t", workspace=workspace):
            approval_services.create_request(
                workspace=workspace,
                actor=owner,
                tool_name=name,
                payload={},
                origin=ApprovalRequest.Origin.AGENT,
                channel="system",
                system_created=True,
            )
    assert ApprovalRequest.objects.count() == 0
    assert _effects() == ["approval_request"]


def test_every_registered_read_tool_is_denied_inside_a_run(owner, workspace):
    names = registry.list_read_tool_names()
    for name in names:
        spec = registry.get_read_tool(name)
        with pytest.raises(UnattendedDenied):
            with recorded_unattended_run("run-r", workspace=workspace):
                registry.run_read_tool(
                    spec, workspace=workspace, actor=owner, args={}, channel="web"
                )
    assert _effects() == ["read_tool"] * len(names)


# --- through a real run ---------------------------------------------------


def test_a_denial_rolls_back_its_probe_block_and_is_audited_once(owner, workspace, fake_probe):
    def try_to_write(ws, actor, now):
        Task.objects.create(workspace=ws, title="smuggled", created_by=actor)
        task_services.create_task(workspace=ws, actor=actor, title="via service")

    fake_probe.on_collect = try_to_write
    schedule = make_schedule(workspace, owner)
    run = run_schedule(schedule)
    assert not Task.objects.filter(title__in=["smuggled", "via service"]).exists()
    assert _effects() == ["task_write"]
    [row] = _denials()
    assert row.metadata["run_id"] == str(run.id)
    assert row.metadata["step"] == fake_probe.spec.name
    assert row.workspace == workspace
    assert run.outcome == ProactiveRun.Outcome.DENIED
    assert run.denials == 1
    assert run.probe_failures == {fake_probe.spec.name: "denied"}


def test_a_denial_in_a_step_that_commits_is_audited_once(owner, workspace, fake_probe):
    """A (badly written) probe that catches the denial itself: its block
    commits, the recorder flushes once, the end-of-run flush adds nothing."""

    def swallow(ws, actor, now):
        try:
            with transaction.atomic():
                task_services.create_task(workspace=ws, actor=actor, title="x")
        except UnattendedDenied:
            pass

    fake_probe.on_collect = swallow
    run = run_schedule(make_schedule(workspace, owner))
    assert len(_denials()) == 1
    assert run.outcome == ProactiveRun.Outcome.QUIET


def test_every_effect_is_denied_inside_a_real_run(owner, workspace, fake_probe):
    """Each D2 effect attempted from a probe ends that step `denied`, and the
    run continues with the next probe."""
    attempted = sorted(EFFECTS)
    attempts = iter(attempted)

    def attempt(ws, actor, now):
        EFFECTS[next(attempts)](ws, actor)

    fake_probe.on_collect = attempt
    schedule = make_schedule(workspace, owner)
    for _ in attempted:
        run_schedule(schedule)
    assert sorted(_effects()) == sorted(effect.split(":")[0] for effect in attempted)
    assert ProactiveRun.objects.filter(outcome=ProactiveRun.Outcome.DENIED).count() == len(
        attempted
    )
