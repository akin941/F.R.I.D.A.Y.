"""Phase 9 closeout end to end (brief §8, "E2E (CI, fakes)"): the Python
reference node, standing in for the deferred Swift app, goes through the
whole server half in one test.

owner issues a node code on the web -> the node pairs through
`POST /api/v1/nodes/pair` (pinning the key the response returns) ->
connects to the real `NodeSocketApp` (in process, `gateway_harness`) ->
`hello` / `hello.ack` -> a FakeAdapter chat turn "Claude Code oturumlarım
ne yapıyor?" in a firm conversation -> fenced rows of that workspace only
in the answer's prompt -> `ok` chain rows (hashes only) and `nodes.invoke`
audit rows -> the owner revokes the node over the web -> `bye{revoked}`
and the socket closes -> the node tools are no longer offered and the next
turn reads nothing.

It lives in `ops` (beside the other closeout e2e tests) because it spans
`accounts`, `nodes`, `tools`, `assistant` and `memory`. Nothing leaves the
process but the test Postgres and Redis: no Mac, no Claude Code, no model."""

import json

import pytest
from django.db import connections
from rest_framework.test import APIClient

from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from assistant import services as assistant_services
from assistant.models import Conversation, Message
from audit.models import AuditLog
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, WorkspaceMappingRule
from nodes import bus
from nodes.audit_chain import verify_chain
from nodes.gateway import NodeSocketApp
from nodes.models import Node, NodeInvocation
from nodes.protocol.signing import load_public_key_b64
from nodes.reference.executor import FakeExecutor
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.reference.readers import Readers
from nodes.tests.gateway_harness import V1, Harness, WallClock
from tools.registry import get_read_tool
from tools.tests.node_reads_helpers import HostileReaders, _firm, _user
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db(transaction=True)

QUESTION = "Claude Code oturumlarım ne yapıyor?"
NODE_TOOLS = ("node_sessions", "node_session_detail", "node_usage")


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("node-e2e-home"))


@pytest.fixture
def harness():
    app = NodeSocketApp(tick_seconds=0.05)
    harness = Harness(app)
    yield harness
    harness.close()
    connections.close_all()


@pytest.fixture
def world():
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
    state = {"owner": owner, "personal": personal, "firm": firm, "node_ids": []}
    yield state
    for node_id in state["node_ids"]:
        for clear in (bus.clear_presence, bus.clear_inbox, bus.clear_inflight):
            clear(node_id)


@pytest.fixture
def chat(settings, monkeypatch):
    """FakeAdapter behind the gateway (a cloud row: the firm read is
    firm-class), the three prompts, the router pinned to `node_sessions`,
    and a spy on every gateway call's messages."""
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    for name, purpose, content in [
        ("friday_system", "chat", "You are Friday."),
        ("friday_router_intent", "router.intent", "Classify the message."),
        ("friday_router_connector_args", "router.connector_args", "Extract the arguments."),
    ]:
        template = PromptTemplate.objects.create(name=name, purpose=purpose)
        version = PromptVersion.objects.create(template=template, version=1, content=content)
        template.active_version = version
        template.save(update_fields=["active_version"])
    ModelProvider.objects.create(
        name="fake-cloud",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=False,
    )
    monkeypatch.setattr(
        assistant_services,
        "parse_intent",
        lambda _text: {"intent": "connector_read", "tool_name": "node_sessions"},
    )
    monkeypatch.setattr(assistant_services, "parse_connector_args", lambda _text: {})
    calls = []
    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        calls.append({"purpose": purpose, "messages": json.dumps(messages, ensure_ascii=False)})
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    return calls


def _post(client, conversation):
    response = client.post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": QUESTION},
        format="json",
    )
    assert response.status_code == 200, response.content
    events = []
    for chunk in b"".join(response.streaming_content).decode().split("\n\n"):
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))
    return events


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


def _reference_node(body, home):
    """The reference node as the Swift app will be: it pins exactly the keys
    the pairing response returned and reads the fixture home."""
    pinned = {
        entry["key_id"]: load_public_key_b64(entry["public_key_b64"])
        for entry in body["server_signing_keys"]
    }
    readers = HostileReaders(
        Readers(
            home=home,
            policy=fixture_policy(home),
            grants={"claude": True, "codex": True},
            window_titles=False,
        )
    )
    clock = WallClock()
    return ReferenceNode(
        node_id=body["node_id"],
        pinned=pinned,
        policy=fixture_policy(home),
        executor=FakeExecutor(clock),
        clock=clock,
        catalogue=default_catalogue(readers.availability()),
        readers=readers,
    )


def test_pair_connect_read_revoke(world, home, harness, chat, web_client):
    owner, firm = world["owner"], world["firm"]
    owner_client = web_client(owner)

    # 1. Pair through the endpoint (the code is issued on the web).
    body = _pair_through_the_endpoint(owner_client)
    world["node_ids"].append(body["node_id"])
    assert body["node_secret"].startswith("jnd_")
    assert body["gateway_path"] == "/ws/node/v1" and body["protocol_version"] == 1
    node_row = Node.objects.get(pk=body["node_id"])
    assert node_row.owner == owner

    # 2. Connect and `hello`: the gateway acks and marks the node present.
    reference = _reference_node(body, home)
    link = harness.connect(secret=body["node_secret"], node=reference, subprotocols=(V1,))
    assert link.frames_of("hello.ack"), link.close_code
    assert link.frames_of("hello.ack")[0]["node_id"] == body["node_id"]
    assert link.wait_for(lambda: bus.is_present(body["node_id"]))
    assert get_read_tool("node_sessions").is_available(firm)
    listed = owner_client.get("/api/v1/nodes").json()
    assert [n["status"] for n in listed if n["id"] == body["node_id"]] == ["connected"]

    # 3. The chat turn: the answer is built from the node's rows, fenced and
    #    filtered to the conversation's workspace.
    conversation = Conversation.objects.create(workspace=firm, created_by=owner)
    events = _post(owner_client, conversation)
    assert events[-1]["type"] == "done"
    assert [c["purpose"] for c in chat] == ["router.intent", "router.connector_args", "chat"]
    prompt = json.loads(chat[-1]["messages"])[0]["content"]
    assert '<connector_result source="friday-node" tool="node_sessions">' in prompt
    assert prompt.count("</connector_result>") == 1
    assert "cc-acme-1" in prompt
    for other in ("cc-friday-1", "cx-friday-1", "cc-scratch-1"):
        assert other not in prompt  # the personal workspace's and unmapped rows
    invokes = link.frames_of("invoke")
    assert invokes and {tuple(i["args"]["cwd_prefixes"]) for i in invokes} == {("~/work",)}
    user_message = Message.objects.get(conversation=conversation, role="user")
    assert user_message.intent_payload == {"tool_name": "node_sessions", "outcome": "ok"}

    # 4. The chain: one `ok` row per invoke sent, in the firm workspace,
    #    hashes only, the chain intact; one `nodes.invoke` audit row each.
    rows = list(NodeInvocation.objects.order_by("seq"))
    assert len(rows) == len(invokes)
    assert {(r.status, r.workspace_id, r.channel) for r in rows} == {("ok", firm.pk, "web")}
    assert {r.nonce for r in rows} == {i["nonce"] for i in invokes}
    stored = json.dumps(list(NodeInvocation.objects.values()), default=str)
    assert "cc-acme-1" not in stored and "~/work" not in stored
    assert verify_chain().ok
    assert AuditLog.objects.filter(action="nodes.invoke").count() == len(rows)

    # 5. Revoke over the web: `bye{revoked}`, the socket closes, the node
    #    may not reconnect.
    revoked = owner_client.post(f"/api/v1/nodes/{body['node_id']}/revoke")
    assert revoked.status_code == 200, revoked.content
    assert revoked.json()["status"] == "revoked"
    assert link.wait_closed(5.0)
    assert link.frames_of("bye")[-1]["reason"] == "revoked"
    assert not reference.reconnect_allowed
    assert link.wait_for(lambda: not bus.is_present(body["node_id"]))

    # 6. The tool is unavailable: not offered, and a new turn reads nothing.
    for name in NODE_TOOLS:
        assert not get_read_tool(name).is_available(firm)
    sent_before = len(link.frames_of("invoke"))
    calls_before = len(chat)
    second = Conversation.objects.create(workspace=firm, created_by=owner)
    _post(owner_client, second)
    # The router no longer sees a node tool: the same "connector_read"
    # answer degrades to plain chat (no argument call, no read).
    message = Message.objects.get(conversation=second, role="user")
    assert message.intent == Message.Intent.CHAT
    assert "outcome" not in (message.intent_payload or {})
    assert "node_sessions" not in json.loads(chat[calls_before]["messages"])[0]["content"]
    assert [c["purpose"] for c in chat[calls_before:]] == ["router.intent", "chat"]
    assert len(link.frames_of("invoke")) == sent_before
    assert NodeInvocation.objects.count() == len(rows)
    # A reconnect with the revoked secret is refused at the handshake.
    again = harness.connect(secret=body["node_secret"], subprotocols=(V1,), wait_hello_ack=False)
    assert again.wait_closed(5.0) and again.close_code == 4401
