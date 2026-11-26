"""Helpers for the tests that run the packaged Linux node
(`node/friday-node-linux`, Phase 13 brief D10) as a separate process.

The package is its own uv project and imports nothing from `backend/`, so
these tests never import it: they launch its console script from its own
environment. Where that environment does not exist (`uv sync --frozen` was
not run in `node/friday-node-linux`) they skip, unless
`FRIDAY_LINUX_NODE=1` demands it (the CI `linux-node` job)."""

import json
import os
import shutil
from pathlib import Path

import pytest

LINUX_NODE = Path(__file__).resolve().parents[3] / "node" / "friday-node-linux"


def linux_node_command() -> list:
    uv = shutil.which("uv")
    ready = uv is not None and (LINUX_NODE / ".venv").is_dir()
    if not ready:
        reason = "node/friday-node-linux has no environment (run `uv sync --frozen` there)"
        if os.environ.get("FRIDAY_LINUX_NODE") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    return [uv, "run", "--frozen", "--no-sync", "--directory", str(LINUX_NODE), "friday-node-linux"]


def linux_node_env(base: Path, home: Path) -> dict:
    """An isolated `$HOME` and XDG layout for one node process."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("VIRTUAL_ENV", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR")
    }
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(base / "xdg-config"),
            "XDG_STATE_HOME": str(base / "xdg-state"),
            "XDG_RUNTIME_DIR": str(base / "xdg-run"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def write_private_json(path: Path, body) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(body), encoding="utf-8")
    os.chmod(path, 0o600)


def write_policy(env: dict, *, claude=False, codex=False, repos=()) -> Path:
    """The node's `policy.json`, written directly (the test is the local
    owner; the interactive CLI is tested in the package)."""
    path = Path(env["XDG_CONFIG_HOME"]) / "friday-node" / "policy.json"
    write_private_json(
        path,
        {
            "v": 1,
            "grants": {"claude": claude, "codex": codex},
            "repos": [{"name": name, "root": root} for name, root in repos],
        },
    )
    return path


def credential_path(env: dict) -> Path:
    return Path(env["XDG_STATE_HOME"]) / "friday-node" / "credential"
