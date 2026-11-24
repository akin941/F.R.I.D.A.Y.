"""Phase 12 D1/D6 workspace axis: a probe receives exactly one workspace and
sees only its rows; notices land in the run's workspace; a result whose
data class does not match the workspace (firm data in a personal run, or
the reverse) is refused; `proactive.api` reads are `for_actor()`-scoped."""

import pytest

from core import probes
from proactive import api
from proactive.models import ProactiveNotice
from proactive.runs import run_schedule
from proactive.tests.conftest import make_schedule
from tasks.models import Task
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def _tasks_probe():
    """A fake probe over real rows: open tasks of the given workspace."""

    def collect(workspace, owner, now):
        rows = list(Task.objects.filter(workspace=workspace).order_by("created_at"))
        data_class = "personal" if workspace.type == "personal" else "firm"
        return probes.ProbeResult(
            count=len(rows),
            data_class=data_class,
            items=tuple(probes.ProbeItem(key=str(t.pk), label=t.title) for t in rows[:5]),
            item_keys=tuple(str(t.pk) for t in rows),
        )

    spec = probes.ProbeSpec(
        name="tasks.fake_open",
        owner_app="tasks",
        collect=collect,
        digest_section="tasks",
        notice_kind="tasks.fake_open",
        link="/tasks",
        notice_template="Görev: {label}",
    )
    probes.register_probe(spec)
    return spec


def test_each_probe_sees_only_its_workspace(owner, workspace, firm, member):
    _tasks_probe()
    other = Workspace.objects.get(owner=member, type=Workspace.Type.PERSONAL)
    schedules = {ws.pk: make_schedule(ws, ws.owner) for ws in (workspace, firm, other)}
    for ws in (workspace, firm, other):
        run_schedule(schedules[ws.pk])  # baselines
    Task.objects.create(workspace=workspace, title="Kişisel iş", created_by=owner)
    Task.objects.create(workspace=firm, title="Firma işi", created_by=owner)
    Task.objects.create(workspace=other, title="Başkasının işi", created_by=member)
    for ws in (workspace, firm, other):
        run_schedule(schedules[ws.pk])
    by_workspace = {
        ws.pk: list(ProactiveNotice.objects.filter(workspace=ws).values_list("title", "data_class"))
        for ws in (workspace, firm, other)
    }
    assert by_workspace[workspace.pk] == [("Görev: Kişisel iş", "personal")]
    assert by_workspace[firm.pk] == [("Görev: Firma işi", "firm")]
    assert by_workspace[other.pk] == [("Görev: Başkasının işi", "personal")]


@pytest.mark.parametrize("wrong", ["firm-in-personal", "personal-in-firm"])
def test_a_result_of_the_wrong_data_class_is_refused(owner, workspace, firm, fake_probe, wrong):
    target = workspace if wrong == "firm-in-personal" else firm
    fake_probe.data_class = "firm" if wrong == "firm-in-personal" else "personal"
    schedule = make_schedule(target, owner)
    run_schedule(schedule)
    fake_probe.items[target.pk] = [("a", "A")]
    run = run_schedule(schedule)
    assert ProactiveNotice.objects.count() == 0
    assert run.probe_failures == {fake_probe.spec.name: "probe_invalid:data_class_mismatch"}


def test_api_reads_are_scoped_to_the_actor_and_workspace(owner, workspace, firm, member):
    for ws in (workspace, firm):
        ProactiveNotice.objects.create(
            workspace=ws,
            kind="tasks.fake_due",
            dedupe_key="a",
            title=f"Yeni: {ws.name}",
            link="/tasks",
            data_class="personal" if ws.type == "personal" else "firm",
        )
    section = api.notice_section(actor=owner, workspace=workspace)
    assert section == {
        "unread": 1,
        "latest": [{"title": f"Yeni: {workspace.name}", "link": "/tasks"}],
    }
    # The member sees nothing of the owner's personal workspace.
    assert api.notice_section(actor=member, workspace=workspace) == {"unread": 0, "latest": []}
    assert [n["title"] for n in api.unread_notices(actor=owner, workspace=firm)] == [
        f"Yeni: {firm.name}"
    ]
