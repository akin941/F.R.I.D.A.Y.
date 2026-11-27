"""Phase 13 branch 4 done criterion, server side: the packaged Linux node
(`node/friday-node-linux`, a separate process from its own environment)
pairs with a live test server over pinned TLS (`POST /api/v1/nodes/pair`
with `platform: linux`), connects to the real gateway process
(`config.asgi_nodes`, `uvicorn --ws wsproto`, TLS with a throwaway
self-signed certificate the node pins with `--ca`), and serves the
`node_sessions` read from a real temporary `$HOME`. Its `hello` yields a
v1 snapshot with **zero invocable action capabilities** (D9), and
`revoke_device` ends the daemon without a reconnect.

One uvicorn serves both doors (as Caddy does on the home server): HTTP to
Django, `/ws/node/v1` and `/healthz` to the gateway. It lives under
`config/tests` because it serves the composition roots. Skipped where the
Linux node has no environment (see `nodes.tests.linux_node`)."""

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

import pytest
from django.db import connections

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from core.channels import CHANNEL_WEB
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, WorkspaceMappingRule
from nodes import bus, services
from nodes.conformance.gateway import self_signed_context
from nodes.models import CapabilitySnapshot, Node
from nodes.tests.linux_node import linux_node_command, linux_node_env, write_policy
from tools.registry import get_read_tool
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db(transaction=True)

SESSION_ID = "cc-linux-1"


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _home(base: Path) -> Path:
    """A real-shaped `$HOME` with one recent Claude Code session."""
    home = base / "home"
    cwd = home / "work" / "acme"
    cwd.mkdir(parents=True)
    now = datetime.datetime.now(datetime.UTC)
    lines = [
        {
            "type": "user",
            "uuid": "u-1",
            "sessionId": SESSION_ID,
            "cwd": str(cwd),
            "timestamp": _iso(now - datetime.timedelta(minutes=10)),
            "message": {"role": "user", "content": "Run the acme tests."},
        },
        {
            "type": "assistant",
            "uuid": "a-1",
            "sessionId": SESSION_ID,
            "cwd": str(cwd),
            "timestamp": _iso(now - datetime.timedelta(minutes=9)),
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-5",
                "content": [{"type": "text", "text": "Running them."}],
            },
        },
    ]
    transcript = home / ".claude" / "projects" / "-home-work-acme" / f"{SESSION_ID}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return Path(os.path.realpath(home))


class LiveHome:
    """Django + the node gateway on one loopback TLS port."""

    def __init__(self):
        import uvicorn
        from django.core.asgi import get_asgi_application

        from config import asgi_nodes

        _server_ssl, _client_ssl, cert_path = self_signed_context()
        self.cert_path = cert_path
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
            ssl_certfile=cert_path,
            ssl_keyfile=str(Path(cert_path).parent / "key.pem"),
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started, "uvicorn did not start"
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
        from asgiref.sync import sync_to_async

        asyncio.run(sync_to_async(connections.close_all, thread_sensitive=True)())
        connections.close_all()


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def live():
    server = LiveHome().start()
    yield server
    server.stop()


def _node_code(owner):
    raw, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    return raw


def test_the_packaged_linux_node_pairs_connects_and_serves_node_sessions(live, owner, settings):
    command = linux_node_command()
    settings.CELERY_TASK_ALWAYS_EAGER = True
    base = Path(tempfile.mkdtemp(prefix="fnl-"))  # short: the hook socket path
    home = _home(base)
    env = linux_node_env(base, home)
    write_policy(env, claude=True)
    raw = _node_code(owner)
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
    assert raw not in paired.stdout + paired.stderr
    assert "jnd_" not in paired.stdout + paired.stderr
    node = Node.objects.get(owner=owner)
    assert node.platform == Node.Platform.LINUX
    assert node.name == "Masaüstü"
    assert f"paired: node {node.pk}" in paired.stdout

    daemon = subprocess.Popen(  # noqa: S603 - fixed argv
        [*command, "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        deadline = time.monotonic() + 30
        while not bus.is_present(node.pk) and time.monotonic() < deadline:
            assert daemon.poll() is None, daemon.stdout.read().decode()
            time.sleep(0.05)
        assert bus.is_present(node.pk)

        # D9: a v1 session, read capabilities only, nothing invocable that acts.
        snapshot = CapabilitySnapshot.objects.filter(node=node).latest("created_at")
        entries = {entry["name"]: entry for entry in snapshot.entries}
        assert {entry["protocol_version"] for entry in entries.values()} == {1}
        assert {entry["class"] for entry in entries.values()} == {"read"}
        assert not [e for e in entries.values() if e["invocable"] and e["class"] != "read"]
        invocable = {name for name, entry in entries.items() if entry["invocable"]}
        # Runbook A12: the owner-turn reads are server contracts now, so the
        # claude one is invocable behind the same ~/.claude grant.
        assert invocable == {
            "claude.sessions.list",
            "claude.sessions.read",
            "claude.transcripts.owner_turns",
        }
        assert entries["codex.threads.list"]["reason"] == "not_granted"
        assert entries["apps.frontmost"]["reason"] == "unsupported"
        assert entries["git.status"]["reason"] == "not_granted"
        assert entries["codex.transcripts.owner_turns"]["reason"] == "not_granted"
        node.refresh_from_db()
        assert node.node_version.startswith("friday-node-linux ")
        assert node.os_version.startswith("Linux")

        outcome = services.invoke_read(
            node=node,
            capability="claude.sessions.list",
            args={"since_hours": 24, "limit": 50},
            workspace=node.workspace,
            actor=owner,
        )
        assert outcome.status == "ok", outcome
        [row] = outcome.output["sessions"]
        assert row["session_id"] == SESSION_ID and row["cwd"] == "~/work/acme"

        # The chat's `node_sessions` read tool, end to end (the 8A path rule
        # maps ~/work to the personal workspace).
        personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
        ensure_default_sources(workspace=personal, created_by=owner)
        hooks = IngestionSource.objects.get(
            workspace=personal, kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS
        )
        WorkspaceMappingRule.objects.create(
            workspace=personal,
            source=hooks,
            rule_type=WorkspaceMappingRule.RuleType.PATH_PREFIX,
            pattern="~/work",
            target_workspace=personal,
            created_by=owner,
        )
        tool = get_read_tool("node_sessions")
        assert tool.is_available(personal)
        result = tool.execute(
            workspace=personal, actor=owner, args={"tool": "claude"}, channel=CHANNEL_WEB
        )
        assert SESSION_ID in result.text and "~/work/acme" in result.text

        assert live.node_app.subscriber_ready.wait(5)
        devices.revoke_device(device=Node.objects.get(pk=node.pk).device, actor=owner)
        code = daemon.wait(15)
    finally:
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(5)
        bus.clear_presence(node.pk)
        bus.clear_inbox(node.pk)
        bus.clear_inflight(node.pk)
    output = daemon.stdout.read().decode("utf-8", "replace")
    assert code == 0, output
    assert "not reconnecting: bye:revoked" in output
    assert "jnd_" not in output
