# ruff: noqa: F811 -- pytest fixtures imported from test_views are redefined as arguments
"""Phase 12 D16/D20: role x endpoint for the curator surface
(`GET /api/v1/skills/curator`, `POST /api/v1/skills/curator/dismiss`) over
the same principals as every `/skills` endpoint; another workspace's run is
404; archive suggestions reach the `skills.proposals` probe with the
approved skill's name and disappear once archived or dismissed."""

import pytest
from django.utils import timezone

from audit.models import AuditLog
from core import probes
from core.channels import CHANNEL_WEB
from skills import services
from skills.models import SkillCuratorRun
from skills.probes import SPEC
from skills.tests.helpers import BASE, active_skill, personal_of, skill_text
from skills.tests.test_views import (  # noqa: F401
    MATRIX,
    firm,
    member,
    owner,
    principals,
    workspace,
)

pytestmark = pytest.mark.django_db


def _run(workspace, skills, week="2026-W47"):
    return SkillCuratorRun.objects.create(
        workspace=workspace,
        week=week,
        started_at=timezone.now(),
        finished_at=timezone.now(),
        outcome=SkillCuratorRun.Outcome.PROPOSALS,
        archive_suggestions=[
            {"skill_id": str(skill.pk), "reason": "unused", "dismissed": False} for skill in skills
        ],
    )


@pytest.fixture
def rows(owner, workspace, firm):
    result = {}
    for key, target in (("personal", workspace), ("firm", firm)):
        skill = active_skill(target, owner, skill_text(name="bosta"))
        result[key] = {"skill": skill, "run": _run(target, [skill])}
    return result


def _snapshot():
    return sorted(
        (str(run.pk), [s["dismissed"] for s in run.archive_suggestions])
        for run in SkillCuratorRun.objects.all()
    )


ENDPOINTS = {
    "curator": lambda c, q, r: c.get(f"{BASE}/curator{q}"),
    "dismiss": lambda c, q, r: c.post(
        f"{BASE}/curator/dismiss",
        {"run_id": str(r["run"].pk), "skill_id": str(r["skill"].pk)},
        format="json",
    ),
}


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
@pytest.mark.parametrize(("principal", "expected"), MATRIX)
def test_role_matrix(principals, workspace, firm, rows, endpoint, principal, expected):
    client = principals(principal)
    in_firm = principal in ("owner-firm", "firm-member")
    target = firm if in_firm else workspace
    before = _snapshot()
    response = ENDPOINTS[endpoint](
        client, f"?workspace={target.pk}", rows["firm" if in_firm else "personal"]
    )
    if expected == 200:
        assert response.status_code in (200, 204), response.content
    else:
        assert response.status_code == expected, response.content
        assert _snapshot() == before


def test_the_curator_view_lists_open_suggestions(owner, web_client, workspace, rows):
    data = web_client(owner).get(f"{BASE}/curator?workspace={workspace.pk}").data
    assert data["latest_run"]["id"] == str(rows["personal"]["run"].pk)
    assert data["latest_run"]["outcome"] == "proposals"
    [suggestion] = data["suggestions"]
    assert suggestion["skill_name"] == "bosta" and suggestion["reason"] == "unused"
    assert suggestion["skill_id"] == str(rows["personal"]["skill"].pk)


def test_no_run_yet(owner, web_client, workspace):
    data = web_client(owner).get(f"{BASE}/curator?workspace={workspace.pk}").data
    assert data == {"workspace": str(workspace.pk), "latest_run": None, "suggestions": []}


def test_dismiss_is_one_item_and_audited(owner, web_client, workspace, rows):
    client = web_client(owner)
    payload = {
        "run_id": str(rows["personal"]["run"].pk),
        "skill_id": str(rows["personal"]["skill"].pk),
    }
    assert client.post(f"{BASE}/curator/dismiss", payload, format="json").status_code == 204
    row = AuditLog.objects.get(action="skills.curator.suggestion_dismissed")
    assert row.metadata["skill_id"] == payload["skill_id"]
    again = client.post(f"{BASE}/curator/dismiss", payload, format="json")
    assert again.status_code == 400 and again.data["code"] == "skill.invalid_state"
    assert client.get(f"{BASE}/curator?workspace={workspace.pk}").data["suggestions"] == []
    # The skill itself is untouched.
    rows["personal"]["skill"].refresh_from_db()
    assert rows["personal"]["skill"].status == "active"


def test_another_workspaces_run_is_404(owner, web_client, member):
    foreign = personal_of(member)
    # Seeded directly: only the installation owner may propose over the web.
    version = services.create_version(
        workspace=foreign,
        parsed=services.parse_text(skill_text(name="yabanci")),
        proposed_by="owner",
        origin="owner",
        actor=member,
    )
    skill = version.skill
    run = _run(foreign, [skill])
    client = web_client(owner)
    response = client.post(
        f"{BASE}/curator/dismiss",
        {"run_id": str(run.pk), "skill_id": str(skill.pk)},
        format="json",
    )
    assert response.status_code == 404
    assert client.get(f"{BASE}/curator?workspace={foreign.pk}").status_code == 404


def test_a_bad_body_is_400(owner, web_client):
    response = web_client(owner).post(
        f"{BASE}/curator/dismiss", {"run_id": "x", "skill_id": "y"}, format="json"
    )
    assert response.status_code == 400


def test_the_probe_shows_archive_suggestions_until_decided(owner, workspace, firm, rows):
    result = SPEC.collect(workspace, owner, timezone.now())
    probes.validate_result(SPEC, result, max_keys=200)
    assert result.count == 1
    [item] = result.items
    assert item.label == "Arşiv önerisi: bosta"
    assert item.key == f"archive:{rows['personal']['run'].pk}:{rows['personal']['skill'].pk}"
    assert item.link == f"/skills/{rows['personal']['skill'].pk}"
    firm_result = SPEC.collect(firm, owner, timezone.now())
    assert firm_result.count == 1
    assert set(result.all_keys()).isdisjoint(firm_result.all_keys())

    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=rows["personal"]["skill"])
    assert SPEC.collect(workspace, owner, timezone.now()).count == 0


def test_only_the_latest_run_counts(owner, workspace, rows):
    skill = rows["personal"]["skill"]
    _run(workspace, [], week="2026-W48")
    assert SPEC.collect(workspace, owner, timezone.now()).count == 0
    assert skill.status == "active"
