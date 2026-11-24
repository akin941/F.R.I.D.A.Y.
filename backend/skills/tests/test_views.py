"""Phase 12 D14/D20: role x endpoint for every `/skills` endpoint over the
Phase 4 principals, the `cli`/`openai`/`hook` PATs, a node secret, a
`node_voice` token, a kill-switched owner and a firm workspace with a
non-owner member; no delete (405), no bulk endpoint; another workspace's
rows are 404; the `skills.proposals` probe."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import Resolver404, resolve
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token, pair_node
from core import probes
from skills.models import InstructionSkill, InstructionSkillVersion
from skills.probes import SPEC
from skills.tests.helpers import (
    BASE,
    PASSWORD,
    active_skill,
    make_firm,
    make_owner,
    personal_of,
    propose,
    skill_text,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def workspace(owner):
    return personal_of(owner)


@pytest.fixture
def member():
    return register_user(email="member@test.dev", password=PASSWORD, name="Member")


@pytest.fixture
def firm(owner, member):
    return make_firm(owner, member)


@pytest.fixture
def rows(owner, workspace, firm):
    """In each workspace: an active skill with a pending new version, and a
    second active skill to archive."""
    result = {}
    for key, target in (("personal", workspace), ("firm", firm)):
        skill = active_skill(target, owner)
        pending = propose(target, owner, skill_text(body="Yeni sürüm."))
        archivable = active_skill(target, owner, skill_text(name="arsivlik"))
        result[key] = {"skill": skill, "pending": pending, "archivable": archivable}
    return result


@pytest.fixture
def principals(owner, workspace, member, firm, web_client):
    _, secret = pair_node(owner)
    voice = mint_token(secret)

    def make(principal):
        if principal in ("owner-web", "owner-firm"):
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            return bearer(make_pat(owner, workspace, principal.removeprefix("owner-pat-"))[0])
        if principal == "node-secret":
            return bearer(secret)
        if principal == "node-voice":
            return bearer(voice)
        if principal in ("non-owner", "firm-member"):
            return web_client(member)
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        return APIClient()

    return make


def _upload():
    return SimpleUploadedFile(
        "SKILL.md", skill_text(name="yuklenen").encode(), content_type="text/markdown"
    )


ENDPOINTS = {
    "list": lambda c, q, r: c.get(f"{BASE}{q}"),
    "create": lambda c, q, r: c.post(
        f"{BASE}{q}", {"text": skill_text(name="yazilan")}, format="json"
    ),
    "upload": lambda c, q, r: c.post(f"{BASE}/upload{q}", {"file": _upload()}, format="multipart"),
    "detail": lambda c, q, r: c.get(f"{BASE}/{r['skill'].pk}"),
    "new-version": lambda c, q, r: c.post(
        f"{BASE}/{r['skill'].pk}/versions", {"text": skill_text(body="Üçüncü.")}, format="json"
    ),
    "approve": lambda c, q, r: c.post(
        f"{BASE}/versions/{r['pending'].pk}/approve",
        {"sha256": r["pending"].content_sha256},
        format="json",
    ),
    "edit-approve": lambda c, q, r: c.post(
        f"{BASE}/versions/{r['pending'].pk}/edit-approve",
        {"sha256": r["pending"].content_sha256, "text": skill_text(body="Düzeltilmiş.")},
        format="json",
    ),
    "reject": lambda c, q, r: c.post(
        f"{BASE}/versions/{r['pending'].pk}/reject", {}, format="json"
    ),
    "archive": lambda c, q, r: c.post(f"{BASE}/{r['archivable'].pk}/archive"),
}

MATRIX = [
    ("owner-web", 200),
    ("owner-firm", 200),
    ("kill-switched-owner", 200),
    ("owner-pat-" + PersonalAccessToken.Kind.CLI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.OPENAI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.HOOK, 401),
    ("node-secret", 401),
    ("node-voice", 401),
    ("non-owner", 403),
    ("firm-member", 403),
    ("agent", 403),
    ("anonymous", 401),
]


def _snapshot():
    return (
        sorted(InstructionSkill.objects.values_list("id", "status", "current_version_id")),
        sorted(InstructionSkillVersion.objects.values_list("id", "decision")),
    )


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
        assert response.status_code in (200, 201), response.content
    else:
        assert response.status_code == expected, response.content
        assert _snapshot() == before


def test_restore(owner, web_client, rows):
    client = web_client(owner)
    skill = rows["personal"]["archivable"]
    assert client.post(f"{BASE}/{skill.pk}/archive").status_code == 200
    response = client.post(f"{BASE}/{skill.pk}/restore")
    assert response.status_code == 200
    assert response.data["status"] == "active"


@pytest.mark.parametrize("principal", ["non-owner", "node-voice", "owner-pat-cli", "agent"])
def test_restore_refused(principals, rows, principal):
    skill = rows["personal"]["archivable"]
    skill.status = "archived"
    skill.save(update_fields=["status"])
    response = principals(principal).post(f"{BASE}/{skill.pk}/restore")
    assert response.status_code in (401, 403)
    skill.refresh_from_db()
    assert skill.status == "archived"


def test_no_delete_endpoint(owner, web_client, rows):
    client = web_client(owner)
    skill = rows["personal"]["skill"]
    assert client.delete(f"{BASE}/{skill.pk}").status_code == 405
    assert (
        client.delete(f"{BASE}/versions/{rows['personal']['pending'].pk}/reject").status_code == 405
    )
    assert InstructionSkill.objects.filter(pk=skill.pk).exists()


def test_no_bulk_endpoint():
    for path in (
        "/api/v1/skills/bulk-approve",
        "/api/v1/skills/versions/bulk",
        "/api/v1/skills/approve",
    ):
        with pytest.raises(Resolver404):
            resolve(path)


def test_another_workspace_is_404(owner, web_client, member):
    foreign_owner_ws = personal_of(member)
    client = web_client(owner)
    assert client.get(f"{BASE}?workspace={foreign_owner_ws.pk}").status_code == 404
    assert client.get(f"{BASE}?workspace=not-a-uuid").status_code == 404


def test_another_workspaces_rows_are_404(owner, web_client, member):
    from skills import services

    foreign_ws = personal_of(member)
    parsed = services.parse_text(skill_text())
    # Seeded directly: only the workspace owner may propose over the web.
    version = services.create_version(
        workspace=foreign_ws, parsed=parsed, proposed_by="owner", origin="owner", actor=member
    )
    client = web_client(owner)
    assert client.get(f"{BASE}/{version.skill_id}").status_code == 404
    assert (
        client.post(
            f"{BASE}/versions/{version.pk}/approve",
            {"sha256": version.content_sha256},
            format="json",
        ).status_code
        == 404
    )
    assert client.post(f"{BASE}/{version.skill_id}/archive").status_code == 404


def test_list_is_scoped_and_filtered(owner, web_client, workspace, firm, rows):
    client = web_client(owner)
    personal = client.get(f"{BASE}?workspace={workspace.pk}").data
    assert {s["workspace"] for s in personal["skills"]} == {str(workspace.pk)}
    assert {s["name"] for s in personal["skills"]} == {"pr-yaz", "arsivlik"}
    pending = next(s for s in personal["skills"] if s["name"] == "pr-yaz")
    assert pending["pending_versions"] == 1
    only = client.get(f"{BASE}?workspace={workspace.pk}&status=archived").data
    assert only["skills"] == []
    assert client.get(f"{BASE}?status=bogus").status_code == 400


def test_approve_with_a_stale_hash_is_version_changed(owner, web_client, rows):
    pending = rows["personal"]["pending"]
    response = web_client(owner).post(
        f"{BASE}/versions/{pending.pk}/approve", {"sha256": "a" * 64}, format="json"
    )
    assert response.status_code == 400
    assert response.data["code"] == "skill.version_changed"


def test_a_refused_text_reports_the_parser_reason(owner, web_client):
    response = web_client(owner).post(
        BASE,
        {"text": "---\nname: ab\ndescription: x\nallowed-tools: Bash\n---\nb\n"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "skill.invalid"
    assert response.data["reason"] == "permission_key_refused"


def test_detail_shows_every_version(owner, web_client, rows):
    data = web_client(owner).get(f"{BASE}/{rows['personal']['skill'].pk}").data
    assert [v["version"] for v in data["versions"]] == [2, 1]
    assert data["versions"][0]["decision"] == "pending"
    assert data["versions"][0]["text"].startswith('---\nname: "pr-yaz"')


# --- the skills.proposals probe ------------------------------------------------


def test_the_probe_is_registered_at_boot():
    assert probes.get_probe("skills.proposals") is SPEC


def test_the_probe_reports_pending_versions(owner, workspace, firm, rows):
    from django.utils import timezone

    from skills import ports

    ports.propose_version(
        workspace=workspace, text=skill_text(name="model-adi"), proposed_by="curator"
    )
    result = SPEC.collect(workspace, owner, timezone.now())
    probes.validate_result(SPEC, result, max_keys=200)
    assert result.count == 2
    labels = sorted(item.label for item in result.items)
    assert labels == ["Beceri: pr-yaz", "Beceri: yeni beceri"]
    assert result.data_class == "personal"
    firm_result = SPEC.collect(firm, owner, timezone.now())
    assert firm_result.count == 1 and firm_result.data_class == "firm"
    assert set(result.all_keys()).isdisjoint(firm_result.all_keys())
