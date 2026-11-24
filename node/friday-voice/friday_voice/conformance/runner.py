"""Run the `friday-voice/v1` conformance cases (brief D6).

- `run_host_case`: the reference host (`HostCore`, manual clock) against
  the case's scripted hostile child, in process;
- `run_child_case`: the reference child (`ChildCore` through `ChildLink`,
  manual clock) against the case's scripted hostile host, in process;
- `run_child_case_live`: a real `friday-voice run` process over its stdio
  pipes, real clock (cases with `"clock": "manual"` are skipped);
- `run_host_case_live`: the reference host driving a real
  `friday-voice conformance-child` process that plays the hostile child
  (the same run the Swift host's conformance build performs, Mac M1).

Each runner returns a list of failure strings: empty means pass.
"""

import base64
import io
import queue
import subprocess
import sys
import threading
import time

from ..child import ChildCore, ChildLink
from ..ports import ManualClock, RealClock
from ..protocol.framing import decode_frame, read_frame
from ..protocol.messages import CHILD_TO_HOST, ProtocolError
from ..reference.host import HostCore, ProcessHost
from .definitions import CREDENTIAL, SILENCE_20MS

LIVE_TIMEOUT_S = 5.0
LIVE_SETTLE_S = 0.2


def host_credential():
    return {key: value for key, value in CREDENTIAL.items() if key != "type"}


def _frame_types(frames):
    return [decode_frame(frame)[0]["type"] for frame in frames]


def _check_host(step, core, to_child, index):
    failures = []
    observed = {
        "mic": "held" if core.mic_held else "released",
        "link": "open" if core.link_open else "dropped",
        "indicator": core.indicator,
        "failure": core.last_failure,
        "restart_in_ms": core.restart_in_ms,
        "dropped_play": core.dropped_play,
        "to_child": to_child,
    }
    for key, want in step.items():
        if key in ("op", "describe"):
            continue
        if observed[key] != want:
            failures.append(f"step {index}: {key} is {observed[key]!r}, expected {want!r}")
    return failures


def _host_local(core, step):
    event, value = step["event"], step["value"]
    if event == "link":
        core.set_link(value == "up")
    elif event == "mute":
        core.set_mute(value)
    elif event == "mode":
        core.set_mode(value)
    elif event == "ptt":
        core.ptt(value)
    else:
        raise ValueError(f"unknown host_local event {event!r}")


def _host_core(case, clock):
    config = case["host"]
    return HostCore(
        clock,
        credential=host_credential(),
        mode=config["mode"],
        muted=config["muted"],
        link_up=config["link_up"],
    )


def run_host_case(case) -> list:
    clock = ManualClock()
    core = _host_core(case, clock)
    core.start_child()
    sent = []
    failures = []
    for index, step in enumerate(case["steps"]):
        op = step["op"]
        if op == "child_send":
            core.child_bytes(base64.b64decode(step["frame_b64"]))
        elif op == "child_close_stdout":
            core.child_stream_closed()
        elif op == "child_exit":
            core.child_exited(step["code"])
        elif op == "host_local":
            _host_local(core, step)
        elif op == "host_capture":
            for _ in range(step["frames"]):
                core.feed_audio(SILENCE_20MS)
        elif op == "advance_ms":
            clock.advance(step["ms"])
            core.tick()
            if core.restart_due():
                core.start_child()
        elif op == "expect":
            sent += _frame_types(core.take_outbound())
            failures += _check_host(step, core, sent, index)
            sent = []
            continue
        else:
            raise ValueError(f"unknown host step {op!r}")
        sent += _frame_types(core.take_outbound())
    return failures


def _match_frames(got, want, index):
    if len(got) != len(want):
        return [
            f"step {index}: child sent {[h.get('type') for h in got]}, "
            f"expected {[w['type'] for w in want]}"
        ]
    failures = []
    for position, (header, expected) in enumerate(zip(got, want, strict=True)):
        for key, value in expected.items():
            if header.get(key) != value:
                failures.append(
                    f"step {index}: frame {position} {key} is {header.get(key)!r}, "
                    f"expected {value!r}"
                )
    return failures


def run_child_case(case) -> list:
    clock = ManualClock()
    core = ChildCore(clock, log_stream=io.StringIO())
    link = ChildLink(core)
    got = []
    failures = []
    for index, step in enumerate(case["steps"]):
        op = step["op"]
        if op == "host_send":
            got += [decode_frame(f, direction=CHILD_TO_HOST)[0] for f in link.feed(_b64(step))]
        elif op == "close_stdin":
            got += [decode_frame(f, direction=CHILD_TO_HOST)[0] for f in link.close()]
        elif op == "advance_ms":
            clock.advance(step["ms"])
            got += [decode_frame(f, direction=CHILD_TO_HOST)[0] for f in link.tick()]
        elif op == "expect_frames":
            failures += _match_frames(got, step["frames"], index)
            got = []
        elif op == "expect_exit":
            if not core.finished or core.exit_code != step["code"]:
                failures.append(
                    f"step {index}: child exit is {core.exit_code!r}, expected {step['code']!r}"
                )
        elif op == "expect_running":
            if core.finished:
                failures.append(f"step {index}: the child ended")
        elif op == "expect_buffered":
            if core.buffered_bytes() != step["bytes"]:
                failures.append(
                    f"step {index}: {core.buffered_bytes()} bytes buffered, "
                    f"expected {step['bytes']}"
                )
        else:
            raise ValueError(f"unknown child step {op!r}")
    return failures


def _b64(step):
    return base64.b64decode(step["frame_b64"])


# --- live runs (real processes, real clock) ---


def child_argv():
    return [sys.executable, "-m", "friday_voice", "run"]


def _reader(stream, out):
    while True:
        try:
            frame = read_frame(stream, direction=CHILD_TO_HOST)
        except ProtocolError as exc:
            out.put(("error", exc.code))
            return
        except (OSError, ValueError):
            out.put(("eof", None))
            return
        if frame is None:
            out.put(("eof", None))
            return
        out.put(("frame", frame[0]))


def run_child_case_live(case, argv=None) -> list:
    """Drive a real child process. Returns failures; a manual-clock case
    returns ["skipped: manual clock"]."""
    if case["clock"] == "manual":
        return ["skipped: manual clock"]
    process = subprocess.Popen(
        argv or child_argv(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    inbox = queue.Queue()
    threading.Thread(target=_reader, args=(process.stdout, inbox), daemon=True).start()
    failures = []
    try:
        for index, step in enumerate(case["steps"]):
            op = step["op"]
            if op == "host_send":
                try:
                    process.stdin.write(_b64(step))
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass  # the child already stopped; the expectations say whether it should
            elif op == "close_stdin":
                process.stdin.close()
            elif op == "expect_frames":
                failures += _match_frames(_collect(inbox, len(step["frames"])), step["frames"], index)
            elif op == "expect_exit":
                try:
                    code = process.wait(timeout=LIVE_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    code = None
                if code != step["code"]:
                    failures.append(f"step {index}: child exit is {code!r}, expected {step['code']}")
            elif op == "expect_running":
                time.sleep(LIVE_SETTLE_S)
                if process.poll() is not None:
                    failures.append(f"step {index}: the child ended")
            elif op == "expect_buffered":
                continue  # in-process only: the child's memory is not observable
            else:
                raise ValueError(f"unknown child step {op!r}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=LIVE_TIMEOUT_S)
    return failures


def _collect(inbox, count):
    """`count` frames from the child (keepalive states excluded), then a
    short settle to catch extras."""
    got = []
    deadline = time.monotonic() + LIVE_TIMEOUT_S
    while len(got) < count and time.monotonic() < deadline:
        try:
            kind, value = inbox.get(timeout=max(deadline - time.monotonic(), 0.01))
        except queue.Empty:
            break
        if kind != "frame":
            inbox.put((kind, value))
            break
        got.append(value)
    time.sleep(LIVE_SETTLE_S)
    while True:
        try:
            kind, value = inbox.get_nowait()
        except queue.Empty:
            break
        if kind != "frame":
            inbox.put((kind, value))
            break
        got.append(value)
    return got


def conformance_child_argv(case_id, cases_dir=None):
    argv = [sys.executable, "-m", "friday_voice", "conformance-child", "--case", case_id]
    if cases_dir:
        argv += ["--cases", str(cases_dir)]
    return argv


def _wait(predicate, timeout=LIVE_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def run_host_case_live(case, argv=None) -> list:
    """The reference host drives `friday-voice conformance-child`, which
    plays the case's child side; this side runs the host steps and checks
    every expectation once the host has taken in every frame the child
    was scripted to send so far."""
    if case["clock"] == "manual":
        return ["skipped: manual clock"]
    core = _host_core(case, RealClock())
    host = ProcessHost(core, argv or conformance_child_argv(case["id"])).start()
    failures = []
    child_frames = 0
    seen = 0
    try:
        for index, step in enumerate(case["steps"]):
            op = step["op"]
            if op == "child_send":
                child_frames += 1
            elif op in ("child_close_stdout", "child_exit"):
                _wait(lambda: not core.link_open)
            elif op == "host_local":
                with host.lock:
                    _host_local(core, step)
                    host.flush()
            elif op == "host_capture":
                with host.lock:
                    for _ in range(step["frames"]):
                        core.feed_audio(SILENCE_20MS)
                    host.flush()
            elif op == "expect":
                frames_in = child_frames
                _wait(lambda n=frames_in: core.frames_received >= n or not core.link_open)
                if "link" in step and step["link"] == "dropped":
                    _wait(lambda: not core.link_open)
                with host.lock:
                    sent = host.sent_types[seen:]
                    seen = len(host.sent_types)
                    failures += _check_host(step, core, sent, index)
            else:
                raise ValueError(f"unknown host step {op!r}")
    finally:
        host.stop()
    return failures
