"""`friday-voice conformance-child`: the scripted hostile child (brief D6).

A conformance build of a host (the Swift `VoiceChildHost` under `#if
CONFORMANCE`, Mac session M1; the reference host in
`runner.run_host_case_live`) launches

    friday-voice conformance-child --case <id> [--cases <dir>]

as its child and runs the case's host-side steps itself. This process
plays the child side of the same case over stdio:

- `child_send` writes the frame bytes exactly (malformed ones included);
- `child_close_stdout` closes stdout; `child_exit` exits with the code;
- `host_local` waits for the frame the host must send for that event
  (link/mute/mode/ptt) -- the sync point that keeps both sides in step;
- `expect` reads the host frames listed in `to_child` that are not read
  yet and checks their types; `link: dropped` waits for the host to close
  this process's stdin (or to kill it);
- `advance_ms` sleeps (scaled by `--time-scale`);
- after every `expect` it pauses `--settle-ms` (default 300) before the
  next step, so the host side can check its expectation before the next
  frame arrives.

At the end it waits for the host to close stdin, then writes one verdict
line to stderr (`{"case": ..., "verdict": "pass"|"fail", "failures": [...]}`)
and exits 0 on pass, 1 on fail. The host side judges mic, indicator and
restarts; this side judges what the host wrote to it.
"""

import base64
import json
import os
import queue
import sys
import threading
import time

from ..protocol.framing import read_frame
from .build import load_cases

TIMEOUT_S = 10.0
LOCAL_EVENT_FRAME = {"link": "link", "mute": "mute", "mode": "mode", "ptt": "ptt"}


def _reader(stream, inbox):
    while True:
        try:
            frame = read_frame(stream)
        except Exception:  # any read failure ends the link here
            inbox.put(None)
            return
        if frame is None:
            inbox.put(None)
            return
        inbox.put(frame[0]["type"])


class _Script:
    def __init__(self, case, stdin, stdout, time_scale, settle_ms):
        self.case = case
        self.stdout = stdout
        self.time_scale = time_scale
        self.settle_s = settle_ms / 1000
        self.inbox = queue.Queue()
        self.read_since_expect = []
        self.eof = False
        self.failures = []
        threading.Thread(target=_reader, args=(stdin, self.inbox), daemon=True).start()

    def _next(self, timeout=TIMEOUT_S):
        if self.eof:
            return None
        try:
            item = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return "timeout"
        if item is None:
            self.eof = True
        return item

    def _read(self, want, index):
        got = self._next()
        if got != want:
            self.failures.append(f"step {index}: host sent {got!r}, expected {want!r}")
        else:
            self.read_since_expect.append(got)

    def run(self):
        for index, step in enumerate(self.case["steps"]):
            op = step["op"]
            if op == "child_send":
                try:
                    self.stdout.write(base64.b64decode(step["frame_b64"]))
                    self.stdout.flush()
                except (BrokenPipeError, OSError, ValueError):
                    pass
            elif op == "child_close_stdout":
                self.stdout.close()
            elif op == "child_exit":
                return step["code"]
            elif op == "host_local":
                self._read(LOCAL_EVENT_FRAME[step["event"]], index)
            elif op == "advance_ms":
                time.sleep(step["ms"] / 1000 * self.time_scale)
            elif op == "expect":
                want = step.get("to_child", [])
                done = self.read_since_expect
                if want[: len(done)] != done:
                    self.failures.append(f"step {index}: host sent {done}, expected {want}")
                for item in want[len(done) :]:
                    self._read(item, index)
                self.read_since_expect = []
                if step.get("link") == "dropped":
                    while not self.eof and self._next() not in (None, "timeout"):
                        pass
                time.sleep(self.settle_s)
            # host_capture: the host side captures; its frames are checked by `expect`.
        while not self.eof and self._next() not in (None, "timeout"):
            pass
        return None


def main(case_id, cases_dir=None, time_scale=1.0, settle_ms=300) -> int:
    cases = {case["id"]: case for case in load_cases("host", cases_dir)}
    if case_id not in cases:
        sys.stderr.write(json.dumps({"case": case_id, "verdict": "unknown_case"}) + "\n")
        return 2
    stdout = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    script = _Script(cases[case_id], sys.stdin.buffer, stdout, time_scale, settle_ms)
    exit_code = script.run()
    verdict = {
        "case": case_id,
        "verdict": "fail" if script.failures else "pass",
        "failures": script.failures,
    }
    sys.stderr.write(json.dumps(verdict) + "\n")
    sys.stderr.flush()
    # Leave without the interpreter's shutdown (the stdin reader thread may
    # still be blocked in read()).
    os._exit(exit_code if exit_code is not None else (1 if script.failures else 0))
