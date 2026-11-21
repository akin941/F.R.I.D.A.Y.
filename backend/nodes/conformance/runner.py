"""Play conformance cases against a node (Phase 9 brief D5, Phase 10 brief
branch 2).

`run_case(case, driver)` plays one case document (`nodes.conformance.cases`)
through a *driver*:

- `InProcessDriver` calls a `ReferenceNode` directly;
- `nodes.conformance.gateway.SocketDriver` plays the same steps over a
  localhost `wsproto` WebSocket as the hostile fake gateway, with the
  conformance hook (`nodes.conformance.hook`) standing in for the owner and
  the clock.

The fake gateway's own side is `GatewayChecker`: every node frame is
validated against the session version's schema, and an `output` or
`confirmation` frame (or a `result`) for an invocation that is not in flight
is counted as a frame error. Heartbeats are answered with `heartbeat.ack`
(unless the case is `silent`) and are not part of a case's expected frames.
"""

import base64
import json
from dataclasses import dataclass, field

from nodes.capabilities import get_action_contract
from nodes.conformance.cases import NODE_ID, PINNED
from nodes.protocol.messages import ProtocolError, validate
from nodes.reference.confirm import ConfirmationWindowDouble, Decision, FakeClock
from nodes.reference.executor import FakeCommandScript, FakeExecutor
from nodes.reference.fixtures.factory import fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.reference.worktree import list_worktrees

ADVANCE_STEP_SECONDS = 5


def narrow_catalogue(availability=None):
    """The `narrow` catalogue: `repo.command.run` advertises a narrower
    `command_kind` enum than the server contract (no `build`), so args valid
    for the server are outside the node's own schema (check 20)."""
    entries = default_catalogue(availability)
    contract = get_action_contract("repo.command.run")
    for entry in entries:
        if entry["name"] == contract.name:
            kinds = entry["args_schema"]["properties"]["command_kind"]["enum"]
            entry["args_schema"]["properties"]["command_kind"]["enum"] = [
                kind for kind in kinds if kind != "build"
            ]
    return entries


def build_reference_node(case, *, home, worktree_base=None):
    """A fresh reference node configured by `case["setup"]` over the fixture
    home. Returns the node (its clock, window and executor are attributes)."""
    setup = case.get("setup", {})
    clock = FakeClock(setup.get("now"))
    window = ConfirmationWindowDouble(
        clock, [Decision.from_document(d) for d in setup.get("window", ())]
    )
    executor = FakeExecutor(
        clock, scripts=[FakeCommandScript.from_document(s) for s in setup.get("executor", ())]
    )
    availability = setup.get("availability")
    if setup.get("catalogue") == "narrow":
        catalogue = narrow_catalogue(availability)
    else:
        catalogue = default_catalogue(availability)
    return ReferenceNode(
        node_id=NODE_ID,
        pinned=dict(PINNED),
        policy=fixture_policy(home),
        executor=executor,
        clock=clock,
        window=window,
        catalogue=catalogue,
        read_delay_seconds=setup.get("read_delay", 0),
        worktree_base=str(worktree_base) if worktree_base else None,
        node_env={"PYTHONHASHSEED": "0", "GITHUB_TOKEN": "ghp_" + "x" * 36},
    )


@dataclass
class GatewayChecker:
    """The fake gateway's view of the node's frames."""

    version: int
    in_flight: set = field(default_factory=set)
    frame_errors: int = 0
    errors: list = field(default_factory=list)

    def server_sent(self, frame):
        if isinstance(frame, dict) and frame.get("type") == "invoke":
            invocation = frame.get("invocation_id")
            if isinstance(invocation, str):
                self.in_flight.add(invocation)

    def node_sent(self, frame) -> None:
        try:
            validate(frame, "node_to_server", self.version)
        except ProtocolError:
            self.frame_errors += 1
            self.errors.append("schema")
            return
        kind = frame["type"]
        invocation = frame.get("invocation_id")
        if kind in ("output", "confirmation"):
            if invocation not in self.in_flight:
                self.frame_errors += 1
                self.errors.append(f"{kind}_not_in_flight")
        elif kind == "result" and invocation is not None:
            if invocation not in self.in_flight:
                self.frame_errors += 1
                self.errors.append("result_not_in_flight")
            else:
                self.in_flight.discard(invocation)


class InProcessDriver:
    """Drives a ReferenceNode in process."""

    def __init__(self, node, version):
        self.node = node
        self.version = version
        self.frames: list = []
        self.silent = False
        self.checker = GatewayChecker(version)
        self._handle(node.connect(version))

    def _handle(self, frames):
        for frame in frames:
            self.checker.node_sent(frame)
            self.frames.append(frame)
            if frame["type"] == "heartbeat" and not self.silent:
                ack = {
                    "v": self.version,
                    "type": "heartbeat.ack",
                    "id": f"00000000-0000-4000-8000-{frame['seq']:012d}",
                    "seq": frame["seq"],
                    "server_time": self.node.clock.now(),
                }
                self._handle(self.node.receive(json.dumps(ack)))

    def send(self, raw, frame=None):
        if frame is not None:
            self.checker.server_sent(frame)
        self._handle(self.node.receive(raw))
        self._handle(self.node.tick())

    def advance(self, seconds):
        while seconds > 0:
            step = min(seconds, ADVANCE_STEP_SECONDS)
            self.node.clock.advance(step)
            seconds -= step
            self._handle(self.node.tick())

    def tick(self):
        self._handle(self.node.tick())

    def disconnect(self):
        self.node.disconnect()

    def kill_switch(self):
        self._handle(self.node.engage_kill_switch())

    def report(self):
        return node_report(self.node)

    def close(self):
        pass


def node_report(node) -> dict:
    """What the hook reports about a node after a case."""
    calls = getattr(node.executor, "calls", [])
    return {
        "executor_calls": len(calls),
        "executor_terminated": sum(1 for call in calls if call.get("terminated")),
        "windows_shown": len(node.window.shown),
        "closed_reason": node.closed_reason,
        "reconnect_allowed": node.reconnect_allowed,
        "policy_fingerprint": node.policy.fingerprint(),
        "next_reconnect_delay": node.next_reconnect_delay,
    }


@dataclass
class CaseResult:
    name: str
    version: int
    failures: list

    @property
    def passed(self) -> bool:
        return not self.failures


def raw_frame(step) -> str:
    if step["op"] == "send_raw":
        return step["data"] * step.get("repeat", 1)
    return json.dumps(step["frame"], ensure_ascii=False)


def play(case, driver) -> None:
    driver.silent = case.get("setup", {}).get("silent", False)
    for step in case["steps"]:
        op = step["op"]
        if op in ("send", "send_raw"):
            driver.send(raw_frame(step), step.get("frame"))
        elif op == "advance":
            driver.advance(step["seconds"])
        elif op == "tick":
            driver.tick()
        elif op == "disconnect":
            driver.disconnect()
        elif op == "kill_switch":
            driver.kill_switch()
        else:
            raise ValueError(f"unknown step {op!r}")


def check(case, frames, report, checker, *, policy_fingerprint, home=None) -> list:
    """The failures of one played case (empty when it passed)."""
    expect = case["expect"]
    failures = []
    session = [f for f in frames if f["type"] not in ("hello", "heartbeat")]
    matchers = expect["frames"]
    if len(session) != len(matchers):
        failures.append(
            f"expected {len(matchers)} frames, got {len(session)}: "
            + ", ".join(_describe(f) for f in session)
        )
    for index, (matcher, frame) in enumerate(zip(matchers, session, strict=False)):
        for key, value in matcher.items():
            if frame.get(key, _MISSING) != value:
                failures.append(f"frame {index} ({_describe(frame)}): {key} != {value!r}")
    for key in (
        "executor_calls",
        "executor_terminated",
        "windows_shown",
        "closed_reason",
        "reconnect_allowed",
    ):
        if key in expect and report.get(key) != expect[key]:
            failures.append(f"{key}: expected {expect[key]!r}, got {report.get(key)!r}")
    if report.get("policy_fingerprint") != policy_fingerprint:
        failures.append("the local policy changed during the session")
    if checker.frame_errors:
        failures.append(f"gateway frame errors: {checker.errors}")
    text = json.dumps(frames, ensure_ascii=False)
    for encoded in expect.get("absent_b64", ()):
        if base64.b64decode(encoded).decode("utf-8") in text:
            failures.append("a raw secret reached a frame")
    if "worktrees_after" in expect and home is not None:
        count = len(list_worktrees(str(home / "work" / "acme")))
        if count != expect["worktrees_after"]:
            failures.append(f"worktrees_after: expected {expect['worktrees_after']}, got {count}")
    if "backoff_max_seconds" in expect:
        delay = report.get("next_reconnect_delay")
        if delay is None or not 0 <= delay <= expect["backoff_max_seconds"]:
            failures.append("the first reconnect backoff is outside 0..backoff_max_seconds")
    return failures


_MISSING = object()


def _describe(frame):
    parts = [frame.get("type", "?")]
    for key in ("state", "status", "refusal_code", "stream"):
        if frame.get(key) is not None:
            parts.append(str(frame[key]))
    return ":".join(parts)


def run_case_in_process(case, *, home, worktree_base=None) -> CaseResult:
    node = build_reference_node(case, home=home, worktree_base=worktree_base)
    fingerprint = node.policy.fingerprint()
    driver = InProcessDriver(node, case["version"])
    play(case, driver)
    failures = check(
        case,
        driver.frames,
        driver.report(),
        driver.checker,
        policy_fingerprint=fingerprint,
        home=home,
    )
    return CaseResult(case["name"], case["version"], failures)


def format_table(results) -> str:
    """The pass/fail table `node_conformance_server` prints."""
    lines = []
    width = max((len(r.name) for r in results), default=10)
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(f"v{result.version}  {result.name.ljust(width)}  {mark}")
        for failure in result.failures:
            lines.append(f"      - {failure}")
    passed = sum(1 for r in results if r.passed)
    by_version = {}
    for result in results:
        total, ok = by_version.get(result.version, (0, 0))
        by_version[result.version] = (total + 1, ok + int(result.passed))
    for version in sorted(by_version):
        total, ok = by_version[version]
        lines.append(f"v{version}: {ok}/{total} passed")
    lines.append("ALL PASS" if passed == len(results) else f"FAILED: {len(results) - passed}")
    return "\n".join(lines)
