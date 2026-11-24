import pytest

from accounts.services import register_user
from connectors import mcp_client
from connectors.manifest import register_manifest, unregister_manifest
from connectors.models import ConnectorBinding
from connectors.reads import register_reads, unregister_reads
from connectors.services import set_binding_enabled
from connectors.tests import fakes
from connectors.writes import register_writes, unregister_writes
from core.channels import CHANNEL_WEB
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"
# A credential value that must never show up anywhere Friday writes.
CREDENTIAL_CANARY = "ghp_CREDENTIAL-CANARY-0123456789"


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def firm(owner):
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def fake_state():
    return fakes.FakeServerState()


@pytest.fixture
def server(monkeypatch, fake_state):
    """The fake server wired behind the production MCP client."""
    monkeypatch.setenv(fakes.URL_ENV, fakes.SERVER_URL)
    monkeypatch.setenv(fakes.PERSONAL_URL_ENV, fakes.SERVER_URL)
    monkeypatch.setenv("FAKE_MCP_TOKEN", CREDENTIAL_CANARY)
    monkeypatch.setattr(mcp_client, "_connect_http", fakes.asgi_connector(fake_state))
    return fake_state


@pytest.fixture
def n8n_state():
    return fakes.N8nServerState()


@pytest.fixture
def n8n(monkeypatch, n8n_state):
    """The fake n8n trigger behind the in-process relay, wired to the
    production MCP client (Phase 12 branch 4). The `n8n` manifest itself is
    the committed one (registered at boot)."""
    return fakes.install_n8n(monkeypatch, n8n_state)


@pytest.fixture
def n8n_binding(n8n, workspace, enable):
    from connectors.manifest import get_manifest

    return enable(get_manifest("n8n"), workspace)


def _install(manifest):
    register_manifest(manifest)
    register_reads(manifest)
    register_writes(manifest)


def _uninstall(manifest):
    unregister_reads(manifest)
    unregister_writes(manifest)
    unregister_manifest(manifest.name)


@pytest.fixture
def fake_manifest(server):
    manifest = fakes.make_manifest()
    _install(manifest)
    yield manifest
    _uninstall(manifest)


@pytest.fixture
def personal_manifest(server):
    manifest = fakes.make_manifest(
        name="mailish", data_class="personal", url_env=fakes.PERSONAL_URL_ENV
    )
    _install(manifest)
    yield manifest
    _uninstall(manifest)


@pytest.fixture
def committed(django_capture_on_commit_callbacks):
    """`with committed(): ...` -- run the block's `on_commit` callbacks (the
    pin check after enable, a write's dispatch after approval) as a real
    commit would, eager Celery included."""

    def _committed():
        return django_capture_on_commit_callbacks(execute=True)

    return _committed


@pytest.fixture
def enable(owner, committed):
    """Enable a manifest in a workspace through the real service (its pin
    check runs eagerly against the fake server)."""

    def _enable(manifest, workspace):
        with committed():
            set_binding_enabled(
                workspace=workspace,
                actor=owner,
                connector=manifest.name,
                enabled=True,
                channel=CHANNEL_WEB,
            )
        return ConnectorBinding.objects.get(workspace=workspace, connector=manifest.name)

    return _enable
