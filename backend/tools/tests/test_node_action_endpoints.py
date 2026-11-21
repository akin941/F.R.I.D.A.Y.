"""`POST/GET /api/v1/nodes/<id>/actions` (Phase 10 brief D8) and approving
a `node_action` over `/approvals`: the confirm-class path end to end with
the reference node, and role x endpoint for every principal."""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from approvals.models import ApprovalRequest
from approvals.services import create_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes import bus
from nodes.models import NodeActionExecution, NodeInvocation
from tools.tests.conftest import REPO_ARGS

OPEN_TERMINAL = {"capability": "apps.open", "args": {"bundle_id": "com.apple.Terminal"}}
PRINCIPALS = [
    "owner-web",
    "owner-cli",
    "owner-openai",
    "owner-hook",
    "node",
    "member",
    "curator",
    "agent",
    "anonymous",
]


def _bearer(raw):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def principal(kind, world, web_client):
    if kind == "owner-web":
        return web_client(world.owner)
    if kind.startswith("owner-"):
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            workspace=world.personal,
            user=world.owner,
            name=kind,
            kind=kind.removeprefix("owner-"),
            token_hash=hash_token(raw),
        )
        return _bearer(raw)
    if kind == "node":
        return _bearer(world.node_secret)
    if kind == "member":
        return web_client(world.member)
    if kind == "curator":
        return web_client(world.curator)
    if kind == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


def actions_url(node):
    return f"/api/v1/nodes/{node.pk}/actions"


# --- confirm class end to end -------------------------------------------


@pytest.mark.django_db
def test_open_an_app_end_to_end(node_world, make_link, web_client):
    link = make_link()
    response = web_client(node_world.owner).post(
        actions_url(node_world.node), OPEN_TERMINAL, format="json"
    )
    assert response.status_code == 202, response.content
    body = response.json()
    assert body["status"] == "sent" and body["capability_class"] == "confirm"
    assert body["approval_id"] is None
    assert not ApprovalRequest.objects.exists()
    link.pump()
    execution = NodeActionExecution.objects.get(pk=body["id"])
    assert (execution.status, execution.confirm_state) == ("ok", "approved")
    assert link.node.executor.calls[0]["bundle_id"] == "com.apple.Terminal"
    envelope_frames = [f for f in link.frames if f["type"] == "confirmation"]
    assert [f["state"] for f in envelope_frames] == ["shown", "approved"]
    row = NodeInvocation.objects.get(trace_id=execution.trace_id)
    assert (row.invocation_class, row.status, row.approval_id) == ("confirm", "ok", None)
    detail = web_client(node_world.owner).get(f"{actions_url(node_world.node)}/{execution.pk}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "ok"
    assert "tail" not in detail.json() and "args" not in detail.json()


@pytest.mark.django_db
def test_the_owner_can_deny_an_app_open_on_the_mac(node_world, make_link, web_client):
    link = make_link(decisions=(("deny", 0),))
    response = web_client(node_world.owner).post(
        actions_url(node_world.node), OPEN_TERMINAL, format="json"
    )
    link.pump()
    execution = NodeActionExecution.objects.get(pk=response.json()["id"])
    assert execution.status == "denied"
    assert link.node.executor.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"capability": "repo.command.run", "args": REPO_ARGS}, "not_confirm_class"),
        ({"capability": "apps.open", "args": {"bundle_id": "çom.apple.x"}}, "args_contract"),
        ({"capability": "apps.open", "args": {"bundle_id": "x"}}, "args_contract"),
        ({"capability": "git.status", "args": {"repo_path": "~/x"}}, "not_confirm_class"),
    ],
)
def test_only_valid_confirm_class_requests_are_accepted(node_world, web_client, body, code):
    response = web_client(node_world.owner).post(actions_url(node_world.node), body, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert not NodeActionExecution.objects.exists() and not NodeInvocation.objects.exists()


@pytest.mark.django_db
def test_an_offline_node_is_node_offline_and_nothing_is_queued(node_world, web_client):
    bus.clear_presence(node_world.node.pk)
    response = web_client(node_world.owner).post(
        actions_url(node_world.node), OPEN_TERMINAL, format="json"
    )
    assert response.status_code == 202
    assert response.json()["status"] == "node_offline"
    assert bus.drain_inbox(node_world.node.pk) == []


@pytest.mark.django_db
def test_another_users_node_is_404_with_one_chain_and_audit_row(node_world, web_client):
    response = web_client(node_world.owner).post(
        actions_url(node_world.other_node), OPEN_TERMINAL, format="json"
    )
    unknown = web_client(node_world.owner).post(
        "/api/v1/nodes/00000000-0000-4000-8000-000000000000/actions", OPEN_TERMINAL, format="json"
    )
    assert response.status_code == unknown.status_code == 404
    assert response.json() == unknown.json()
    row = NodeInvocation.objects.get(node=node_world.other_node)
    assert (row.status, row.refusal_code, row.nonce) == ("server_refused", "actor_not_owner", "")
    assert AuditLog.objects.filter(action="nodes.refused").count() == 2
    assert bus.drain_inbox(node_world.other_node.pk) == []
    assert not NodeActionExecution.objects.exists()


def test_only_the_actions_view_calls_invoke_confirm():
    """D8: the router, tools and drivers can never start a confirm-class
    action; only `nodes.views` calls `invoke_confirm`."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    callers = []
    for path in backend.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts or "migrations" in path.parts:
            continue
        text = path.read_text("utf-8")
        if "invoke_confirm(" in text:
            callers.append(path.relative_to(backend).as_posix())
    assert sorted(callers) == ["nodes/services.py", "nodes/views.py"]
    services_text = (backend / "nodes" / "services.py").read_text("utf-8")
    assert services_text.count("invoke_confirm(") == 1  # its definition only


# --- role x endpoint ----------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("kind", PRINCIPALS)
def test_role_matrix_post_actions(node_world, make_link, web_client, kind):
    make_link()
    client = principal(kind, node_world, web_client)
    response = client.post(actions_url(node_world.node), OPEN_TERMINAL, format="json")
    if kind == "owner-web":
        assert response.status_code == 202
        assert NodeActionExecution.objects.count() == 1
        return
    expected = {"member": 404, "curator": 404, "agent": 403, "anonymous": 401}
    assert response.status_code == expected.get(kind, response.status_code)
    assert response.status_code in (401, 403, 404)
    assert not NodeActionExecution.objects.exists()
    assert bus.drain_inbox(node_world.node.pk) == []


@pytest.mark.django_db
@pytest.mark.parametrize("kind", PRINCIPALS)
def test_role_matrix_get_action(node_world, make_link, web_client, kind):
    make_link()
    created = web_client(node_world.owner).post(
        actions_url(node_world.node), OPEN_TERMINAL, format="json"
    )
    url = f"{actions_url(node_world.node)}/{created.json()['id']}"
    response = principal(kind, node_world, web_client).get(url)
    if kind == "owner-web":
        assert response.status_code == 200
        return
    expected = {"member": 404, "curator": 404, "agent": 403, "anonymous": 401}
    assert response.status_code == expected.get(kind, response.status_code)
    assert response.status_code in (401, 403, 404)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", PRINCIPALS)
def test_role_matrix_approving_a_node_action(node_world, web_client, kind):
    approval = create_request(
        workspace=node_world.firm,
        actor=node_world.owner,
        tool_name="node_action",
        payload={
            "node_id": str(node_world.node.pk),
            "capability": "repo.command.run",
            "args": REPO_ARGS,
        },
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )
    client = principal(kind, node_world, web_client)
    response = client.post(f"/api/v1/approvals/requests/{approval.pk}/approve", format="json")
    approval.refresh_from_db()
    if kind == "owner-web":
        assert response.status_code == 200, response.content
        assert approval.status == "approved"
        assert NodeActionExecution.objects.filter(approval=approval).exists()
        return
    assert response.status_code in (401, 403, 404)
    assert approval.status == "pending"
    assert not NodeActionExecution.objects.exists()
