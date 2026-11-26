"""Shared fixtures: an isolated XDG layout and `$HOME`, a real temporary
home with synthetic Claude Code / Codex transcripts and a git repo, and a
throwaway self-signed TLS certificate for localhost servers."""

import datetime
import io
import ipaddress
import json
import os
import ssl
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from friday_node_linux.cli import Io
from friday_node_linux.tls import spki_sha256

NOW = 1_796_000_000  # 2026-11-29T22:13:20Z
SECRET = "jnd_" + "S3cr3tN0deValue_" * 3
FAKE_GITHUB_TOKEN = "ghp_" + "FixtureFAKE0123456789abcdefGHIJKLmn"


def iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def claude_lines(session_id, cwd, start, *, text="Fix the flaky invoice test."):
    return [
        {
            "type": "user",
            "uuid": f"{session_id}-u1",
            "sessionId": session_id,
            "cwd": cwd,
            "timestamp": iso(start),
            "message": {"role": "user", "content": text},
        },
        {
            "type": "assistant",
            "uuid": f"{session_id}-a1",
            "sessionId": session_id,
            "cwd": cwd,
            "timestamp": iso(start + 10),
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-5",
                "content": [
                    {"type": "text", "text": "Looking at it."},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x"}},
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
            "costUSD": 0.001,
        },
        {
            "type": "user",
            "uuid": f"{session_id}-u2",
            "sessionId": session_id,
            "cwd": cwd,
            "timestamp": iso(start + 12),
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "FILE-CONTENT-PHRASE"}
                ],
            },
        },
    ]


def write_jsonl(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def make_home(base: Path) -> Path:
    """A real-looking `$HOME`: two Claude projects, one Codex thread, a
    git repo, and things outside the granted roots."""
    home = base / "home"
    projects = home / ".claude" / "projects"
    write_jsonl(
        projects / "-home-work-acme" / "cc-acme-1.jsonl",
        claude_lines(
            "cc-acme-1",
            str(home / "work" / "acme"),
            NOW - 600,
            text=f"Rotate the acme token {FAKE_GITHUB_TOKEN} please.",
        ),
    )
    write_jsonl(
        projects / "-home-work-acme-secret" / "cc-secret-1.jsonl",
        claude_lines("cc-secret-1", str(home / "work" / "acme-secret"), NOW - 300),
    )
    thread = home / ".codex" / "sessions" / "2026" / "11" / "29" / "rollout-cx-1.jsonl"
    write_jsonl(
        thread,
        [
            {
                "timestamp": iso(NOW - 900),
                "type": "session_meta",
                "payload": {"id": "cx-acme-1", "cwd": str(home / "work" / "acme")},
            },
            {
                "timestamp": iso(NOW - 890),
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Bump the acme client."}],
                },
            },
        ],
    )
    # Outside every granted root: a transcript-shaped file the readers must
    # never reach through a symlink.
    outside = base / "outside"
    write_jsonl(
        outside / "cc-escape-1.jsonl",
        claude_lines("cc-escape-1", str(home / "work" / "acme"), NOW - 100, text="ESCAPE-PHRASE"),
    )
    repo = home / "work" / "acme"
    repo.mkdir(parents=True, exist_ok=True)
    git_env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(repo),
    }
    for argv in (
        ["git", "-c", "init.defaultBranch=main", "init", "-q"],
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(argv, cwd=repo, env=git_env, check=True, capture_output=True)
    return Path(os.path.realpath(home))


@pytest.fixture
def home(tmp_path):
    return make_home(tmp_path)


@pytest.fixture
def env(tmp_path, home):
    """XDG dirs under the test's tmp path (never the real ones)."""
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_RUNTIME_DIR": str(tmp_path / "xdg-run"),
    }


class Tty(io.StringIO):
    pass


@pytest.fixture
def make_io(env, home):
    def make(answer="", tty=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        terminal = Io(
            stdin=io.StringIO(answer),
            stdout=stdout,
            stderr=stderr,
            is_tty=lambda: tty,
            env=env,
            home=home,
        )
        return SimpleNamespace(io=terminal, stdout=stdout, stderr=stderr)

    return make


def self_signed(directory: Path, *, host="127.0.0.1"):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "friday-node-linux-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(host)), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return SimpleNamespace(
        cert_path=cert_path,
        key_path=key_path,
        pem=cert_path.read_text(encoding="ascii"),
        server_context=server,
        spki=spki_sha256(cert.public_bytes(serialization.Encoding.DER)),
    )


@pytest.fixture
def tls(tmp_path):
    return self_signed(tmp_path / "tls")


@pytest.fixture
def other_tls(tmp_path):
    return self_signed(tmp_path / "tls-other")
