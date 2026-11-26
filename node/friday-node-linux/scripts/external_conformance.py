"""Run the node conformance suite against the packaged Linux node over a
real localhost TLS socket (Phase 13 brief D10; CI job `linux-node`,
`make linux-node-conformance`).

1. starts the backend's hostile fake gateway:
   `manage.py node_conformance_server --external --tls-self-signed
   --protocol 1 --port <free> --hook-socket <tmp>` (from `backend/`, in the
   backend's own environment, `uv run --directory backend`);
2. reads the self-signed certificate path it prints;
3. starts `friday-node-linux conformance --hook-socket <tmp> --gateway
   127.0.0.1:<port> --ca <cert>` in this project's environment;
4. waits for both and exits with the gateway's status (non-zero unless
   every v1 case passed), printing its pass/fail table.

No database, no real server, no network beyond localhost. The backend
environment needs `DJANGO_SETTINGS_MODULE` importable (the dev settings by
default; `DATABASE_URL` / `REDIS_URL` are read but never connected).
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BACKEND = PACKAGE_ROOT.parents[1] / "backend"
CERT_LINE = re.compile(r"TLS certificate \(pin it in the conformance build\): (\S+)")
TIMEOUT_SECONDS = 600


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv is required", file=sys.stderr)
        return 2
    port = free_port()
    hook = Path(tempfile.mkdtemp(prefix="friday-linux-conformance-")) / "hook.sock"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    gateway = subprocess.Popen(  # noqa: S603 - fixed argv
        [
            uv,
            "run",
            "--frozen",
            "--directory",
            str(BACKEND),
            "python",
            "-u",
            "manage.py",
            "node_conformance_server",
            "--external",
            "--tls-self-signed",
            "--protocol",
            "1",
            "--port",
            str(port),
            "--hook-socket",
            str(hook),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    lines: list = []
    cert = {}
    ready = threading.Event()

    def pump():
        for line in gateway.stdout:
            lines.append(line)
            match = CERT_LINE.search(line)
            if match:
                cert["path"] = match.group(1)
            if "waiting for the node" in line:
                ready.set()
        ready.set()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    if not ready.wait(120) or "path" not in cert or gateway.poll() is not None:
        gateway.kill()
        print("".join(lines), file=sys.stderr)
        print("the fake gateway did not start", file=sys.stderr)
        return 1
    deadline = time.monotonic() + 30
    while not hook.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    node = subprocess.Popen(  # noqa: S603 - fixed argv
        [
            uv,
            "run",
            "--frozen",
            "--directory",
            str(PACKAGE_ROOT),
            "friday-node-linux",
            "conformance",
            "--hook-socket",
            str(hook),
            "--gateway",
            f"127.0.0.1:{port}",
            "--ca",
            cert["path"],
        ],
        env=env,
    )
    try:
        status = gateway.wait(TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        gateway.kill()
        status = 1
    reader.join(5)
    try:
        node.wait(30)
    except subprocess.TimeoutExpired:
        node.kill()
    print("".join(lines))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
