"""The executor port (Phase 10 brief D4, D5 check 24, D11).

The reference node never runs anything itself: after the owner's click and
check 24, it hands a `SandboxSpec` (commands) or an `ActionRequest` (open,
focus, recipes, AX) to an executor:

- `FakeExecutor` records every call (argv, env, settings document, cwd,
  limits, mode; or the action) and plays scripted output and exit codes on
  the node clock. The conformance and escape suites run against it, and
  "the executor is never called on any refusal path" is asserted on it.
- `SrtExecutor` (Linux, opt-in) runs the argv under Anthropic's
  sandbox-runtime (`srt`) with the generated settings document, the
  stripped environment and the limits; it is used only by the `-m srt`
  tests and `node_escape_suite --executor srt`, and is never the ADR-014
  evidence (the Mac sandbox is Seatbelt, Mac session M3).

A running command exposes `read()` (pending output chunks), `poll()` (the
outcome once finished) and `terminate()` (SIGTERM, then SIGKILL after the
spec's grace): the node calls `terminate()` on socket loss and at the
runtime cap, and sends no late result.
"""

import base64
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

from nodes.reference.sandbox import SandboxSpec

SRT_ARGV_TEMPLATE = ("{srt}", "--settings", "{settings}", "--")


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int | None
    sandbox_violation: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ActionRequest:
    capability: str
    args: dict
    bundle_id: str | None = None
    recipe: str | None = None


@dataclass(frozen=True)
class ActionOutcome:
    ok: bool
    error: str | None = None


@dataclass
class FakeCommandScript:
    """What a fake command does: output `chunks` (`(stream, data, at)` with
    `data` as `str` or `bytes`, `at` seconds after start), then exit with
    `exit_code` after `duration` seconds; `hang` never exits on its own."""

    chunks: list = field(default_factory=list)
    exit_code: int = 0
    duration: int = 0
    hang: bool = False
    sandbox_violation: bool = False
    fail_to_start: bool = False

    @classmethod
    def from_document(cls, body):
        chunks = []
        for chunk in body.get("chunks", ()):
            stream, data = chunk[0], chunk[1]
            at = chunk[2] if len(chunk) > 2 else 0
            if isinstance(data, dict) and "b64" in data:
                data = base64.b64decode(data["b64"])
            chunks.append((stream, data, at))
        return cls(
            chunks=chunks,
            exit_code=body.get("exit_code", 0),
            duration=body.get("duration", 0),
            hang=body.get("hang", False),
            sandbox_violation=body.get("sandbox_violation", False),
            fail_to_start=body.get("fail_to_start", False),
        )


class FakeRunning:
    def __init__(self, executor, script: FakeCommandScript, record):
        self._executor = executor
        self._script = script
        self._record = record
        self._started = executor.clock.now()
        self._pending = list(script.chunks)
        self._done = False
        self.terminated = False

    def _elapsed(self):
        return self._executor.clock.now() - self._started

    def read(self):
        if self.terminated:
            return []
        elapsed = self._elapsed()
        ready = [c for c in self._pending if c[2] <= elapsed]
        self._pending = [c for c in self._pending if c[2] > elapsed]
        return [
            (stream, data.encode("utf-8") if isinstance(data, str) else bytes(data))
            for stream, data, _ in ready
        ]

    def poll(self):
        if self.terminated:
            return CommandOutcome(exit_code=-int(signal.SIGTERM))
        if self._script.hang or self._pending or self._elapsed() < self._script.duration:
            return None
        self._done = True
        return CommandOutcome(self._script.exit_code, self._script.sandbox_violation)

    def terminate(self):
        if self._done or self.terminated:
            return
        self.terminated = True
        self._record["terminated"] = True
        self._record["signals"] = ["SIGTERM", "SIGKILL"]
        self._executor.events.append(("terminate", self._record["call"]))


class FakeExecutor:
    """Records calls; plays `FakeCommandScript`s (one per started command,
    in order; a default script when the queue is empty)."""

    def __init__(self, clock, *, scripts=None, default=None, action_ok=True):
        self.clock = clock
        self.scripts = list(scripts or [])
        self.default = default or FakeCommandScript(chunks=[("stdout", "ok\n", 0)])
        self.action_ok = action_ok
        self.calls: list = []
        self.events: list = []
        self.running: list = []

    def start(self, spec: SandboxSpec):
        script = self.scripts.pop(0) if self.scripts else self.default
        record = {
            "call": len(self.calls),
            "kind": "command",
            "argv": list(spec.argv),
            "cwd": spec.cwd,
            "env": dict(spec.env),
            "settings": spec.settings,
            "limits": dict(spec.limits),
            "mode": spec.mode,
            "terminated": False,
        }
        self.calls.append(record)
        if script.fail_to_start:
            raise OSError("scripted start failure")
        running = FakeRunning(self, script, record)
        self.running.append(running)
        return running

    def perform(self, action: ActionRequest) -> ActionOutcome:
        self.calls.append(
            {
                "call": len(self.calls),
                "kind": "action",
                "capability": action.capability,
                "args": dict(action.args),
                "bundle_id": action.bundle_id,
                "recipe": action.recipe,
            }
        )
        return ActionOutcome(self.action_ok, None if self.action_ok else "scripted_failure")


def _set_limits(limits):
    import resource

    cpu = int(limits["cpu_seconds"])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    nproc = int(limits["max_processes"])
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        cap = nproc if hard == resource.RLIM_INFINITY else min(nproc, hard)
        resource.setrlimit(resource.RLIMIT_NPROC, (cap, cap))
    except (ValueError, OSError):  # pragma: no cover - platform dependent
        pass


class SrtRunning:
    def __init__(self, process, grace, settings_dir, clock):
        self._process = process
        self._grace = grace
        self._settings_dir = settings_dir
        self._queue = queue.Queue()
        self._readers = [
            threading.Thread(target=self._pump, args=(name, pipe), daemon=True)
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        for reader in self._readers:
            reader.start()
        self.terminated = False

    def _pump(self, stream, pipe):
        for block in iter(lambda: pipe.read1(4096), b""):
            self._queue.put((stream, block))
        pipe.close()

    def read(self):
        chunks = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except queue.Empty:
                return chunks

    def poll(self):
        code = self._process.poll()
        if code is None or any(reader.is_alive() for reader in self._readers):
            return None
        shutil.rmtree(self._settings_dir, ignore_errors=True)
        return CommandOutcome(exit_code=max(min(code, 255), -255))

    def terminate(self):
        if self._process.poll() is not None:
            return
        self.terminated = True
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self._process.wait(timeout=self._grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process.wait(timeout=5)
        shutil.rmtree(self._settings_dir, ignore_errors=True)


class SrtExecutor:
    """Runs commands under `srt` on Linux (opt-in; bubblewrap underneath).

    `srt_path` defaults to `srt` on PATH. The settings document is written
    to a private directory outside every writable root. The invocation is
    `SRT_ARGV_TEMPLATE` + the mapped argv; the exact `srt` flags are
    verified against the pinned version on the Mac (owner step)."""

    def __init__(self, srt_path=None, *, clock=None, argv_template=SRT_ARGV_TEMPLATE):
        self.srt_path = srt_path or shutil.which("srt")
        self.clock = clock
        self.argv_template = tuple(argv_template)
        self.calls: list = []

    @classmethod
    def available(cls) -> bool:
        return shutil.which("srt") is not None

    def start(self, spec: SandboxSpec):
        if not self.srt_path:
            raise OSError("srt is not installed")
        settings_dir = tempfile.mkdtemp(prefix="friday-srt-")
        os.chmod(settings_dir, 0o700)
        settings_path = os.path.join(settings_dir, "settings.json")
        with open(settings_path, "wb") as handle:
            handle.write(spec.settings_bytes())
        prefix = [
            part.replace("{srt}", self.srt_path).replace("{settings}", settings_path)
            for part in self.argv_template
        ]
        argv = prefix + list(spec.argv)
        self.calls.append({"argv": argv, "cwd": spec.cwd, "env": dict(spec.env)})
        process = subprocess.Popen(  # noqa: S603 - argv list, no shell
            argv,
            cwd=spec.cwd,
            env=dict(spec.env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=lambda: _set_limits(spec.limits),  # noqa: PLW1509
            close_fds=True,
        )
        return SrtRunning(process, spec.limits["kill_grace_seconds"], settings_dir, self.clock)

    def perform(self, action: ActionRequest) -> ActionOutcome:
        # Open/focus, recipes and AX need macOS (Mac session M2).
        return ActionOutcome(False, "unsupported_on_linux")


def wait_until_finished(running, *, timeout=30.0, interval=0.01):
    """Test helper: poll a real running command until it finishes, returning
    (chunks, outcome)."""
    chunks = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunks.extend(running.read())
        outcome = running.poll()
        if outcome is not None:
            chunks.extend(running.read())
            return chunks, outcome
        time.sleep(interval)
    running.terminate()
    raise TimeoutError("command did not finish")
