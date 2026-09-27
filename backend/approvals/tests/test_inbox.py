"""The cross-workspace approvals inbox (Phase 4 branch 5): `scope=all` on
`GET /api/v1/approvals/requests`, the inbox filters, `expires_at`/`origin`
on every row, and the read-only `GET /api/v1/approvals/requests/<id>`.

Role x endpoint against the Phase 4 principals -- owner web session, owner
PAT, non-owner member, `is_agent` account, anonymous -- plus two-user,
two-workspace isolation."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from approvals.models import ApprovalRequest
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

URL = "/api/v1/approvals/requests"
PASSWORD = "Str0ngDevPass!42"

_TOOL = SimpleNamespace(
    gated=True,
    describe_execution=None,
    describe_request=lambda request: {"kind": "test", "id": str(request.id)},
)


@pytest.fixture(autouse=True)
def _resolve_tool():
    with mock.patch("approvals.services._resolve_tool", lambda name: _TOOL):
        yield


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save(update_fields=["is_owner"])
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def firm(owner):
    firm = Workspace.objects.create(name="Firma", owner=owner, type=Workspace.Type.COMPANY)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    return firm


@pytest.fixture
def stranger():
    return register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")


@pytest.fixture
def stranger_workspace(stranger):
    return Workspace.objects.get(owner=stranger)


def make(workspace, **fields):
    fields.setdefault("tool_name", "some_tool")
    return ApprovalRequest.objects.create(workspace=workspace, **fields)


def ids(response):
    return {row["id"] for row in response.json()}


def pat_client(user, workspace):
    raw = f"jpat_inbox-{workspace.id.hex}"
    PersonalAccessToken.objects.create(
        user=user, workspace=workspace, name="cli", token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def member(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def agent_account(workspace):
    agent = member(workspace, WorkspaceMember.Role.ADMIN, "agent@test.dev")
    agent.is_agent = True
    agent.save(update_fields=["is_agent"])
    return agent


# --- scope=all: owner web session ---


def test_owner_web_sees_every_membership_labeled_with_its_workspace(
    owner, personal, firm, stranger_workspace, web_client
):
    mine = make(personal)
    theirs = make(firm)
    make(stranger_workspace)

    response = web_client(owner).get(URL, {"scope": "all"})

    assert response.status_code == 200
    body = {row["id"]: row for row in response.json()}
    assert set(body) == {str(mine.id), str(theirs.id)}
    assert body[str(mine.id)]["workspace"] == str(personal.id)
    assert body[str(theirs.id)]["workspace"] == str(firm.id)


def test_scope_all_newest_first(owner, personal, firm, web_client):
    older = make(personal)
    ApprovalRequest.objects.filter(id=older.id).update(
        created_at=timezone.now() - timedelta(hours=1)
    )
    newer = make(firm)

    response = web_client(owner).get(URL, {"scope": "all"})

    assert [row["id"] for row in response.json()] == [str(newer.id), str(older.id)]


def test_scope_all_workspace_param_narrows(owner, personal, firm, web_client):
    make(personal)
    theirs = make(firm)

    response = web_client(owner).get(URL, {"scope": "all", "workspace": str(firm.id)})

    assert response.status_code == 200
    assert ids(response) == {str(theirs.id)}


def test_scope_all_foreign_workspace_param_is_404(owner, stranger_workspace, web_client):
    make(stranger_workspace)
    response = web_client(owner).get(URL, {"scope": "all", "workspace": str(stranger_workspace.id)})
    assert response.status_code == 404
    assert response.json() == {"code": "not_found"}


def test_scope_all_invalid_workspace_is_400(owner, web_client):
    response = web_client(owner).get(URL, {"scope": "all", "workspace": "nope"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid"


def test_unknown_scope_is_400(owner, personal, web_client):
    response = web_client(owner).get(URL, {"scope": "everything", "workspace": str(personal.id)})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid"


def test_scope_workspace_still_requires_workspace_for_web(owner, web_client):
    assert web_client(owner).get(URL, {"scope": "workspace"}).status_code == 400


def test_display_summary_for_every_curated_workspace(owner, personal, firm, web_client):
    make(personal)
    make(firm)
    response = web_client(owner).get(URL, {"scope": "all"})
    assert all(row["display_summary"]["kind"] == "test" for row in response.json())


# --- filters ---


def test_filters_status_tool_origin_and_requested_channel(owner, personal, firm, web_client):
    match = make(
        firm,
        tool_name="harness_push",
        origin=ApprovalRequest.Origin.HARNESS,
        requested_channel=ApprovalRequest.RequestedChannel.SYSTEM,
    )
    make(personal, tool_name="harness_push", origin=ApprovalRequest.Origin.HARNESS)
    make(
        firm,
        tool_name="harness_push",
        origin=ApprovalRequest.Origin.HARNESS,
        requested_channel=ApprovalRequest.RequestedChannel.SYSTEM,
        status=ApprovalRequest.Status.APPROVED,
    )
    make(firm, origin=ApprovalRequest.Origin.HARNESS, requested_channel="system")

    response = web_client(owner).get(
        URL,
        {
            "scope": "all",
            "status": "pending",
            "tool": "harness_push",
            "origin": "harness",
            "requested_channel": "system",
        },
    )

    assert response.status_code == 200
    assert ids(response) == {str(match.id)}


@pytest.mark.parametrize(
    "param,value",
    [
        ("status", "bogus"),
        ("origin", "robot"),
        ("requested_channel", "carrier_pigeon"),
        ("tool", "x" * 101),
    ],
)
def test_invalid_filter_is_400(owner, personal, param, value, web_client):
    for scope in ("all", "workspace"):
        response = web_client(owner).get(
            URL, {"scope": scope, "workspace": str(personal.id), param: value}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid"


def test_filters_apply_to_the_workspace_scope_too(owner, personal, web_client):
    match = make(personal, origin=ApprovalRequest.Origin.AGENT)
    make(personal)
    response = web_client(owner).get(URL, {"workspace": str(personal.id), "origin": "agent"})
    assert ids(response) == {str(match.id)}


# --- scope=all: other principals ---


def test_pat_is_forced_to_its_bound_workspace_even_with_scope_all(owner, personal, firm):
    mine = make(personal)
    make(firm)
    client = pat_client(owner, personal)

    for params in (
        {"scope": "all"},
        {"scope": "all", "workspace": str(firm.id)},
        {"scope": "all", "workspace": str(personal.id)},
    ):
        response = client.get(URL, params)
        assert response.status_code == 200
        assert ids(response) == {str(mine.id)}
        # Low-trust channel: never the curator summary.
        assert all(row["display_summary"] is None for row in response.json())


def test_non_owner_member_sees_only_its_memberships(owner, personal, firm, web_client):
    make(personal)
    visible = make(firm)
    user = member(firm, WorkspaceMember.Role.MEMBER, "member@test.dev")
    own_personal = Workspace.objects.get(owner=user)
    own = make(own_personal)

    response = web_client(user).get(URL, {"scope": "all"})

    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert set(rows) == {str(visible.id), str(own.id)}
    # A member of the firm is not a curator there: no summary for its row.
    assert rows[str(visible.id)]["display_summary"] is None
    assert rows[str(own.id)]["display_summary"] is not None


def test_agent_account_is_refused_scope_all(personal, web_client):
    agent = agent_account(personal)
    response = web_client(agent).get(URL, {"scope": "all"})
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_anonymous_is_401():
    response = APIClient().get(URL, {"scope": "all"})
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_two_users_two_workspaces_isolation(
    owner, personal, stranger, stranger_workspace, web_client
):
    mine = make(personal)
    theirs = make(stranger_workspace)

    assert ids(web_client(owner).get(URL, {"scope": "all"})) == {str(mine.id)}
    assert ids(web_client(stranger).get(URL, {"scope": "all"})) == {str(theirs.id)}


def test_scope_all_query_count_is_bounded(
    owner, personal, firm, django_assert_max_num_queries, web_client
):
    for workspace in (personal, firm):
        for _ in range(3):
            make(workspace)
    for i in range(3):
        extra = Workspace.objects.create(name=f"firm {i}", owner=owner, type="company")
        WorkspaceMember.objects.create(workspace=extra, user=owner, role="owner")
        make(extra)
    # auth + list + one curator-role lookup per workspace (cached per row).
    with django_assert_max_num_queries(12):
        response = web_client(owner).get(URL, {"scope": "all"})
    assert len(response.json()) == 9


# --- row fields ---


def test_rows_carry_origin_and_expiry(owner, personal, settings, web_client):
    settings.APPROVAL_REQUEST_TTL_HOURS = 24
    pending = make(personal, origin=ApprovalRequest.Origin.HARNESS)
    decided = make(personal, status=ApprovalRequest.Status.REJECTED)

    response = web_client(owner).get(URL, {"scope": "all"})

    rows = {row["id"]: row for row in response.json()}
    assert rows[str(pending.id)]["origin"] == "harness"
    expected = pending.created_at + timedelta(hours=24)
    assert rows[str(pending.id)]["expires_at"] is not None
    from django.utils.dateparse import parse_datetime

    assert parse_datetime(rows[str(pending.id)]["expires_at"]) == expected
    assert rows[str(decided.id)]["expires_at"] is None
    assert "payload" not in rows[str(pending.id)]


# --- detail ---


def detail_url(approval_request):
    return f"{URL}/{approval_request.id}"


def test_detail_owner_web_reads_any_membership(owner, firm, web_client):
    request = make(firm)
    response = web_client(owner).get(detail_url(request))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(request.id)
    assert body["workspace"] == str(firm.id)
    assert body["display_summary"] == {"kind": "test", "id": str(request.id)}
    assert "payload" not in body


def test_detail_pat_reads_only_its_bound_workspace(owner, personal, firm):
    client = pat_client(owner, personal)
    assert client.get(detail_url(make(personal))).status_code == 200
    response = client.get(detail_url(make(firm)))
    assert response.status_code == 404


def test_detail_non_member_is_404(stranger, firm, web_client):
    response = web_client(stranger).get(detail_url(make(firm)))
    assert response.status_code == 404
    assert response.json() == {"code": "not_found"}


def test_detail_member_reads_without_summary(firm, web_client):
    request = make(firm)
    user = member(firm, WorkspaceMember.Role.MEMBER, "member@test.dev")
    response = web_client(user).get(detail_url(request))
    assert response.status_code == 200
    assert response.json()["display_summary"] is None


def test_detail_agent_account_reads_its_membership_but_never_decides(personal, web_client):
    request = make(personal)
    agent = agent_account(personal)
    client = web_client(agent)
    assert client.get(detail_url(request)).status_code == 200
    assert client.post(f"{detail_url(request)}/approve").status_code == 403


def test_detail_anonymous_is_401(personal):
    response = APIClient().get(detail_url(make(personal)))
    assert response.status_code == 401


def test_detail_unknown_id_is_404(owner, web_client):
    response = web_client(owner).get(f"{URL}/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_detail_is_read_only(owner, personal, method, web_client):
    response = getattr(web_client(owner), method)(detail_url(make(personal)))
    assert response.status_code == 405
