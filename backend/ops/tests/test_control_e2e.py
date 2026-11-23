"""Phase 10 closeout end to end (brief §8, "E2E (CI, fakes)"): the Python
reference node v2, standing in for the deferred Swift app, goes through the
whole control path against the real `NodeSocketApp` (in process,
`gateway_harness`, real Postgres and Redis).

1. A typed "acme'de testleri çalıştır" in a firm conversation -> the router
   proposes a `node_action` draft -> the card click creates the approval
   (the real trust check answers `ok`) -> the owner approves on the web ->
   one grant -> the invoke reaches the node over the socket -> the node's
   window approves -> the fake executor's output streams into the HUD
   terminal stream -> an `ok` execution, one chain row (v2) and one
   `nodes.invoke` audit row.
2. The same turn as voice -> a draft only: no approval, no execution, no
   frame.
3. A confirm-class `apps.open` card click -> the node's window -> `ok`, no
   `ApprovalRequest`.
4. The owner's kill switch while a confirmation window is open -> the
   socket closes, the window is force-closed, the execution ends
   `node_disconnected` and nothing ran.

It lives in `ops` beside the other closeout e2e tests because it spans
`accounts`, `approvals`, `assistant`, `tools`, `nodes` and `hud`. Nothing
leaves the process but the test Postgres and Redis: no Mac, no `srt`, no
model (FakeAdapter, scripted per purpose)."""

import json
import time

import pytest
from django.apps import apps
from django.db import connections
from rest_framework.test import APIClient

from ai_core.adapters.fake import FakeAdapter
from ai_core.management.commands import seed_ai_defaults
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from approvals.models import ApprovalRequest
from assistant.models import Conversation
from audit.models import AuditLog
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, WorkspaceMappingRule
from nodes import bus, output
from nodes.audit_chain import verify_chain
from nodes.gateway import NodeSocketApp
from nodes.models import NodeActionExecution, NodeInvocation
from nodes.protocol.signing import load_public_key_b64
from nodes.reference.confirm import ConfirmationWindowDouble, Decision
from nodes.reference.executor import FakeExecutor
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.tests.gateway_harness import V1, V2, Harness, WallClock
from tools import node_action_ports
from tools.tests.conftest import script, server_scrubber
from tools.tests.node_reads_helpers import _firm, _user
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db(transaction=True)

CHAT_URL = "/api/v1/ai/conversations/{}/messages"
APPROVALS_URL = "/api/v1/approvals/requests"
TYPED = "acme'de testleri çalıştır"
ACME = {"repo_path": "~/work/acme", "command_kind": "test"}
TERMINAL = {"bundle_id": "com.apple.Terminal"}
NODE_INTENT = json.dumps({"intent": "tool_request", "tool_name": "node_action"})
CONSISTENT = '{"verdict": "consistent", "reason_code": "matches_request"}'
TERMINAL_STATES = {
    "ok",
    "failed",
    "denied",
    "timeout",
    "node_disconnected",
    "node_refused",
    "server_refused",
    "node_offline",
    "stale_dispatch",
    "expired",
}


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("control-e2e-home"))


@pytest.fixture
def harness():
    app = NodeSocketApp(tick_seconds=0.05)
    harness = Harness(app)
    yield harness
    harness.close()
    connections.close_all()


@pytest.fixture
def world():
    """The owner with a personal and a firm workspace and the 8A path rules
    (`~/work` -> firm, `~/code` -> personal); the server output scrubber as
    `config.asgi_nodes` registers it; the real trust check and evidence
    source as `AssistantConfig.ready()` registers them at startup."""
    ports = node_action_ports.snapshot_ports()
    owner = _user("owner@test.dev", is_owner=True)
    personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    firm = _firm(owner, "Acme")
    ensure_default_sources(workspace=personal, created_by=owner)
    hooks = IngestionSource.objects.get(
        workspace=personal, kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS
    )
    for pattern, target in (("~/work", firm), ("~/code", personal)):
        WorkspaceMappingRule.objects.create(
            workspace=personal,
            source=hooks,
            rule_type=WorkspaceMappingRule.RuleType.PATH_PREFIX,
            pattern=pattern,
            target_workspace=target,
            created_by=owner,
        )
    output.register_scrubber(server_scrubber)
    apps.get_app_config("assistant").ready()
    state = {"owner": owner, "personal": personal, "firm": firm, "node_ids": []}
    yield state
    for node_id in state["node_ids"]:
        for clear in (bus.clear_presence, bus.clear_inbox, bus.clear_inflight):
            clear(node_id)
    output.reset_scrubber()
    node_action_ports.restore_ports(ports)


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])


@pytest.fixture
def friday(settings, monkeypatch):
    """A chat stack on FakeAdapter with the seeded Phase 10 prompts; every
    model answer is scripted per purpose and every call recorded."""
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", seed_ai_defaults.FRIDAY_ROUTER_INTENT_V1)
    _prompt(
        "friday_router_node_action_args",
        "router.node_action_args",
        seed_ai_defaults.FRIDAY_ROUTER_NODE_ACTION_ARGS_V1,
    )
    _prompt(
        "friday_control_trust_check",
        "control.trust_check",
        seed_ai_defaults.FRIDAY_CONTROL_TRUST_CHECK_V1,
    )
    state = {
        "router.intent": NODE_INTENT,
        "router.node_action_args": None,
        "control.trust_check": CONSISTENT,
        "chat": "Tamam.",
        "calls": [],
    }
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        state["calls"].append(purpose)
        answer = state.get(purpose)
        return original(self, purpose, messages) if answer is None else answer

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)
    return state


def _pair_through_the_endpoint(owner_client):
    issued = owner_client.post(
        "/api/v1/auth/pairing-codes", {"kind": "strong", "device_kind": "node"}, format="json"
    )
    assert issued.status_code == 201, issued.content
    paired = APIClient().post(
        "/api/v1/nodes/pair",
        {"code": issued.json()["code"], "node_name": "MacBook", "protocol_versions": [1]},
        format="json",
    )
    assert paired.status_code == 201, paired.content
    return paired.json()


def _reference_node(body, home, decisions, scripts):
    """The reference node v2 as the Swift app will be: it pins exactly the
    keys the pairing response returned, maps `~/work/acme` locally, renders
    its own window (scripted owner) and runs the fake executor."""
    pinned = {
        entry["key_id"]: load_public_key_b64(entry["public_key_b64"])
        for entry in body["server_signing_keys"]
    }
    clock = WallClock()
    return ReferenceNode(
        node_id=body["node_id"],
        pinned=pinned,
        policy=fixture_policy(home),
        executor=FakeExecutor(clock, scripts=list(scripts)),
        clock=clock,
        window=ConfirmationWindowDouble(clock, [Decision(a, after) for a, after in decisions]),
        catalogue=default_catalogue(),
    )


def _turn(client, conversation, content, **body):
    response = client.post(
        CHAT_URL.format(conversation.pk), {"content": content, **body}, format="json"
    )
    assert response.status_code == 200, response.content
    events = []
    for chunk in b"".join(response.streaming_content).split(b"\n\n"):
        if chunk.startswith(b"data: "):
            events.append(json.loads(chunk[len(b"data: ") :]))
    return [event for event in events if event["type"] == "draft"]


def _click_card(client, workspace, draft):
    response = client.post(
        APPROVALS_URL,
        {
            "workspace": str(workspace.pk),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return ApprovalRequest.objects.get(pk=response.json()["id"])


def _pump_until(link, predicate, timeout=10.0):
    """Tick the node (its window and executor run on its own ticks) until
    `predicate()` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        if link.node.connected:
            link.node_call(lambda node: node.tick())
        time.sleep(0.02)
    return predicate()


def _execution_status(pk):
    return NodeActionExecution.objects.filter(pk=pk).values_list("status", flat=True).first()


def _sse_frames(response):
    body = b"".join(response.streaming_content).decode()
    frames = []
    for raw in body.split("\n\n"):
        data = [line for line in raw.split("\n") if line.startswith("data: ")]
        if data:
            frames.append(json.loads(data[0][len("data: ") :]))
    return frames


def test_typed_voice_confirm_and_kill_switch(world, home, harness, friday, web_client):
    owner, firm = world["owner"], world["firm"]
    client = web_client(owner)

    # Pair through the endpoint and connect on protocol v2.
    body = _pair_through_the_endpoint(client)
    world["node_ids"].append(body["node_id"])
    reference = _reference_node(
        body,
        home,
        decisions=(("approve", 0), ("approve", 0)),  # the third window gets no click
        scripts=[script(("stdout", "acme: 12 passed\n"), ("stderr", "1 warning\n"))],
    )
    link = harness.connect(secret=body["node_secret"], node=reference, subprotocols=(V2, V1))
    assert link.subprotocol == V2 and link.frames_of("hello.ack"), link.close_code
    assert link.wait_for(lambda: bus.is_present(body["node_id"]))
    friday["router.node_action_args"] = json.dumps(
        {"node_id": body["node_id"], "capability": "repo.command.run", "args": ACME}
    )

    # 1. Typed: draft -> approval -> approve -> grant -> window -> output.
    conversation = Conversation.objects.create(workspace=firm, created_by=owner)
    (draft,) = _turn(client, conversation, TYPED)
    assert draft["tool_name"] == "node_action" and draft["action_class"] == "gate"
    assert not ApprovalRequest.objects.exists()
    approval = _click_card(client, firm, draft)
    assert approval.status == "pending"
    assert approval.payload["trust"]["verdict"] == "ok"
    assert approval.payload["source_input_mode"] == "text"
    assert not link.frames_of("invoke")  # nothing reaches the node before approval
    decided = client.post(f"{APPROVALS_URL}/{approval.pk}/approve", format="json")
    assert decided.status_code == 200, decided.content
    execution = NodeActionExecution.objects.get(approval=approval)
    assert _pump_until(link, lambda: _execution_status(execution.pk) in TERMINAL_STATES)
    execution.refresh_from_db()
    assert (execution.status, execution.confirm_state, execution.exit_code) == ("ok", "approved", 0)
    (invoke,) = link.frames_of("invoke")
    assert invoke["class"] == "gate" and invoke["grant"]["approval_id"] == str(approval.pk)
    assert invoke["grant"]["expires_at"] - invoke["grant"]["issued_at"] <= 120
    assert reference.window.shown and approval.pk.hex[:8] in reference.window.shown[0].replace(
        "-", ""
    )
    assert [call["argv"] for call in reference.executor.calls] and len(
        reference.executor.calls
    ) == 1

    # The HUD terminal stream carries the scrubbed output, then `end`.
    stream = client.get(f"/api/v1/hud/terminal/{execution.pk}/stream")
    assert stream.status_code == 200
    frames = _sse_frames(stream)
    chunks = [(f["stream"], f["text"]) for f in frames if f["type"] == "chunk"]
    assert chunks == [("stdout", "acme: 12 passed\n"), ("stderr", "1 warning\n")]
    assert frames[-1]["type"] == "end" and frames[-1]["status"] == "ok"

    # One chain row (v2, gate, bound to the approval) and one audit row.
    row = NodeInvocation.objects.get(trace_id=execution.trace_id)
    assert (row.invocation_class, row.status, row.hash_version) == ("gate", "ok", 2)
    assert row.approval_id == approval.pk and row.workspace_id == firm.pk
    assert verify_chain().ok
    assert (
        AuditLog.objects.filter(action="nodes.invoke", resource_id=str(execution.pk)).count() == 1
    )
    assert ModelUsage.objects.filter(purpose="control.trust_check").count() == 1

    # 2. The same turn as voice: a draft only.
    approvals_before = ApprovalRequest.objects.count()
    invokes_before = len(link.frames_of("invoke"))
    voice_conversation = Conversation.objects.create(workspace=firm, created_by=owner)
    (voice_draft,) = _turn(client, voice_conversation, TYPED, input_mode="voice")
    assert voice_draft["tool_name"] == "node_action" and voice_draft["where_to_act"]
    _pump_until(link, lambda: False, timeout=0.3)
    assert ApprovalRequest.objects.count() == approvals_before
    assert NodeActionExecution.objects.count() == 1
    assert len(link.frames_of("invoke")) == invokes_before
    assert ModelUsage.objects.filter(purpose="control.trust_check").count() == 1

    # 3. Confirm class: the card click -> the node's window -> `ok`.
    opened = client.post(
        f"/api/v1/nodes/{body['node_id']}/actions",
        {"capability": "apps.open", "args": TERMINAL},
        format="json",
    )
    assert opened.status_code == 202, opened.content
    confirm_id = opened.json()["id"]
    assert opened.json()["approval_id"] is None
    assert _pump_until(link, lambda: _execution_status(confirm_id) in TERMINAL_STATES)
    confirm = NodeActionExecution.objects.get(pk=confirm_id)
    assert (confirm.status, confirm.confirm_state) == ("ok", "approved")
    assert reference.executor.calls[-1]["bundle_id"] == "com.apple.Terminal"
    assert ApprovalRequest.objects.count() == approvals_before  # no approval for confirm class
    confirm_row = NodeInvocation.objects.get(trace_id=confirm.trace_id)
    assert (confirm_row.invocation_class, confirm_row.status) == ("confirm", "ok")
    ran_before = len(reference.executor.calls)

    # 4. The kill switch while a window is open: nothing runs.
    pending = _click_card(client, firm, draft)
    decided = client.post(f"{APPROVALS_URL}/{pending.pk}/approve", format="json")
    assert decided.status_code == 200, decided.content
    last = NodeActionExecution.objects.get(approval=pending)
    assert _pump_until(link, lambda: _execution_status(last.pk) == "confirming")
    assert reference.window.is_open
    engaged = client.post("/api/v1/auth/nodes/kill-switch", format="json")
    assert engaged.status_code == 200, engaged.content
    assert link.wait_closed(5.0)
    assert not reference.window.is_open
    assert reference.window.closed_by == ["disconnect"]
    assert link.wait_for(lambda: _execution_status(last.pk) == "node_disconnected")
    assert len(reference.executor.calls) == ran_before
    assert link.wait_for(lambda: not bus.is_present(body["node_id"]))
    last_row = NodeInvocation.objects.get(trace_id=last.trace_id)
    assert last_row.status == "node_disconnected"
    assert verify_chain().ok
