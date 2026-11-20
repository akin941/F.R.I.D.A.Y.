"""`GET /api/v1/connectors/reads` (Phase 5 branch 9): the Settings page's
recent reads, bookkeeping only from the `connector.read` audit rows --
role x endpoint against the five Phase 4 principals, workspace isolation,
and no argument hash or content in the response."""

import pytest

from accounts.services import register_user
from audit.services import record_audit
from connectors.services import ConnectorPermissionDenied, list_recent_reads
from connectors.tests.test_bindings_api import PRINCIPALS, _client
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

URL = "/api/v1/connectors/reads"
ARGS_HASH = "f" * 64


def _read_row(workspace, owner, **metadata):
    record_audit(
        action="connector.read",
        actor=owner,
        workspace=workspace,
        metadata={
            "connector": "github",
            "tool": "github_pr_status",
            "binding": "b1",
            "channel": "web",
            "args_sha256": ARGS_HASH,
            "size": 120,
            "truncated": False,
            "outcome": "ok",
            "duration_ms": 35,
            **metadata,
        },
    )


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_reads_role_matrix(owner, workspace, web_client, principal, expected):
    _read_row(workspace, owner)
    client = _client(principal, owner, workspace, web_client)
    response = client.get(URL, {"workspace": str(workspace.id)})
    assert response.status_code == expected
    if expected == 200:
        assert len(response.json()) == 1


def test_bookkeeping_only_newest_first(owner, workspace, web_client):
    _read_row(workspace, owner, tool="first")
    _read_row(
        workspace, owner, tool="second", outcome="timeout", truncated=True, data_class="personal"
    )
    record_audit(action="connector.binding.enabled", actor=owner, workspace=workspace, metadata={})
    rows = web_client(owner).get(URL, {"workspace": str(workspace.id)}).json()
    assert [r["tool"] for r in rows] == ["second", "first"]
    assert rows[0]["outcome"] == "timeout"
    assert rows[0]["truncated"] is True
    assert rows[0]["data_class"] == "personal"
    assert rows[1]["data_class"] == ""
    assert rows[1]["size"] == 120 and rows[1]["duration_ms"] == 35
    assert set(rows[0]) == {
        "id",
        "created_at",
        "connector",
        "tool",
        "outcome",
        "channel",
        "data_class",
        "size",
        "duration_ms",
        "truncated",
    }
    assert ARGS_HASH not in str(rows)


def test_connector_filter(owner, workspace, web_client):
    _read_row(workspace, owner)
    _read_row(workspace, owner, connector="google", tool="mail_search")
    client = web_client(owner)
    rows = client.get(URL, {"workspace": str(workspace.id), "connector": "google"}).json()
    assert [r["tool"] for r in rows] == ["mail_search"]
    bad = client.get(URL, {"workspace": str(workspace.id), "connector": "../x"})
    assert bad.status_code == 400


def test_workspace_isolation(owner, workspace, firm, web_client):
    _read_row(firm, owner, tool="firm_only")
    assert web_client(owner).get(URL, {"workspace": str(workspace.id)}).json() == []
    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    foreign = Workspace.objects.get(owner=stranger)
    _read_row(foreign, stranger)
    assert web_client(owner).get(URL, {"workspace": str(foreign.id)}).status_code == 404


def test_non_curator_refused_in_service(owner, workspace):
    member = register_user(email="m@test.dev", password="Str0ngDevPass!42")
    with pytest.raises(ConnectorPermissionDenied):
        list_recent_reads(workspace=workspace, actor=member)
