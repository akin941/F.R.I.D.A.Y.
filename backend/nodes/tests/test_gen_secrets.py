"""`deploy/docker/gen-secrets.sh` writes a valid NODE_SIGNING_KEY (Phase 9
brief D4) and never overwrites an existing one."""

import subprocess
from pathlib import Path

import pytest

from nodes.protocol.signing import SigningKey

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "docker" / "gen-secrets.sh"


def _run(env_file):
    return subprocess.run(
        ["sh", str(SCRIPT), str(env_file)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def _values(env_file):
    return dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if line and not line.startswith("#")
    )


@pytest.mark.skipif(not SCRIPT.exists(), reason="deploy scripts not present")
def test_gen_secrets_writes_a_valid_node_signing_key_once(tmp_path):
    env_file = tmp_path / ".env"
    completed = _run(env_file)
    key = _values(env_file)["NODE_SIGNING_KEY"]
    assert SigningKey.from_b64(key).key_id  # a valid Ed25519 raw private key
    assert key not in completed.stdout  # the script names keys, never values
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    _run(env_file)
    assert _values(env_file)["NODE_SIGNING_KEY"] == key  # never overwritten
    assert env_file.read_text().count("NODE_SIGNING_KEY=") == 1
