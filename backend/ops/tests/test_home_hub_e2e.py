# ruff: noqa: F811 -- pytest fixtures imported from the other e2e modules are redefined
"""Phase 13 closeout end to end (brief §8): one home server, two PCs, the TV.

1. **Two PCs on one live home server.** Django and the real node gateway
   (`config.asgi_nodes`) on one loopback TLS port (as Caddy serves them on
   the home box). "MacBook" is the Python reference node on protocol v2
   (standing in for the deferred Swift app) over `NodeClient` with the
   served certificate pinned; "Masaüstü" is the packaged
   `friday-node-linux` daemon (its own process and environment, v1,
   read-only) paired over HTTPS with `platform: linux`. Rooms and labels are
   set through the owner API; the HUD `nodes` section shows both; a typed
   read naming each PC reaches that PC only; an unnamed read with both
   online asks and invokes nothing; a `node_action` for the MacBook is
   approved on the web and clicked in the MacBook's window while the Linux
   node is connected and runs on the MacBook only; "Masaüstünde testleri
   çalıştır" is refused (`node_lacks_capability`), never moved to the
   MacBook; `nodes_labels` passes.
2. **A `node_voice` turn from the MacBook** through the Phase 11 reference
   voice host and a real `friday-voice run` child: an unnamed action
   request resolves to the speaking node (`origin`); naming "Masaüstü"
   is refused naming it. Drafts only, nothing requested.
3. **"Salondaki TV'yi aç" by voice** -> a draft card -> the owner's tap and
   web approval -> exactly one call to the fake Home Assistant -> the state
   read back. An entity that is not allowlisted is never drafted and never
   called (typed model proposal, direct request).
4. **Backup -> restore drill** on this test run's Postgres (a local restic
   repository, a throwaway age key) and every Phase 13 preflight row
   evaluated (`check_home` + `check_home_control`).

Nothing reaches a real model, Home Assistant, Mac, bucket or network beyond
the loopback. Parts 1-2 skip where `node/friday-node-linux` /
`node/friday-voice` have no environment (unless `FRIDAY_LINUX_NODE=1` /
`FRIDAY_VOICE_E2E=1` demand them); part 4 skips without the home-ops tools
(unless `FRIDAY_HOME_OPS=1`). The real-hardware run of all of this is the
Mac session M2 and the owner's M3 (pending owner evaluation)."""

import asyncio
import datetime
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connections
from rest_framework.test import APIClient

from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from ai_core.management.commands import seed_ai_defaults
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from approvals.models import ApprovalRequest
from assistant.models import Conversation, Message
from audit.models import AuditLog
from connectors.manifest import get_manifest
from connectors.models import ConnectorWriteExecution
from connectors.tests.conftest import committed, enable  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from core.channels import CHANNEL_WEB
from core.speech import phrase_key
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, WorkspaceMappingRule
from nodes import bus, output, targeting
from nodes.audit_chain import verify_chain
from nodes.conformance.gateway import self_signed_context
from nodes.models import EXECUTION_TERMINAL_STATUSES, Node, NodeActionExecution, NodeInvocation
from nodes.protocol.signing import load_public_key_b64
from nodes.reference.confirm import ConfirmationWindowDouble, Decision
from nodes.reference.executor import FakeExecutor
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.reference.readers import Readers
from nodes.reference.transport import NodeClient
from nodes.services import relabel_node
from nodes.tests.gateway_harness import WallClock
from nodes.tests.linux_node import linux_node_command, linux_node_env, write_policy
from nodes.tests.two_nodes import add_node
from ops import preflight
from ops.tests.test_live_voice_e2e import (  # noqa: F401 -- fixtures and helpers
    _live_conversations,
    _usage,
    friday,
    make_link,
    node_home,
    node_world,
    run,
    speech,
    stack,
)
from tools import node_action_ports
from tools.owner_reads import AUDIT_ACTION
from tools.tests.conftest import script, server_scrubber
from tools.tests.node_reads_helpers import HostileReaders, _firm, _user
from tools.tests.test_node_action_trust import ACME, propose
from workspaces import rooms
from workspaces.models import Room, Workspace

pytestmark = pytest.mark.django_db(transaction=True)

CHAT_URL = "/api/v1/ai/conversations/{}/messages"
APPROVALS_URL = "/api/v1/approvals/requests"
SESSIONS_INTENT = json.dumps({"intent": "connector_read", "tool_name": "node_sessions"})
NODE_INTENT = json.dumps({"intent": "tool_request", "tool_name": "node_action"})
HA_INTENT = json.dumps({"intent": "tool_request", "tool_name": "ha_call_service"})
CONSISTENT = '{"verdict": "consistent", "reason_code": "matches_request"}'
ASK = "Hangi bilgisayar: MacBook mu, Masaüstü mü?"
LACKS = "Masaüstü bu işlemi sunmuyor; başka bir bilgisayarda çalıştırmadım."
LINUX_SESSION = "cc-linux-hub-1"
TV = "media_player.salon_tv"
LIGHT = "light.salon"
# A voice turn lands in the personal workspace: a personal repo (~/code).
CODE = {"repo_path": "~/code/friday", "command_kind": "test"}


def _prompt(name, purpose, content):
    template, _ = PromptTemplate.objects.get_or_create(name=name, defaults={"purpose": purpose})
    version, _ = PromptVersion.objects.get_or_create(
        template=template, version=1, defaults={"content": content}
    )
    template.active_version = version
    template.save(update_fields=["active_version"])


def _events(response):
    events = []
    for chunk in b"".join(response.streaming_content).split(b"\n\n"):
        if chunk.startswith(b"data: "):
            events.append(json.loads(chunk[len(b"data: ") :]))
    return events


def _turn(client, conversation, content, **body):
    response = client.post(
        CHAT_URL.format(conversation.pk), {"content": content, **body}, format="json"
    )
    assert response.status_code == 200, response.content
    return _events(response)


def _drafts(events):
    return [event for event in events if event["type"] == "draft"]


def _reply(conversation):
    return (
        Message.objects.filter(conversation=conversation, role=Message.Role.ASSISTANT)
        .order_by("-created_at")
        .values_list("content", flat=True)
        .first()
    )


def _wait(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# --- 1. two PCs on one live home server --------------------------------------------


class LiveHomeServer:
    """Django + the node gateway on one loopback TLS port (the home box's
    Caddy in one process); keeps the client context that pins the served
    throwaway certificate."""

    def __init__(self):
        import uvicorn
        from django.core.asgi import get_asgi_application

        from config import asgi_nodes

        _server_ssl, self.client_ssl, self.cert_path = self_signed_context()
        self.node_app = asgi_nodes.node_app
        django_app = get_asgi_application()

        async def app(scope, receive, send):
            if scope["type"] == "http" and scope["path"] != "/healthz":
                return await django_app(scope, receive, send)
            return await asgi_nodes.application(scope, receive, send)

        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(16)
        self.port = self.socket.getsockname()[1]
        config = uvicorn.Config(
            app,
            ws="wsproto",
            lifespan="on",
            log_level="warning",
            ws_max_size=256 * 1024,
            ssl_certfile=self.cert_path,
            ssl_keyfile=str(Path(self.cert_path).parent / "key.pem"),
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )

    def start(self):
        self.thread.start()
        assert _wait(lambda: self.server.started), "uvicorn did not start"
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
        from asgiref.sync import sync_to_async

        asyncio.run(sync_to_async(connections.close_all, thread_sensitive=True)())
        connections.close_all()


def _linux_home(base: Path) -> Path:
    """The Linux PC's real-shaped `$HOME`: one recent Claude Code session in
    `~/work/acme` (the 8A rule maps `~/work` to the firm)."""
    home = base / "home"
    cwd = home / "work" / "acme"
    cwd.mkdir(parents=True)
    now = datetime.datetime.now(datetime.UTC)

    def at(minutes):
        return (now - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    lines = [
        {
            "type": "user",
            "uuid": "u-1",
            "sessionId": LINUX_SESSION,
            "cwd": str(cwd),
            "timestamp": at(10),
            "message": {"role": "user", "content": "Run the acme tests."},
        },
        {
            "type": "assistant",
            "uuid": "a-1",
            "sessionId": LINUX_SESSION,
            "cwd": str(cwd),
            "timestamp": at(9),
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-5",
                "content": [{"type": "text", "text": "Running them."}],
            },
        },
    ]
    transcript = home / ".claude" / "projects" / "-home-work-acme" / f"{LINUX_SESSION}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return Path(os.path.realpath(home))


@pytest.fixture
def hub_world():
    """The owner, a personal and a firm workspace, the 8A path rules
    (`~/work` -> firm, `~/code` -> personal), the server output scrubber
    and the real trust check (as at startup)."""
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
    world = SimpleNamespace(owner=owner, personal=personal, firm=firm, node_ids=[])
    yield world
    for node_id in world.node_ids:
        for clear in (bus.clear_presence, bus.clear_inbox, bus.clear_inflight):
            clear(node_id)
    output.reset_scrubber()
    node_action_ports.restore_ports(ports)


@pytest.fixture
def hub_friday(settings, monkeypatch):
    """FakeAdapter behind the gateway, a script per purpose, every call
    recorded (the purposes prove that resolution adds no model call)."""
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", seed_ai_defaults.FRIDAY_ROUTER_INTENT_V1)
    _prompt("friday_router_connector_args", "router.connector_args", "Extract the arguments.")
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
        "router.intent": SESSIONS_INTENT,
        "router.connector_args": json.dumps({"tool": "claude"}),
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


@pytest.fixture(scope="module")
def mac_home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("home-hub-mac"))


@pytest.fixture
def live_home():
    server = LiveHomeServer().start()
    yield server
    server.stop()


def _pair_mac(client):
    """The MacBook pairs through the endpoint (`platform: macos`, as Swift M1
    will send it)."""
    issued = client.post(
        "/api/v1/auth/pairing-codes", {"kind": "strong", "device_kind": "node"}, format="json"
    )
    assert issued.status_code == 201, issued.content
    paired = APIClient().post(
        "/api/v1/nodes/pair",
        {
            "code": issued.json()["code"],
            "node_name": "MacBook",
            "protocol_versions": [1, 2],
            "platform": "macos",
        },
        format="json",
    )
    assert paired.status_code == 201, paired.content
    return paired.json()


def _mac_reference(body, home):
    """The reference node v2 as the Swift app will be: the pairing
    response's keys pinned, `~/work/acme` mapped locally, its own
    confirmation window (the scripted owner approves) and the fake
    executor; reads over the fixture home at the fixture time."""
    pinned = {
        entry["key_id"]: load_public_key_b64(entry["public_key_b64"])
        for entry in body["server_signing_keys"]
    }
    clock = WallClock()
    readers = HostileReaders(
        Readers(home=home, policy=fixture_policy(home), grants={"claude": True, "codex": True})
    )
    return ReferenceNode(
        node_id=body["node_id"],
        pinned=pinned,
        policy=fixture_policy(home),
        executor=FakeExecutor(clock, scripts=[script(("stdout", "acme: 12 passed\n"))]),
        clock=clock,
        window=ConfirmationWindowDouble(clock, [Decision("approve", 0)]),
        catalogue=default_catalogue(readers.availability()),
        readers=readers,
    )


def _pair_linux(command, env, live, owner_client):
    issued = owner_client.post(
        "/api/v1/auth/pairing-codes", {"kind": "strong", "device_kind": "node"}, format="json"
    )
    assert issued.status_code == 201, issued.content
    raw = issued.json()["code"]
    paired = subprocess.run(  # noqa: S603 - fixed argv
        [
            *command,
            "pair",
            "--server",
            f"https://127.0.0.1:{live.port}",
            "--code",
            raw,
            "--ca",
            live.cert_path,
            "--name",
            "Masaüstü",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert paired.returncode == 0, paired.stdout + paired.stderr
    assert raw not in paired.stdout + paired.stderr and "jnd_" not in paired.stdout
    return Node.objects.get(name="Masaüstü")


def _hud_nodes(client, workspace, settings):
    settings.HUD_STREAM_INTERVAL_SECONDS = 1
    settings.HUD_STREAM_MAX_SECONDS = 1
    response = client.get("/api/v1/hud/events", {"workspace": str(workspace.pk)})
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode()
    frames = [
        json.loads(raw[len("data: ") :]) for raw in body.split("\n\n") if raw.startswith("data: ")
    ]
    (snapshot,) = [frame for frame in frames if frame.get("type") == "snapshot"]
    return snapshot["nodes"]


def _invocations(node):
    return NodeInvocation.objects.filter(node_id=node.pk).count()


def _execution_status(pk):
    return NodeActionExecution.objects.filter(pk=pk).values_list("status", flat=True).first()


def test_two_pcs_on_one_live_home_server(
    hub_world, hub_friday, live_home, mac_home, web_client, settings
):
    command = linux_node_command()  # skips before any process starts
    owner, firm = hub_world.owner, hub_world.firm
    client = web_client(owner)

    # --- the MacBook (reference node v2) over pinned TLS -------------------
    body = _pair_mac(client)
    hub_world.node_ids.append(body["node_id"])
    mac_node = _mac_reference(body, mac_home)
    mac_client = NodeClient(
        mac_node,
        "127.0.0.1",
        live_home.port,
        secret=body["node_secret"],
        ssl_context=live_home.client_ssl,
    ).start()

    # --- Masaüstü (the packaged Linux node) over pinned TLS ----------------
    base = Path(tempfile.mkdtemp(prefix="fnh-"))  # short: the hook socket path
    env = linux_node_env(base, _linux_home(base))
    write_policy(env, claude=True)
    desk = _pair_linux(command, env, live_home, client)
    hub_world.node_ids.append(str(desk.pk))
    daemon = subprocess.Popen(  # noqa: S603 - fixed argv
        [*command, "run"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env
    )
    try:
        mac = Node.objects.get(pk=body["node_id"])
        assert _wait(lambda: bus.is_present(mac.pk) and bus.is_present(desk.pk)), (
            mac_node.closed_reason,
            daemon.poll(),
        )
        assert mac_client.version == 2
        mac.refresh_from_db()
        desk.refresh_from_db()
        assert (mac.platform, desk.platform) == ("macos", "linux")

        # --- rooms and labels (the owner's /settings/home and /devices) ----
        study = rooms.create_room(
            actor=owner, workspace=hub_world.personal, channel=CHANNEL_WEB, name="Çalışma odası"
        )
        rooms.create_room(
            actor=owner, workspace=hub_world.personal, channel=CHANNEL_WEB, name="Salon"
        )
        for node, role in ((mac, "laptop"), (desk, "workstation")):
            patched = client.patch(
                f"/api/v1/nodes/{node.pk}", {"room_id": str(study.pk), "role": role}, format="json"
            )
            assert patched.status_code == 200, patched.content

        # --- the HUD's Düğümler panel shows both ---------------------------
        section = _hud_nodes(client, hub_world.personal, settings)
        rows = {row["name"]: row for row in section["items"]}
        assert section["total"] == 2 and section["online"] == 2
        assert rows["MacBook"]["status"] == rows["Masaüstü"]["status"] == "connected"
        assert rows["MacBook"]["platform"] == "macos" and rows["Masaüstü"]["platform"] == "linux"
        assert (
            rows["MacBook"]["room"]["name"] == rows["Masaüstü"]["room"]["name"] == "Çalışma odası"
        )
        assert rows["MacBook"]["invocable_action_count"] > 0
        assert rows["Masaüstü"]["invocable_action_count"] == 0

        conversation = Conversation.objects.create(workspace=firm, created_by=owner)

        # --- unnamed, both online, neither recently active: ask, call nobody
        hub_friday["calls"].clear()
        _turn(client, conversation, "Claude Code oturumlarım ne yapıyor?")
        assert _reply(conversation).startswith(ASK)
        assert NodeInvocation.objects.count() == 0
        assert hub_friday["calls"] == ["router.intent", "router.connector_args"]
        ask_row = AuditLog.objects.filter(action=AUDIT_ACTION).latest("created_at")
        assert ask_row.metadata["outcome"] == "node_ambiguous"
        assert set(ask_row.metadata["candidates"]) == {str(mac.pk), str(desk.pk)}

        # --- named: each read reaches that PC only -------------------------
        hub_friday["calls"].clear()
        _turn(client, conversation, "Masaüstünde Claude Code oturumlarım ne yapıyor?")
        assert (_invocations(desk), _invocations(mac)) == (1, 0)
        assert NodeInvocation.objects.get(node_id=desk.pk).status == "ok"
        # Resolution adds no model call: the router's two, then the answer.
        assert hub_friday["calls"][:3] == ["router.intent", "router.connector_args", "chat"]
        assert set(hub_friday["calls"]) == {"router.intent", "router.connector_args", "chat"}
        read_row = AuditLog.objects.filter(action=AUDIT_ACTION).latest("created_at")
        assert read_row.metadata["node_id"] == str(desk.pk)
        assert read_row.metadata["target_reason"] == "mention"

        _turn(client, conversation, "MacBook'ta Claude Code oturumlarım ne yapıyor?")
        assert (_invocations(desk), _invocations(mac)) == (1, 1)
        assert NodeInvocation.objects.get(node_id=mac.pk).status == "ok"

        # --- a node action for the MacBook while the Linux node is active --
        hub_friday["router.intent"] = NODE_INTENT
        hub_friday["router.node_action_args"] = json.dumps(
            {"node_id": str(mac.pk), "capability": "repo.command.run", "args": ACME}
        )
        action_conversation = Conversation.objects.create(workspace=firm, created_by=owner)
        (draft,) = _drafts(
            _turn(client, action_conversation, "MacBook'ta acme'de testleri çalıştır")
        )
        assert draft["payload"]["node_id"] == str(mac.pk)
        assert not ApprovalRequest.objects.exists()
        clicked = client.post(
            APPROVALS_URL,
            {
                "workspace": str(firm.pk),
                "tool_name": draft["tool_name"],
                "payload": draft["payload"],
            },
            format="json",
        )
        assert clicked.status_code == 201, clicked.content
        approval = ApprovalRequest.objects.get(pk=clicked.json()["id"])
        assert approval.payload["trust"]["verdict"] == "ok"
        # The card on /approvals names the PC and its room.
        listed = client.get(APPROVALS_URL, {"workspace": str(firm.pk)}).json()
        (summary,) = [
            row["display_summary"]
            for row in (listed["results"] if isinstance(listed, dict) else listed)
        ]
        assert (summary["node_name"], summary["node_room"]) == ("MacBook", "Çalışma odası")
        desk_rows = _invocations(desk)
        decided = client.post(f"{APPROVALS_URL}/{approval.pk}/approve", format="json")
        assert decided.status_code == 200, decided.content
        execution = NodeActionExecution.objects.get(approval=approval)
        assert _wait(lambda: _execution_status(execution.pk) in EXECUTION_TERMINAL_STATUSES)
        execution.refresh_from_db()
        assert (execution.status, execution.confirm_state, execution.exit_code) == (
            "ok",
            "approved",
            0,
        )
        assert str(execution.node_id) == str(mac.pk)
        assert len(mac_node.executor.calls) == 1 and mac_node.window.shown
        row = NodeInvocation.objects.get(trace_id=execution.trace_id)
        assert (row.node_id, row.invocation_class, row.approval_id) == (mac.pk, "gate", approval.pk)
        assert _invocations(desk) == desk_rows  # nothing reached the Linux PC
        assert verify_chain().ok

        # --- the Linux node is named for an action: refused, never moved ---
        approvals_before = ApprovalRequest.objects.count()
        # The model still picks the MacBook (it was last active): the owner's
        # words name the desktop, so the pick is refused, never followed.
        mismatch = Conversation.objects.create(workspace=firm, created_by=owner)
        assert _drafts(_turn(client, mismatch, "Masaüstünde testleri çalıştır")) == []
        assert "uyuşmuyor" in _reply(mismatch)
        # The model agrees with the owner: the desktop offers no action.
        hub_friday["router.node_action_args"] = json.dumps(
            {"node_id": str(desk.pk), "capability": "repo.command.run", "args": ACME}
        )
        refused_conversation = Conversation.objects.create(workspace=firm, created_by=owner)
        assert _drafts(_turn(client, refused_conversation, "Masaüstünde testleri çalıştır")) == []
        assert _reply(refused_conversation) == LACKS
        assert ApprovalRequest.objects.count() == approvals_before
        assert NodeActionExecution.objects.count() == 1
        assert _invocations(desk) == desk_rows

        # --- preflight: labels unique, rooms set -------------------------
        labels = {r.name: r for r in preflight.check_home_control()}["nodes_labels"]
        assert labels.status == preflight.PASS, labels
        assert labels.detail.startswith("2 node(s) of 1 owner(s)")

        # --- revocation ends the Linux daemon without a reconnect ---------
        assert live_home.node_app.subscriber_ready.wait(5)
        from accounts import devices

        devices.revoke_device(device=Node.objects.get(pk=desk.pk).device, actor=owner)
        code = daemon.wait(15)
    finally:
        mac_client.stop()
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(5)
    output_text = daemon.stdout.read().decode("utf-8", "replace")
    assert code == 0, output_text
    assert "not reconnecting: bye:revoked" in output_text
    assert "jnd_" not in output_text


# --- 2. a node_voice turn from the MacBook -----------------------------------------


@pytest.fixture
def voice_home(stack, monkeypatch):
    """The Phase 11 closeout stack (a live Friday over HTTP, the MacBook's
    voice token, the reference node linked in process) plus the Linux
    "Masaüstü" (v1, read capabilities only, online), both PCs labelled in
    the study. `resolve_target` is spied on, never changed."""
    world = stack.world
    study = rooms.create_room(
        actor=world.owner, workspace=world.personal, channel=CHANNEL_WEB, name="Çalışma odası"
    )
    relabel_node(
        actor=world.owner, node=world.node, channel=CHANNEL_WEB, room_id=study.pk, role="laptop"
    )
    desk = add_node(world.owner, name="Masaüstü", platform="linux", role="workstation", room=study)
    resolved = []
    real = targeting.resolve_target

    def spy(*args, **kwargs):
        target = real(*args, **kwargs)
        resolved.append(target)
        return target

    monkeypatch.setattr(targeting, "resolve_target", spy)
    heard = {"text": ""}

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text=heard["text"], audio_seconds=1.0, output_tokens=6, raw_usage={"fake": True}
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    yield SimpleNamespace(stack=stack, desk=desk, resolved=resolved, heard=heard)
    bus.clear_presence(desk.pk)
    bus.clear_inbox(desk.pk)


def _voice_user_messages(world):
    return list(
        Message.objects.filter(
            conversation__workspace=world.personal, role=Message.Role.USER, channel="node_voice"
        ).order_by("created_at")
    )


def test_a_node_voice_turn_from_the_macbook_targets_the_speaking_node(voice_home):
    stack, desk = voice_home.stack, voice_home.desk
    world = stack.world
    # The owner names no PC; the model's pick is the MacBook.
    propose(stack.friday, world.node, "repo.command.run", CODE)
    voice_home.heard["text"] = "friday'de testleri çalıştır"
    result = run(stack, "node_action").result()
    assert result["outcome"]["turns"] == [True] and result["failures"] == []
    (user,) = _voice_user_messages(world)
    assert user.intent == "tool_request"
    assert user.intent_payload["tool_name"] == "node_action"
    assert user.intent_payload["payload"]["node_id"] == str(world.node.pk)
    (target,) = voice_home.resolved
    assert target.reason == targeting.REASON_ORIGIN and target.node.pk == world.node.pk

    # Naming the desktop by voice: refused naming it, never the speaking Mac.
    voice_home.resolved.clear()
    voice_home.heard["text"] = "Masaüstünde friday'de testleri çalıştır"
    propose(stack.friday, desk, "repo.command.run", CODE)
    result = run(stack, "ptt").result()
    assert result["outcome"]["turns"] == [True]
    (target,) = voice_home.resolved
    assert target.code == targeting.NODE_LACKS_CAPABILITY and target.node.pk == desk.pk
    second = _voice_user_messages(world)[-1]
    assert second.intent == "chat"
    assert _reply(second.conversation) == LACKS

    # Drafts only: nothing requested, nothing sent to either PC.
    stack.link.pump()
    assert not ApprovalRequest.objects.exists()
    assert not NodeActionExecution.objects.exists()
    assert not NodeInvocation.objects.exists()
    assert bus.drain_inbox(desk.pk) == [] and bus.drain_inbox(world.node.pk) == []


@pytest.fixture
def owner(node_world):
    """The installation owner, for the connector fixtures (`enable`)."""
    return node_world.owner


# --- 3. the TV: voice draft -> web approval -> one HA call ----------------------------


@pytest.fixture
def tv_home(stack, ha, enable, settings, monkeypatch):
    """The salon TV allowlisted for `turn_on` (room Salon, label "TV"); the
    fake HA also knows `light.salon`, which is NOT allowlisted."""
    settings.HA_ALLOWED_DOMAINS = []
    world = stack.world
    ha.states[TV] = salon_tv_state()
    enable(get_manifest("home_assistant"), world.personal)
    salon = Room.objects.create(workspace=world.personal, name="Salon", key=phrase_key("Salon"))
    allow(world.personal, TV, ["turn_on"], label="TV", room=salon)
    _prompt("friday_router_connector_args", "router.connector_args", "Extract the arguments.")
    ha.requests.clear()

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text="Hey Friday, salondaki TV'yi aç.",
            audio_seconds=1.0,
            output_tokens=6,
            raw_usage={"fake": True},
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return stack


def _write_done():
    return ConnectorWriteExecution.objects.exclude(status__in=("pending", "running")).exists()


def test_the_tv_by_voice_is_one_approved_call_and_its_state_read_back(tv_home, ha, web_client):
    world = tv_home.world
    owner_client = web_client(world.owner)
    result = run(tv_home, "ptt").result()
    assert result["outcome"]["turns"] == [True] and result["failures"] == []
    (user,) = _voice_user_messages(world)
    assert user.intent_payload["tool_name"] == "ha_call_service"
    payload = user.intent_payload["payload"]
    assert payload == {
        "arguments": {"domain": "media_player", "service": "turn_on", "entity_id": TV, "data": {}}
    }
    # By voice: a card, never a request, never a call; no router model call.
    assert _usage("router.intent") == 0
    assert not ApprovalRequest.objects.exists() and ha.posts == []

    # The owner taps the card on /chat/<id>, then approves on /approvals.
    tapped = owner_client.post(
        APPROVALS_URL,
        {"workspace": str(world.personal.pk), "tool_name": "ha_call_service", "payload": payload},
        format="json",
    )
    assert tapped.status_code == 201, tapped.content
    assert ha.posts == []
    request_id = tapped.json()["id"]
    decided = owner_client.post(f"{APPROVALS_URL}/{request_id}/approve", format="json")
    assert decided.status_code == 200, decided.content
    assert _wait(_write_done)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "succeeded", (execution.status, execution.error_type)
    # Exactly one Home Assistant call, then the state read back.
    assert ha.posts == [("/api/services/media_player/turn_on", {"entity_id": TV})]
    assert ha.states[TV]["state"] == "on"
    structured = json.loads(execution.result["text"].split("\n\n")[-1])
    assert '"on"' in json.dumps(structured)

    # Deciding again calls nothing more.
    owner_client.post(f"{APPROVALS_URL}/{request_id}/approve", format="json")
    assert len(ha.posts) == 1


def test_an_entity_that_is_not_allowlisted_is_never_drafted_or_called(tv_home, ha, web_client):
    world = tv_home.world
    owner_client = web_client(world.owner)
    light_on = {"domain": "light", "service": "turn_on", "entity_id": LIGHT, "data": {}}
    # Typed: the model proposes the salon light (known to HA, not allowlisted).
    tv_home.friday["router.intent"] = HA_INTENT
    tv_home.friday["router.connector_args"] = json.dumps(light_on)
    conversation = Conversation.objects.create(workspace=world.personal, created_by=world.owner)
    assert _drafts(_turn(owner_client, conversation, "salondaki lambayı aç")) == []
    assert "izin listende yok" in _reply(conversation)
    # A hand-made request for it: refused at request time.
    refused = owner_client.post(
        APPROVALS_URL,
        {
            "workspace": str(world.personal.pk),
            "tool_name": "ha_call_service",
            "payload": {"arguments": light_on},
        },
        format="json",
    )
    assert refused.status_code == 400
    assert refused.json()["detail"].startswith("entity_not_allowed")
    assert not ApprovalRequest.objects.exists()
    assert ha.posts == []


# --- 4. backup -> drill, and every Phase 13 preflight row --------------------------

PHASE13_ROWS = [
    "home_lan_only",
    "home_tls_pin",
    "home_backup_fresh",
    "home_restore_drill_age",
    "home_disk_encryption",
    "nodes_labels",
    "ha_entities",
    "linux_node_vendor",
    "node_protocol_frozen",
]


def test_backup_drill_round_trip_and_every_phase_13_row(tmp_path, settings, monkeypatch):
    # The round-trip helpers skip (module level) without age/restic/pg16,
    # unless FRIDAY_HOME_OPS=1 demands them.
    from ops.tests import test_home_backup_restore as backup

    home = backup.make_backup_home(tmp_path)
    backup._backup(home)
    status = backup._status(home)
    scratch = backup.create_scratch_db()
    try:
        completed = backup._drill(home, scratch)
    finally:
        backup.drop_scratch_db(scratch)
    assert "node audit chain OK" in completed.stdout
    drill = backup._drill_status(home)
    assert (drill["dump"], drill["result"]) == (status["dump"], "pass")

    settings.HOME_BACKUP_STATUS_PATH = str(home["dir"] / "status" / "status.json")
    settings.HOME_RESTORE_DRILL_STATUS_PATH = str(home["dir"] / "status" / "drill.json")
    settings.HOME_FDE_CONFIRMED_ON = "2026-11-20"
    settings.ALLOWED_HOSTS = ["friday-home.local", "192.168.1.20"]
    settings.PREFLIGHT_NODE_TLS_URL = ""
    monkeypatch.setattr(preflight, "resolve_host", lambda host: [])  # no DNS in tests
    results = preflight.run_preflight(
        checks=(
            ("home", preflight.check_home),
            ("home_control", preflight.check_home_control),
        )
    )
    rows = {row.name: row for row in results}
    assert [row.name for row in results] == PHASE13_ROWS
    assert rows["home_backup_fresh"].status == preflight.PASS, rows["home_backup_fresh"]
    assert rows["home_restore_drill_age"].status == preflight.PASS
    assert rows["home_disk_encryption"].status == preflight.PASS
    # No node paired and no entity allowlisted here; the LAN URL and the TLS
    # pin are owner settings: warnings (owner steps), never failures.
    for name in ("home_lan_only", "home_tls_pin", "nodes_labels", "ha_entities"):
        assert rows[name].status == preflight.WARN, rows[name]
    assert rows["node_protocol_frozen"].status != preflight.FAIL, rows["node_protocol_frozen"]
    assert rows["linux_node_vendor"].status != preflight.FAIL, rows["linux_node_vendor"]
    assert not [row for row in results if row.status == preflight.FAIL], results
