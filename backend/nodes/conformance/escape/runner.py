"""The escape-suite runner, its fake sandbox and its static check (Phase 10
brief D11; `manage.py node_escape_suite`).

`run_suite(executor_kind, base_dir)` builds a fixture home under `base_dir`
(never the real home: every planted canary and symlink lives there or under
a per-run `/tmp` name the runner removes), then for every case:

1. plants what the case needs (canary files in denied directories, a
   symlink in `/tmp`, a listening Unix socket at the node's hook-socket
   path; a loopback listener is shared by the suite);
2. creates a throwaway worktree of the fixture repo and generates the
   sandbox for the case's script (`build_sandbox`, worktree mode, a planted
   `GITHUB_TOKEN` in the node's environment);
3. runs the **static check** over the generated policy;
4. runs the script through the executor, feeding its output through the
   node's `OutputPipeline` (1 MiB cap) and enforcing the wall time;
5. decides the verdict, removes the worktree and checks `git worktree
   list` is unchanged, and removes the plants.

`check_policy_document(case, document, context)` is the static check over an
exported sandbox document (`SandboxSpec.document()` shape), which is what
`node_escape_suite --check-policy <file>` runs over documents the Swift
test target exports (Mac session M2).
"""

import os
import shutil
import socket
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from nodes.conformance.escape import MARKER, load_cases
from nodes.reference.confirm import FakeClock
from nodes.reference.executor import CommandOutcome, SrtExecutor
from nodes.reference.fixtures.factory import build_fixture_home
from nodes.reference.local_policy import CommandMapping
from nodes.reference.sandbox import build_sandbox
from nodes.reference.stream_scrub import MAX_OUTPUT_BYTES, OutputPipeline
from nodes.reference.worktree import create_worktree, list_worktrees, remove_worktree

DEFAULT_RUNTIME_SECONDS = 30
SCRIPT_PATH = "/usr/bin:/bin"


@dataclass
class CaseVerdict:
    id: str
    category: str
    static_ok: bool
    contained: bool
    reasons: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.static_ok and self.contained


@dataclass
class SuiteReport:
    executor: str
    verdicts: list

    @property
    def passed(self) -> bool:
        return bool(self.verdicts) and all(v.passed for v in self.verdicts)

    def table(self) -> str:
        lines = [f"escape suite, executor={self.executor}"]
        for verdict in self.verdicts:
            mark = "CONTAINED" if verdict.passed else "ESCAPED/UNCOVERED"
            lines.append(f"  {verdict.id.ljust(32)} {verdict.category.ljust(14)} {mark}")
            lines.extend(f"      - {reason}" for reason in verdict.reasons)
        ok = sum(1 for v in self.verdicts if v.passed)
        lines.append(f"{ok}/{len(self.verdicts)} contained")
        lines.append("ALL CONTAINED" if self.passed else "FAILED")
        return "\n".join(lines)


def expand(value, context):
    if isinstance(value, str):
        for key, replacement in context.items():
            value = value.replace("{" + key + "}", str(replacement))
        return value
    if isinstance(value, dict):
        return {k: expand(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, context) for v in value]
    return value


def _under(path, root):
    path = os.path.normpath(path)
    root = os.path.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


# --- the static check (the fake sandbox) --------------------------------


def check_policy_document(case, document, context) -> list:
    """The reasons the policy does not cover the case (empty = covered).
    `document` is `SandboxSpec.document()`; `context` the placeholders."""
    attempt = expand(case["attempt"], context)
    settings = document["settings"]
    filesystem = settings["filesystem"]
    network = settings["network"]
    allow_write = filesystem.get("allowWrite", [])
    deny_write = filesystem.get("denyWrite", [])
    deny_read = filesystem.get("denyRead", [])
    reasons = []
    for root in allow_write:
        if any(_under(tmp, root) for tmp in ("/tmp", "/private/tmp")):
            reasons.append("/tmp itself is writable")
    if document.get("mode") != "worktree":
        reasons.append("the escape suite runs in worktree mode")
    kind = attempt["kind"]
    if kind == "write":
        target = attempt["path"]
        writable = any(_under(target, root) for root in allow_write)
        denied = any(_under(target, root) for root in deny_write)
        if writable and not denied:
            reasons.append(f"write target is writable: {case['id']}")
    elif kind == "read":
        if not any(_under(attempt["path"], root) for root in deny_read):
            reasons.append(f"read path is not denied: {case['id']}")
    elif kind == "network":
        if network.get("allowedDomains"):
            reasons.append("the network allows domains")
        if network.get("allowLocalBinding"):
            reasons.append("local binding is allowed")
        if attempt["host"] in network.get("allowedDomains", []):
            reasons.append("the target host is allowed")
    elif kind == "unix_socket":
        if network.get("allowUnixSockets"):
            reasons.append("Unix sockets are allowed")
        if not any(_under(attempt["path"], root) for root in deny_read):
            reasons.append("the hook socket directory is not denied")
    elif kind == "env":
        if attempt["name"] in document["process"]["env"]:
            reasons.append(f"{attempt['name']} reaches the sandbox")
    elif kind == "limit":
        limit = document["limits"].get(attempt["limit"])
        if limit is None or limit >= int(attempt["want"]):
            reasons.append(f"limit {attempt['limit']} does not hold")
    else:
        reasons.append(f"unknown attempt kind {kind!r}")
    return reasons


class FakeSandboxExecutor:
    """The CI executor: a model of the generated policy. For the running
    case it prints the marker exactly when the static model says the
    attempt would succeed, produces the requested output volume for the
    output case and never finishes for the runtime case, so the runner's
    caps and verdict logic are exercised end to end."""

    def __init__(self, clock):
        self.clock = clock
        self.case = None
        self.context = None
        self.calls = []

    def prepare(self, case, context):
        self.case, self.context = case, context

    def start(self, spec):
        self.calls.append({"case": self.case["id"], "argv": list(spec.argv)})
        reasons = check_policy_document(self.case, spec.document(), self.context)
        attempt = self.case["attempt"]
        chunks, hang = [], False
        if attempt["kind"] == "limit" and attempt["limit"] == "max_output_bytes":
            remaining = int(attempt["want"])
            while remaining > 0:
                size = min(remaining, 65536)
                chunks.append(("stdout", b"y" * size))
                remaining -= size
        elif attempt["kind"] == "limit" and attempt["limit"] == "wall_seconds":
            hang = not reasons
        if reasons and not hang:
            chunks.append(("stdout", (MARKER + "\n").encode()))
        return _FakeRun(chunks, hang)


class _FakeRun:
    def __init__(self, chunks, hang):
        self._chunks = list(chunks)
        self._hang = hang
        self.terminated = False

    def read(self):
        chunks, self._chunks = self._chunks, []
        return chunks

    def poll(self):
        if self.terminated:
            return CommandOutcome(exit_code=-15)
        if self._hang:
            return None
        return CommandOutcome(exit_code=0)

    def terminate(self):
        self.terminated = True


# --- plants -------------------------------------------------------------


class _Plants:
    def __init__(self):
        self.paths = []
        self.sockets = []

    def apply(self, plant, context):
        if not plant:
            return
        plant = expand(plant, context)
        if "canary" in plant:
            path = Path(plant["canary"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(context["canary"] + "\n", encoding="utf-8")
            self.paths.append(str(path))
        if "symlink" in plant:
            if os.path.lexists(plant["symlink"]):
                os.unlink(plant["symlink"])
            os.symlink(plant["to"], plant["symlink"])
            self.paths.append(plant["symlink"])
        if "unix_socket" in plant:
            path = plant["unix_socket"]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(path):
                os.unlink(path)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(path)
            listener.listen(1)
            self.sockets.append(listener)
            self.paths.append(path)

    def remove(self):
        for listener in self.sockets:
            listener.close()
        for path in self.paths:
            try:
                if os.path.islink(path) or os.path.isfile(path) or os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass
        self.paths, self.sockets = [], []


# --- the run ------------------------------------------------------------


def _run(executor, spec, clock, *, real):
    pipeline = OutputPipeline()
    pieces = []
    now = time.monotonic if real else clock.now
    running = executor.start(spec)
    started = now()
    outcome = None
    timed_out = False
    while True:
        for stream, data in running.read():
            pieces += pipeline.feed(stream, data)
        outcome = running.poll()
        if outcome is not None:
            for stream, data in running.read():
                pieces += pipeline.feed(stream, data)
            break
        if now() - started >= spec.limits["wall_seconds"]:
            running.terminate()
            timed_out = True
            break
        if real:
            time.sleep(0.01)
        else:
            clock.advance(1)
    pieces += pipeline.finish()
    text = "".join(piece.data for piece in pieces)
    emitted = sum(len(piece.data.encode("utf-8")) for piece in pieces)
    return text, emitted, pipeline, timed_out


def _verdict(case, context, text, emitted, pipeline, timed_out):
    reasons = []
    if MARKER in text:
        reasons.append("the script reported an escape")
    attempt = expand(case["attempt"], context)
    if attempt["kind"] == "write" and attempt["path"] != context["worktree"] + "/.git":
        if os.path.lexists(attempt["path"]):
            reasons.append("a write target outside the worktree exists")
            try:
                os.unlink(attempt["path"])
            except OSError:
                pass
    if emitted > MAX_OUTPUT_BYTES:
        reasons.append("output passed the 1 MiB cap")
    if attempt["kind"] == "limit" and attempt["limit"] == "max_output_bytes":
        if not pipeline.truncated:
            reasons.append("runaway output was not truncated")
    if attempt["kind"] == "limit" and attempt["limit"] == "wall_seconds" and not timed_out:
        reasons.append("the runtime cap did not stop the command")
    return reasons


def run_suite(executor_kind="fake", *, base_dir=None, cases=None, executor=None) -> SuiteReport:
    """Play the escape suite. `executor_kind` is `fake` or `srt`; tests may
    pass their own `executor` (with an optional `prepare(case, context)`)."""
    cases = load_cases() if cases is None else cases
    base = Path(base_dir or tempfile.mkdtemp(prefix="friday-escape-"))
    home = build_fixture_home(base)
    repo = str(home / "work" / "acme")
    worktree_base = base / "worktrees"
    worktree_base.mkdir(exist_ok=True)
    real = executor_kind == "srt"
    clock = time.monotonic if real else FakeClock(0)
    if executor is None:
        executor = SrtExecutor() if real else FakeSandboxExecutor(clock)
    loopback = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    loopback.bind(("127.0.0.1", 0))
    loopback.listen(8)
    verdicts = []
    try:
        before = list_worktrees(repo)
        for case in cases:
            verdicts.append(
                _play_case(case, executor, clock, real, home, repo, worktree_base, loopback, before)
            )
    finally:
        loopback.close()
    return SuiteReport(executor_kind, verdicts)


def _play_case(case, executor, clock, real, home, repo, worktree_base, loopback, before):
    run = uuid.uuid4().hex[:8]
    worktree = create_worktree(repo, str(worktree_base))
    context = {
        "home": str(home),
        "repo": repo,
        "repo_parent": os.path.dirname(repo),
        "worktree": worktree,
        "run": run,
        "canary": f"FRIDAY-CANARY-{run}",
        "loopback_port": loopback.getsockname()[1],
        "hook_socket": str(home / ".friday-node" / f"hook-{run}.sock"),
    }
    plants = _Plants()
    temp_dir = tempfile.mkdtemp(prefix="friday-escape-run-")
    try:
        plants.apply(case.get("plant"), context)
        mapping = CommandMapping(
            kind="test",
            argv=("/bin/sh", "-c", expand(case["script"], context)),
            path=SCRIPT_PATH,
            env_names=("GITHUB_TOKEN",),
            max_runtime_seconds=case.get("runtime_seconds", DEFAULT_RUNTIME_SECONDS),
            mode="worktree",
        )
        spec = build_sandbox(
            mapping,
            home=str(home),
            repo_root=repo,
            write_root=worktree,
            temp_dir=os.path.realpath(temp_dir),
            node_env={"GITHUB_TOKEN": context["canary"], "PATH": "/usr/bin:/bin"},
        )
        static = check_policy_document(case, spec.document(), context)
        if hasattr(executor, "prepare"):
            executor.prepare(case, context)
        text, emitted, pipeline, timed_out = _run(executor, spec, clock, real=real)
        reasons = _verdict(case, context, text, emitted, pipeline, timed_out)
    finally:
        remove_worktree(repo, worktree)
        plants.remove()
        shutil.rmtree(temp_dir, ignore_errors=True)
    if os.path.exists(worktree):
        reasons.append("the worktree was not deleted")
    if list_worktrees(repo) != before:
        reasons.append("git worktree list changed")
    return CaseVerdict(
        id=case["id"],
        category=case["category"],
        static_ok=not static,
        contained=not reasons,
        reasons=[f"static: {r}" for r in static] + reasons,
    )


def export_policies(base_dir=None) -> dict:
    """The reference generator's sandbox document for every case, with its
    context: the `--check-policy` file format (the Swift test target
    exports the same shape from its own `SandboxPolicy`)."""
    base = Path(base_dir or tempfile.mkdtemp(prefix="friday-escape-export-"))
    home = base / "home"
    context = {
        "home": str(home),
        "repo": str(home / "work" / "acme"),
        "repo_parent": str(home / "work"),
        "worktree": str(base / "worktrees" / "wt-export"),
        "run": "export",
        "canary": "FRIDAY-CANARY-export",
        "loopback_port": 9,
        "hook_socket": str(home / ".friday-node" / "hook-export.sock"),
    }
    documents = {}
    for case in load_cases():
        mapping = CommandMapping(
            kind="test",
            argv=("/bin/sh", "-c", expand(case["script"], context)),
            path=SCRIPT_PATH,
            env_names=("GITHUB_TOKEN",),
            max_runtime_seconds=case.get("runtime_seconds", DEFAULT_RUNTIME_SECONDS),
            mode="worktree",
        )
        spec = build_sandbox(
            mapping,
            home=context["home"],
            repo_root=context["repo"],
            write_root=context["worktree"],
            temp_dir=str(base / "run-tmp"),
            node_env={"GITHUB_TOKEN": context["canary"]},
        )
        documents[case["id"]] = spec.document()
    return {"context": context, "documents": documents}


def check_policy_file(document) -> list:
    """`--check-policy`: (case id, reasons) for every case of the suite;
    a case missing from the file is uncovered."""
    context = document["context"]
    results = []
    for case in load_cases():
        exported = document["documents"].get(case["id"])
        if exported is None:
            results.append((case["id"], ["no exported policy for this case"]))
            continue
        results.append((case["id"], check_policy_document(case, exported, context)))
    return results
