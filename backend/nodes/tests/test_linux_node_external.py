"""The packaged Linux node against the hostile conformance gateway over a
real localhost TLS socket (Phase 13 brief D10, branch 4 tests).

- every v1 conformance case, played through the external hook against
  `friday-node-linux conformance` (the same suite the Swift app must pass);
- the real daemon (`friday-node-linux run`, a written credential pinned to
  the conformance key and the gateway's self-signed certificate) receives
  every policy-shaped frame and every non-frame-error frame of the v1 suite:
  its `policy.json` (bytes, mtime) and credential are unchanged, it
  reconnects after the frame-error closes, and `bye{revoked}` ends it
  without a reconnect.

Both run the node as a subprocess from its own environment and skip where
`node/friday-node-linux` has none (see `nodes.tests.linux_node`)."""

import hashlib
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from nodes.conformance.cases import KEYS, NODE_ID, all_cases
from nodes.conformance.gateway import (
    ConformanceGateway,
    ExternalHook,
    SocketDriver,
    play_external,
    self_signed_context,
)
from nodes.conformance.runner import raw_frame
from nodes.reference.fixtures.factory import build_fixture_home
from nodes.tests.linux_node import (
    credential_path,
    linux_node_command,
    linux_node_env,
    write_policy,
    write_private_json,
)


def _short_dir():
    # A Unix socket path must stay under ~108 bytes.
    return Path(tempfile.mkdtemp(prefix="fnl-"))


def test_every_v1_conformance_case_passes_against_the_packaged_node():
    command = linux_node_command()
    server_ssl, _client_ssl, cert_path = self_signed_context()
    gateway = ConformanceGateway(ssl_context=server_ssl)
    hook_path = str(_short_dir() / "hook.sock")
    hook = ExternalHook(hook_path)
    node = subprocess.Popen(  # noqa: S603 - fixed argv
        [
            *command,
            "conformance",
            "--hook-socket",
            hook_path,
            "--gateway",
            f"127.0.0.1:{gateway.port}",
            "--ca",
            cert_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
    )
    cases = all_cases()[1]
    try:
        hook.wait_for_node(timeout=60)
        results = [play_external(gateway, hook, case) for case in cases]
    finally:
        hook.close()
        gateway.close()
        try:
            node.wait(30)
        except subprocess.TimeoutExpired:
            node.kill()
    failed = {result.name: result.failures for result in results if not result.passed}
    assert not failed, failed
    assert len(results) == len(cases) >= 50
    families = {case["name"] for case in cases}
    for name in (
        "v1_unknown_capability",
        "v1_args_extra_key",
        "v1_replay",
        "v1_bad_signature",
        "v1_policy_shaped_policy_set",
        "v1_class_not_read",
    ):
        assert name in families  # the compromised-server family is in the run


class _NoHook:
    """The real daemon has no conformance hook: the test only sends frames."""

    def wait_disconnected(self):
        pass


def _hostile_frames():
    """Every policy-shaped frame, then every frame of the v1 suite that is
    not a frame error (checks 1-4 would close the session) or a `bye`."""
    cases = all_cases()[1]
    shaped = [c for c in cases if c["name"].startswith("v1_policy_shaped")]
    other = [
        c
        for c in cases
        if not c["name"].startswith("v1_policy_shaped")
        and (c["check"] is None or c["check"] > 4)
        and "bye" not in c["name"]
    ]
    frames = []
    for case in [*shaped, *other]:
        for step in case["steps"]:
            if step["op"] == "send":
                frames.append((raw_frame(step), step["frame"]))
    return frames


def _fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns


def test_no_protocol_frame_changes_the_daemons_local_policy(tmp_path):
    command = linux_node_command()
    server_ssl, _client_ssl, cert_path = self_signed_context()
    gateway = ConformanceGateway(ssl_context=server_ssl)
    base = _short_dir()
    home = build_fixture_home(tmp_path / "fixture")
    env = linux_node_env(base, home)
    policy = write_policy(env, claude=True, codex=True, repos=[("acme", "~/work/acme")])
    signing = KEYS["signing"]
    write_private_json(
        credential_path(env),
        {
            "v": 1,
            "node_id": NODE_ID,
            "node_secret": "jnd_" + "c0nformanceSecretValue_x" * 2,
            "server_url": f"https://127.0.0.1:{gateway.port}",
            "gateway_path": "/ws/node/v1",
            "signing_keys": [
                {"key_id": signing["key_id"], "public_key_b64": signing["public_key_b64"]}
            ],
            "tls": {"ca_pem": Path(cert_path).read_text(), "spki_sha256": None},
            "paired_at": int(time.time()),
        },
    )
    policy_before = _fingerprint(policy)
    credential_before = _fingerprint(credential_path(env))
    daemon = subprocess.Popen(  # noqa: S603 - fixed argv
        [*command, "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    sessions = 0
    try:
        frames = _hostile_frames()
        driver = None
        while frames:
            if driver is None or not driver.open:
                driver = SocketDriver(gateway.accept(timeout=30), 1, _NoHook())
                sessions += 1
                hello = driver.frames[0]
                assert hello["type"] == "hello" and hello["v"] == 1
                assert {entry["class"] for entry in hello["catalogue"]} == {"read"}
                assert hello["node_version"].startswith("friday-node-linux ")
            raw, frame = frames.pop(0)
            driver.send(raw, frame)
        # Policy-shaped frames are answered with a refusal and change nothing.
        refusals = [f for f in driver.frames if f.get("type") == "result"]
        assert refusals
        assert _fingerprint(policy) == policy_before
        driver.send(
            '{"v": 1, "type": "bye", "id": "d828103b-d474-4f22-9827-94b6bd394839", '
            '"reason": "revoked"}'
        )
        code = daemon.wait(15)
    finally:
        gateway.close()
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(5)
    output = daemon.stdout.read().decode("utf-8", "replace")
    assert code == 0, output
    assert sessions >= 2  # the five policy-shaped frames closed one session (3 frame errors)
    assert "not reconnecting: bye:revoked" in output
    assert "c0nformanceSecretValue" not in output
    assert _fingerprint(policy) == policy_before
    assert _fingerprint(credential_path(env)) == credential_before
    # The daemon is gone: nothing listens on the hook socket any more.
    assert not (base / "xdg-run" / "friday-node" / "hook.sock").exists()


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_the_daemon_refuses_a_readable_credential(tmp_path, mode):
    command = linux_node_command()
    base = _short_dir()
    env = linux_node_env(base, tmp_path)
    path = credential_path(env)
    write_private_json(path, {"v": 1})
    os.chmod(path, mode)
    result = subprocess.run(  # noqa: S603 - fixed argv
        [*command, "run"], capture_output=True, text=True, env=env, timeout=60
    )
    assert result.returncode == 1
    assert "refusing to start" in result.stderr
