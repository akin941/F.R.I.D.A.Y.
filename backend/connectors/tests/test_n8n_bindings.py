"""Role x endpoint for the `n8n` connector binding (Phase 12 branch 4): the
existing `/api/v1/connectors/bindings` surface, owner web session only --
PATs (cli, openai), a `node_voice` token, a node secret, a member, an agent
and an anonymous caller cannot list or switch it; a firm workspace cannot
bind it at all."""

import pytest
from rest_framework.test import APIClient

from accounts.tests.node_voice_helpers import bearer, mint_token, pair_node
from connectors.models import ConnectorBinding
from connectors.tests.test_bindings_api import PRINCIPALS, _client

pytestmark = pytest.mark.django_db

LIST = "/api/v1/connectors/bindings"


def _principal_client(principal, owner, workspace, web_client):
    if principal == "node-secret":
        _, secret = pair_node(owner)
        return bearer(secret)
    if principal == "node-voice-token":
        _, secret = pair_node(owner)
        return bearer(mint_token(secret))
    return _client(principal, owner, workspace, web_client)


ALL = [*PRINCIPALS, ("node-secret", 401), ("node-voice-token", 401)]


@pytest.mark.parametrize("principal, expected", ALL)
def test_the_n8n_binding_is_owner_web_only(
    n8n, owner, workspace, web_client, committed, principal, expected
):
    client = _principal_client(principal, owner, workspace, web_client)
    listed = client.get(LIST, {"workspace": str(workspace.id)})
    assert listed.status_code in ((200,) if expected == 200 else (401, 403))
    with committed():
        toggled = client.put(
            f"{LIST}/n8n", {"workspace": str(workspace.id), "enabled": True}, format="json"
        )
    assert toggled.status_code in ((200,) if expected == 200 else (401, 403))
    enabled = ConnectorBinding.objects.filter(connector="n8n", enabled=True).exists()
    assert enabled == (expected == 200)
    if expected == 200:
        names = [row["connector"] for row in listed.json()]
        assert "n8n" in names
        binding = ConnectorBinding.objects.get(connector="n8n")
        assert binding.tool_status("n8n_server_status") == "ok"
        assert binding.tool_status("n8n_home_backup") == "ok"


def test_a_firm_workspace_never_lists_or_binds_n8n(n8n, owner, firm, web_client, committed):
    client = web_client(owner)
    listed = client.get(LIST, {"workspace": str(firm.id)})
    assert listed.status_code == 200
    assert "n8n" not in [row["connector"] for row in listed.json()]
    with committed():
        response = client.put(
            f"{LIST}/n8n", {"workspace": str(firm.id), "enabled": True}, format="json"
        )
    assert response.status_code == 400
    assert not ConnectorBinding.objects.filter(connector="n8n").exists()


def test_anonymous_is_401(n8n, workspace):
    assert APIClient().get(LIST, {"workspace": str(workspace.id)}).status_code == 401
