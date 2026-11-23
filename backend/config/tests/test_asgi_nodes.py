"""The node gateway's composition root (Phase 10 brief D7, D12, D14):
`config.asgi_nodes` registers the 8A output scrubber, and it is the only
module that registers node scrubbers; the beat schedules the node action
backstops; the boot checks refuse an over-long grant TTL."""

import asyncio
import json
from pathlib import Path

import pytest
from django.core.checks import run_checks

from config import asgi_nodes
from config.celery import app
from nodes import events, output

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_scrubber():
    yield
    output.reset_scrubber()
    events.reset_sinks()


@pytest.mark.django_db
def test_the_gateway_registers_the_8a_scrubber_with_workspace_terms():
    from accounts.services import register_user
    from memory.models import ScrubTerm
    from workspaces.models import Workspace

    output.reset_scrubber()
    asgi_nodes.register_node_ports()
    assert output.scrubber_registered()
    user = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="O")
    workspace = Workspace.objects.get(owner=user)
    ScrubTerm.objects.create(workspace=workspace, term="ProjectNightjar", created_by=user)
    scrub = output.scrubber_for(workspace)
    text = scrub("codename ProjectNightjar token ghp_" + "a" * 36)
    assert "ProjectNightjar" not in text and "ghp_" + "a" * 36 not in text
    assert output.scrub_tail("ProjectNightjar ok", workspace) != "ProjectNightjar ok"


def test_only_config_asgi_nodes_registers_node_scrubbers():
    """D14: a source test, not import-linter (a function call, not an
    import)."""
    callers = []
    for path in BACKEND.rglob("*.py"):
        if {".venv", "tests", "migrations"} & set(path.parts):
            continue
        if "register_scrubber(" in path.read_text("utf-8"):
            callers.append(path.relative_to(BACKEND).as_posix())
    assert sorted(callers) == ["config/asgi_nodes.py", "nodes/output.py"]


def test_the_gateway_registers_the_memory_hook_sink():
    """D10: the composition root hands `nodes.events` 8A's channel-agnostic
    ingest function; `nodes` itself imports no `memory`."""
    from memory.ingestion.hooks import ingest_hook_events

    events.reset_sinks()
    asgi_nodes.register_node_ports()
    assert events._sinks == {events.HOOK_EVENTS: ingest_hook_events}


def test_only_config_asgi_nodes_registers_node_event_sinks():
    """D10: a source test (a call, not an import)."""
    callers = []
    for path in BACKEND.rglob("*.py"):
        if {".venv", "tests", "migrations"} & set(path.parts):
            continue
        if "register_sink(" in path.read_text("utf-8"):
            callers.append(path.relative_to(BACKEND).as_posix())
    assert sorted(callers) == ["config/asgi_nodes.py", "nodes/events.py"]


def _call(scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(asgi_nodes.application(scope, receive, send))
    return sent


@pytest.mark.django_db(transaction=True)
def test_healthz_answers_without_key_material():
    sent = _call({"type": "http", "method": "GET", "path": "/healthz"})
    assert sent[0]["status"] == 200
    body = json.loads(sent[1]["body"])
    assert body["status"] == "ok" and body["redis"] is True
    assert set(body) == {"status", "redis", "connected_nodes", "signing_key_id"}
    assert len(body["signing_key_id"]) == 16
    assert body["connected_nodes"] == 0
    from django.conf import settings

    # The key id only: no key material in the body.
    assert settings.NODE_SIGNING_KEY not in sent[1]["body"].decode()


@pytest.mark.django_db(transaction=True)
def test_healthz_reports_redis_down(settings):
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    body = json.loads(_call({"type": "http", "method": "GET", "path": "/healthz"})[1]["body"])
    assert body["redis"] is False


def test_the_gateway_route_is_the_node_socket_app():
    from nodes.gateway import NodeSocketApp

    assert isinstance(asgi_nodes.node_app, NodeSocketApp)
    assert asgi_nodes.GATEWAY_WS_PATH == "/ws/node/v1"


def test_a_websocket_on_another_path_is_closed():
    sent = _call({"type": "websocket", "path": "/ws/other"})
    assert sent == [{"type": "websocket.close", "code": 4404}]


def test_other_paths_are_404():
    assert _call({"type": "http", "method": "GET", "path": "/"})[0]["status"] == 404


def test_the_node_action_backstops_are_on_the_beat():
    schedule = app.conf.beat_schedule
    assert schedule["approval-request-expiry-sweep"]["schedule"] == 60.0
    assert schedule["node-action-sweep"]["task"] == "nodes.tasks.sweep_node_actions_task"
    assert schedule["node-action-sweep"]["schedule"] == 60.0
    assert schedule["node-action-tail-retention"]["task"] == (
        "nodes.tasks.clear_node_action_tails_task"
    )


@pytest.mark.parametrize(
    ("name", "value", "check_id"),
    [
        ("NODE_GRANT_TTL_SECONDS", 121, "nodes.E001"),
        ("NODE_GRANT_TTL_SECONDS", 0, "nodes.E001"),
        ("NODE_GRANT_TTL_SECONDS", "90", "nodes.E001"),
        ("NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS", 0, "nodes.E002"),
        ("NODE_ACTION_TAIL_RETENTION_DAYS", 0, "nodes.E003"),
        ("NODE_INVOKE_TIMEOUT_SECONDS", 30, "nodes.E005"),
        ("NODE_INVOKE_TIMEOUT_SECONDS", 0, "nodes.E005"),
        ("NODE_MAX_INFLIGHT", 0, "nodes.E006"),
        ("NODE_RESULT_TTL_SECONDS", 0, "nodes.E007"),
    ],
)
def test_the_boot_checks_refuse_bad_node_action_settings(settings, name, value, check_id):
    setattr(settings, name, value)
    assert check_id in {message.id for message in run_checks()}


def test_the_defaults_pass_the_boot_checks(settings):
    assert settings.NODE_GRANT_TTL_SECONDS == 90
    assert settings.NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS == 60
    assert settings.NODE_ACTION_TAIL_RETENTION_DAYS == 14
    assert (settings.NODE_INVOKE_TIMEOUT_SECONDS, settings.NODE_MAX_INFLIGHT) == (10, 4)
    assert settings.NODE_RESULT_TTL_SECONDS == 30
    ids = {"nodes.E001", "nodes.E002", "nodes.E003", "nodes.E005", "nodes.E006", "nodes.E007"}
    assert not {m.id for m in run_checks()} & ids
