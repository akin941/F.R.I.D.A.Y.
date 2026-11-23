"""`invoke_read` / `refuse_read` without a socket (Phase 9 brief D6, D7;
branch 4): every not-sent outcome writes exactly one chain row and one
`nodes.invoke` AuditLog row, signs nothing and pushes nothing; the shared
in-flight cap holds across processes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from audit.models import AuditLog
from nodes import audit_chain, bus, services
from nodes.models import NodeInvocation
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.node import default_catalogue

pytestmark = pytest.mark.django_db

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def node(make_node, cleanup_nodes):
    result = make_node()
    cleanup_nodes.append(str(result.node.pk))
    catalogue = [e for e in default_catalogue() if e["class"] == "read"]
    services.record_capability_snapshot(
        node=result.node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=1,
    )
    return result.node


def read(node, capability="apps.frontmost", args=None, **kwargs):
    return services.invoke_read(
        node=node,
        capability=capability,
        args={} if args is None else args,
        workspace=node.workspace,
        actor=node.owner,
        **kwargs,
    )


def inbox_len(node):
    return bus._run(lambda c: c.llen(bus.inbox_key(node.pk)))


def assert_one_not_sent_row(outcome, status, code=""):
    assert (outcome.status, outcome.refusal_code) == (status, code)
    row = NodeInvocation.objects.get()
    assert (row.status, row.refusal_code, row.nonce, row.output_sha256, row.output_bytes) == (
        status,
        code,
        "",
        "",
        0,
    )
    audit = AuditLog.objects.get(action="nodes.invoke")
    assert audit.metadata["status"] == status and audit.metadata["nonce"] == ""
    assert outcome.invocation is not None and outcome.invocation.pk == row.pk


def test_no_presence_is_node_offline_at_once_with_nothing_pushed(node):
    outcome = read(node)
    assert_one_not_sent_row(outcome, "node_offline")
    assert inbox_len(node) == 0
    assert bus.inflight_count(node.pk) == 0


def test_an_unknown_or_non_read_capability_is_not_invocable(node):
    bus.mark_present(node.pk)
    outcome = read(node, capability="repo.command.run", args={"repo_path": "~/x"})
    assert_one_not_sent_row(outcome, "server_refused", "not_invocable")
    assert inbox_len(node) == 0


def test_a_capability_missing_from_the_snapshot_is_not_invocable(make_node, cleanup_nodes):
    result = make_node("Bare")
    cleanup_nodes.append(str(result.node.pk))
    bus.mark_present(result.node.pk)
    outcome = read(result.node)
    assert_one_not_sent_row(outcome, "server_refused", "not_invocable")


def test_args_outside_the_server_contract_are_refused(node):
    bus.mark_present(node.pk)
    outcome = read(node, capability="git.status", args={"repo_path": "~/x", "extra": 1})
    assert_one_not_sent_row(outcome, "server_refused", "args_contract")
    assert inbox_len(node) == 0


def test_a_missing_signing_key_is_signing_unavailable(node, settings):
    bus.mark_present(node.pk)
    settings.NODE_SIGNING_KEY = ""
    outcome = read(node)
    assert_one_not_sent_row(outcome, "server_refused", "signing_unavailable")
    assert inbox_len(node) == 0
    assert bus.inflight_count(node.pk) == 0


def test_redis_down_is_bus_unavailable(node, settings):
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    outcome = read(node)
    assert_one_not_sent_row(outcome, "bus_unavailable")


def test_the_inflight_cap_is_busy(node, settings):
    settings.NODE_MAX_INFLIGHT = 2
    bus.mark_present(node.pk)
    assert bus.acquire_inflight(node.pk, 2, 60) and bus.acquire_inflight(node.pk, 2, 60)
    outcome = read(node)
    assert_one_not_sent_row(outcome, "busy")
    assert inbox_len(node) == 0
    assert bus.inflight_count(node.pk) == 2  # the busy call took no slot


def test_a_non_web_channel_is_refused_until_branch_5(node):
    bus.mark_present(node.pk)
    outcome = read(node, channel="system")
    assert_one_not_sent_row(outcome, "server_refused", "not_invocable")
    assert NodeInvocation.objects.get().channel == "system"


def test_refuse_read_writes_the_two_rows_and_sends_nothing(node):
    bus.mark_present(node.pk)
    outcome = services.refuse_read(
        node=node,
        capability="git.status",
        args={"repo_path": "~/work/secret"},
        workspace=node.workspace,
        actor=node.owner,
        trace_id="trace-1",
        code="path_not_in_workspace",
    )
    assert_one_not_sent_row(outcome, "server_refused", "path_not_in_workspace")
    row = NodeInvocation.objects.get()
    assert row.trace_id == "trace-1" and row.capability_version == 1
    assert inbox_len(node) == 0
    # Args are hashed, never stored.
    assert "secret" not in json.dumps(AuditLog.objects.get(action="nodes.invoke").metadata)


def test_a_timeout_when_nobody_answers(node, settings):
    """Presence without a socket: the envelope is pushed (it was sent), no
    answer comes, the read ends `timeout` with its nonce recorded."""
    settings.NODE_INVOKE_TIMEOUT_SECONDS = 1
    bus.mark_present(node.pk)
    outcome = read(node)
    assert outcome.status == "timeout"
    row = NodeInvocation.objects.get()
    assert row.status == "timeout" and row.nonce
    # The inbox list expires with the envelope (TTL <= the read TTL).
    assert 0 < bus._run(lambda c: c.ttl(bus.inbox_key(node.pk))) <= 30
    envelope = json.loads(bus._run(lambda c: c.lpop(bus.inbox_key(node.pk))))
    assert envelope["nonce"] == row.nonce and envelope["v"] == 1
    assert bus.inflight_count(node.pk) == 0


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"verdict": "node_disconnected"}, "node_disconnected", ""),
        ({"verdict": "node_mismatch"}, "server_refused", "node_mismatch"),
        ({"verdict": "result_schema"}, "error", "result_schema"),
        ({"status": "refused", "refusal_code": "replay"}, "refused", "replay"),
        ({"status": "refused", "refusal_code": "made_up"}, "error", "node_error"),
        ({"status": "error", "refusal_code": None}, "error", "node_error"),
        ({"status": "timeout", "refusal_code": None}, "timeout", ""),
        ({"status": "ok", "output": {"bad": 1}}, "error", "result_schema"),
    ],
)
def test_every_answer_maps_into_the_d7_vocabulary(node, monkeypatch, payload, status, code):
    bus.mark_present(node.pk)
    monkeypatch.setattr(bus, "wait_result", lambda invocation_id, timeout: payload)
    outcome = read(node)
    assert (outcome.status, outcome.refusal_code, outcome.output) == (status, code, None)
    row = NodeInvocation.objects.get()
    assert (row.status, row.refusal_code) == (status, code)
    assert bool(row.nonce) == (status != "server_refused")
    assert audit_chain.verify_chain().ok


def test_the_inflight_cap_holds_across_two_processes(node):
    """Brief follow-up 3: the counter lives in Redis, so a caller in another
    process sees the slots this one took."""
    assert bus.acquire_inflight(node.pk, 3, 60)
    assert bus.acquire_inflight(node.pk, 3, 60)
    code = (
        "import django, sys; django.setup();"
        "from nodes import bus;"
        f"print([bus.acquire_inflight('{node.pk}', 3, 60) for _ in range(2)])"
    )
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="config.settings.test")
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 0, child.stderr[-2000:]
    assert child.stdout.strip().splitlines()[-1] == "[True, False]"
    assert bus.inflight_count(node.pk) == 3
    bus.release_inflight(node.pk)
    assert bus.inflight_count(node.pk) == 2
    for _ in range(5):
        bus.release_inflight(node.pk)
    assert bus.inflight_count(node.pk) == 0  # never below zero


# --- branch 5: the step-0 actor check and the row filter ---


def test_a_personal_read_by_someone_else_is_actor_not_owner(node):
    """Phase 8B D13 amendment, step 0: before anything is signed."""
    from nodes.tests.gateway_helpers import other_user

    bus.mark_present(node.pk)
    stranger = other_user()
    for actor in (stranger, None):
        outcome = services.invoke_read(
            node=node, capability="apps.frontmost", args={}, workspace=node.workspace, actor=actor
        )
        assert (outcome.status, outcome.refusal_code) == ("server_refused", "actor_not_owner")
    node.owner.is_active = False
    node.owner.save(update_fields=["is_active"])
    outcome = read(node)
    assert (outcome.status, outcome.refusal_code) == ("server_refused", "actor_not_owner")
    assert inbox_len(node) == 0
    assert NodeInvocation.objects.filter(nonce="").count() == 3
    assert AuditLog.objects.filter(action="nodes.invoke").count() == 3


def test_the_actor_check_needs_an_owner_role_in_the_target_workspace(node, firm):
    from workspaces.models import WorkspaceMember

    bus.mark_present(node.pk)
    WorkspaceMember.objects.filter(workspace=firm, user=node.owner).update(
        role=WorkspaceMember.Role.MEMBER
    )
    outcome = services.invoke_read(
        node=node,
        capability="claude.sessions.read",
        args={"session_id": "s", "last_turns": 1},
        workspace=firm,
        actor=node.owner,
    )
    assert outcome.refusal_code == "actor_not_owner"
    # A firm-class capability has no step 0: it goes on (to `node_offline`
    # here).
    bus.clear_presence(node.pk)
    outcome = services.invoke_read(
        node=node,
        capability="git.status",
        args={"repo_path": "~/work/acme"},
        workspace=firm,
        actor=node.owner,
        row_filter=None,
    )
    assert outcome.status == "node_offline"


def test_the_row_filter_result_and_dropped_count_are_recorded(node, monkeypatch):
    bus.mark_present(node.pk)
    output = {
        "windows": [{"bundle_id": None, "app_name": "A", "window_count": 1, "on_screen": True}]
    }
    monkeypatch.setattr(bus, "wait_result", lambda *_: {"status": "ok", "output": output})
    outcome = read(
        node,
        capability="windows.list",
        args={"limit": 5},
        row_filter=lambda out: ({"windows": []}, 7),
    )
    assert outcome.ok and outcome.output == {"windows": []} and outcome.dropped == 7
    row = NodeInvocation.objects.get()
    assert row.dropped_unmapped == 7
    # The hashes are the node's own answer's.
    from nodes.protocol.canonical import sha256_hex

    assert row.output_sha256 == sha256_hex(output)
    assert AuditLog.objects.get(action="nodes.invoke").metadata["dropped_unmapped"] == 7
    assert audit_chain.verify_chain().ok


def test_a_row_filter_that_raises_ends_the_read_without_output(node, monkeypatch):
    bus.mark_present(node.pk)
    monkeypatch.setattr(
        bus,
        "wait_result",
        lambda *_: {"status": "ok", "output": {"bundle_id": None, "app_name": None}},
    )

    def broken(_):
        raise KeyError("x")

    outcome = read(node, row_filter=broken)
    assert (outcome.status, outcome.refusal_code, outcome.output) == (
        "error",
        "result_schema",
        None,
    )
    assert NodeInvocation.objects.get().status == "error"
