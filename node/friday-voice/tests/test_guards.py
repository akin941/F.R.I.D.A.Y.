"""Static and runtime guards (brief D1, D2, D16): no speech engine, provider
SDK or Pipecat service in the child; no listening socket, statically or at
runtime; nothing imported from backend/."""

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import PACKAGE_ROOT, REPO_ROOT, SOURCE_DIR

from friday_voice import pipecat_adapter
from friday_voice.conformance.runner import child_argv, host_credential
from friday_voice.ports import RealClock
from friday_voice.reference.host import HostCore, ProcessHost

SCANNED = [
    path
    for root in (SOURCE_DIR, PACKAGE_ROOT / "scripts")
    for path in sorted(root.rglob("*.py"))
    if "__pycache__" not in path.parts
]

# D2: speech engines, provider SDKs and Pipecat's provider services.
FORBIDDEN_IMPORTS = (
    "piper",
    "whisper",
    "faster_whisper",
    "mlx_whisper",
    "mlx",
    "ctranslate2",
    "pywhispercpp",
    "openai",
    "anthropic",
    "google.generativeai",
    "google.genai",
    "litellm",
    "kokoro",
    "pipecat.services",
)
# D1: the child opens no listening socket and speaks only stdio + outbound HTTPS.
LISTENER_IMPORTS = (
    "socket",
    "socketserver",
    "http.server",
    "uvicorn",
    "aiohttp.web",
    "websockets.server",
    "websockets.asyncio.server",
)
LISTENER_CALLS = ("bind", "listen", "start_server", "start_unix_server", "create_server", "serve")


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    yield first.value


def _hits(module, prefixes):
    return [p for p in prefixes if module == p or module.startswith(p + ".")]


def _scan(prefixes):
    found = []
    for path in SCANNED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imports(tree):
            if _hits(module, prefixes):
                found.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
    return found


def test_no_speech_engine_provider_sdk_or_pipecat_service_is_imported():
    assert _scan(FORBIDDEN_IMPORTS) == []


def test_the_adapter_surface_lists_no_pipecat_service():
    assert not any(module.startswith("pipecat.services") for module, _ in pipecat_adapter.SURFACE)


def test_no_listener_module_is_imported():
    assert _scan(LISTENER_IMPORTS) == []


def test_no_listener_call_appears():
    found = []
    for path in SCANNED:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in LISTENER_CALLS:
                    where = f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}"
                    found.append(f"{where} .{node.func.attr}")
    assert found == []


def _backend_packages():
    backend = REPO_ROOT / "backend"
    return sorted(p.name for p in backend.iterdir() if (p / "__init__.py").exists()) + ["backend"]


def test_nothing_is_imported_from_backend():
    packages = _backend_packages()
    assert {"nodes", "voice", "accounts", "ai_core", "memory", "config"} <= set(packages)
    assert _scan(tuple(packages)) == []


@pytest.mark.parametrize(
    "line",
    [
        "import piper",
        "from faster_whisper import WhisperModel",
        "from pipecat.services.openai.llm import OpenAILLMService",
        "from pipecat.services import deepgram",
        "import google.generativeai as genai",
        "importlib.import_module('openai')",
        "import mlx.core",
    ],
)
def test_the_guard_catches_what_it_claims(line):
    modules = list(_imports(ast.parse(line)))
    assert any(_hits(module, FORBIDDEN_IMPORTS) for module in modules), line


def test_the_guard_ignores_lookalikes():
    for line in ("import whisperish", "import openaiish", "from pipecat.frames import frames"):
        modules = list(_imports(ast.parse(line)))
        assert not any(_hits(module, FORBIDDEN_IMPORTS) for module in modules), line


# --- runtime: no listening socket after `friday-voice run` starts ---


def _socket_inodes(pid):
    inodes = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = fd.readlink()
        except OSError:
            continue
        text = str(target)
        if text.startswith("socket:["):
            inodes.add(text[len("socket:[") : -1])
    return inodes


def _listening_inodes():
    """Inodes of every listening socket in this network namespace."""
    listening = set()
    for name in ("tcp", "tcp6"):
        path = Path("/proc/net") / name
        if path.exists():
            for line in path.read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A":  # TCP_LISTEN
                    listening.add(fields[9])
    for name in ("udp", "udp6"):  # a bound UDP socket is a listener too
        path = Path("/proc/net") / name
        if path.exists():
            for line in path.read_text().splitlines()[1:]:
                listening.add(line.split()[9])
    unix = Path("/proc/net/unix")
    if unix.exists():
        for line in unix.read_text().splitlines()[1:]:
            fields = line.split()
            if int(fields[3], 16) & 0x10000:  # __SO_ACCEPTCON
                listening.add(fields[6])
    return listening


def listening_sockets(pid):
    return _socket_inodes(pid) & _listening_inodes()


linux_only = pytest.mark.skipif(
    not Path("/proc/net/tcp").exists(), reason="/proc/net is Linux only"
)


@linux_only
def test_the_detector_sees_a_listener():
    """Control: a process that does listen is caught by the same check."""
    script = (
        "import socket, sys, time\n"
        "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
        "print('up', flush=True); time.sleep(30)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b"up"
        assert listening_sockets(process.pid)
    finally:
        process.kill()
        process.wait()


@linux_only
def test_friday_voice_run_opens_no_listening_socket():
    core = HostCore(RealClock(), credential=host_credential())
    host = ProcessHost(core, child_argv()).start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and core.child_state is None:
            time.sleep(0.05)
        assert core.child_state == "armed_ptt"
        assert core.mic_held
        assert listening_sockets(host.process.pid) == set()
    finally:
        assert host.stop() == 0
    assert core.releases[-2:] == ["shutdown", "child:shutdown"]
    assert core.failures == [] and core.restart_at is None
