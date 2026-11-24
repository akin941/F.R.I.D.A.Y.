"""Phase 12 D5/D7/D20 (branch 2): the HUD `notices` section and digest
card -- counts and titles only (no body), `for_actor()`-scoped to the
stream's workspace, a delta on a new notice, and nothing marked read."""

import pytest

from hud import services
from proactive.models import DigestRun, ProactiveNotice, ProactiveRun
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db


def _notice(workspace, title, key, *, read=False):
    from django.utils import timezone

    return ProactiveNotice.objects.create(
        workspace=workspace,
        kind="tasks.due_today",
        dedupe_key=key,
        title=title,
        count=1,
        link="/tasks",
        data_class="personal" if workspace.type == "personal" else "firm",
        read_at=timezone.now() if read else None,
    )


def _firm(owner):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    return firm


def test_notices_section_counts_and_titles_only(owner, workspace):
    firm = _firm(owner)
    for index in range(7):
        _notice(workspace, f"Bugün: görev {index}", f"k{index}")
    _notice(workspace, "Okundu", "read", read=True)
    _notice(firm, "Firma bildirimi", "f1")

    section = services.notices_section(owner, workspace)
    assert section["unread"] == 7
    assert len(section["latest"]) == 5
    assert all(set(item) == {"title", "link"} for item in section["latest"])
    titles = [item["title"] for item in section["latest"]]
    assert "Okundu" not in titles and "Firma bildirimi" not in titles

    firm_section = services.notices_section(owner, firm)
    assert firm_section == {
        "unread": 1,
        "latest": [{"title": "Firma bildirimi", "link": "/tasks"}],
    }
    # Reading the section marks nothing read.
    assert ProactiveNotice.objects.filter(read_at__isnull=True).count() == 8


def test_notices_and_digest_are_hud_sections(owner, workspace):
    assert "notices" in services.SECTIONS and "digest" in services.SECTIONS
    built = services.build_sections(owner, workspace)
    assert built["notices"] == {"unread": 0, "latest": []}
    assert built["digest"] is None


def test_the_digest_card(owner, workspace):
    run = ProactiveRun.objects.create(
        workspace=workspace, kind="morning_digest", started_at="2026-11-24T07:45:00Z"
    )
    digest = DigestRun.objects.create(
        workspace=workspace,
        run=run,
        sections=[{"section": "tasks", "entries": []}],
        spoken_text="Günaydın. 2 görevin bugün bitiyor.",
        composed_by="template",
        data_class="personal",
    )
    card = services.build_sections(owner, workspace)["digest"]
    assert card["id"] == str(digest.pk)
    assert card["spoken_text"] == "Günaydın. 2 görevin bugün bitiyor."
    assert card["opened"] is False and card["spoken"] is False
    assert "sections" not in card
    assert services.build_sections(owner, _firm(owner))["digest"] is None


def test_a_new_notice_is_a_delta(stream, workspace, owner, fake_clock):
    fake_clock.ticks = [lambda: _notice(workspace, "Bugün: Fatura", "new")]
    frames = stream(workspace.id)
    assert frames[1]["notices"] == {"unread": 0, "latest": []}
    delta = frames[2]
    assert delta["type"] == "delta"
    assert set(delta) == {"type", "notices"}
    assert delta["notices"] == {
        "unread": 1,
        "latest": [{"title": "Bugün: Fatura", "link": "/tasks"}],
    }
