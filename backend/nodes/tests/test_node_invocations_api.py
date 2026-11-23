"""`GET /api/v1/nodes/<id>/invocations` (Phase 9 brief D11, branch 5): the
node's chain rows in workspaces the actor can see, newest first, 50 per
page with a `seq` cursor; hashes, codes and sizes -- never args or output.
Role x endpoint over the five Phase 4 principals plus a `hook` PAT and a
node secret; another user's node is a 404; every view is audited
`nodes.invocations.viewed`."""

import json

import pytest

from audit.models import AuditLog
from nodes import services
from nodes.models import NodeInvocation
from nodes.tests.conftest import PRINCIPALS, principal_client
from nodes.tests.gateway_helpers import direct_node, other_user, personal_of

pytestmark = pytest.mark.django_db

SECRET_ARG = "~/work/secret-project"


def url(node):
    return f"/api/v1/nodes/{node.pk}/invocations"


@pytest.fixture
def node(make_node, cleanup_nodes):
    result = make_node()
    cleanup_nodes.append(str(result.node.pk))
    return result.node


def refuse(node, workspace, n=1, code="path_not_in_workspace"):
    for i in range(n):
        services.refuse_read(
            node=node,
            capability="git.status",
            args={"repo_path": f"{SECRET_ARG}/{i}"},
            workspace=workspace,
            actor=node.owner,
            trace_id=f"trace-{i}",
            code=code,
        )


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_role_by_endpoint(principal, owner, personal, node, web_client):
    refuse(node, personal)
    client = principal_client(principal, owner, personal, web_client)
    response = client.get(url(node))
    if principal == "owner-web":
        assert response.status_code == 200
        assert len(response.json()["results"]) == 1
        return
    if principal == "anonymous":
        assert response.status_code == 401
    else:
        assert response.status_code in (401, 403), response.status_code
    assert "trace-0" not in response.content.decode()
    assert not AuditLog.objects.filter(action="nodes.invocations.viewed").exists()


def test_rows_carry_hashes_codes_and_the_workspace_name_never_content(
    owner, personal, firm, node, web_client
):
    refuse(node, personal)
    refuse(node, firm, code="workspace_not_allowed")
    response = web_client(owner).get(url(node))
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    newest, oldest = body["results"]
    assert newest["seq"] > oldest["seq"]
    assert newest["workspace_name"] == "Acme" and oldest["workspace_name"] == personal.name
    assert (newest["status"], newest["refusal_code"]) == ("server_refused", "workspace_not_allowed")
    assert newest["capability"] == "git.status" and newest["class"] == "read"
    assert len(newest["args_sha256"]) == 64 and newest["output_sha256"] == ""
    assert newest["dropped_unmapped"] == 0 and newest["channel"] == "web"
    assert "secret-project" not in json.dumps(body)
    audit = AuditLog.objects.get(action="nodes.invocations.viewed")
    assert audit.metadata == {"node_id": str(node.pk), "count": 2, "cursor": None}


def test_pages_of_fifty_with_a_seq_cursor(owner, personal, node, web_client):
    refuse(node, personal, n=53)
    client = web_client(owner)
    first = client.get(url(node)).json()
    assert len(first["results"]) == 50
    seqs = [row["seq"] for row in first["results"]]
    assert seqs == sorted(seqs, reverse=True)
    second = client.get(url(node), {"cursor": first["next_cursor"]}).json()
    assert len(second["results"]) == 3 and second["next_cursor"] is None
    assert max(row["seq"] for row in second["results"]) < min(seqs)
    assert AuditLog.objects.filter(action="nodes.invocations.viewed").count() == 2


@pytest.mark.parametrize("cursor", ["abc", "-1", "1.5"])
def test_an_invalid_cursor_is_400(owner, node, web_client, cursor):
    response = web_client(owner).get(url(node), {"cursor": cursor})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"


def test_rows_of_a_workspace_the_actor_cannot_see_are_left_out(owner, personal, node, web_client):
    stranger = other_user()
    refuse(node, personal)
    refuse(node, personal_of(stranger))
    assert NodeInvocation.objects.count() == 2
    rows = web_client(owner).get(url(node)).json()["results"]
    assert [row["workspace_id"] for row in rows] == [str(personal.pk)]


def test_another_users_node_is_a_404_and_unknown_too(owner, node, web_client):
    stranger = other_user()
    theirs, _ = direct_node(stranger)
    refuse(theirs, personal_of(stranger))
    client = web_client(owner)
    assert client.get(url(theirs)).status_code == 404
    response = client.get("/api/v1/nodes/00000000-0000-4000-8000-000000000000/invocations")
    assert response.status_code == 404
    assert not AuditLog.objects.filter(action="nodes.invocations.viewed").exists()
