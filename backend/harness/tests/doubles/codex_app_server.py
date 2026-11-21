"""Fake Codex app-server (Phase 10 brief D10 test double).

Replays a fixture transcript over real OS pipes, standing in for the
interactive container: the driver writes to `FakeContainer.stdin` and reads
`FakeContainer.stdout`, exactly as it does with `docker run -i`. No Codex
binary, no Docker.

A transcript is a list of `{"dir": "c2s" | "s2c", "msg": {...}}` frames
(the JSONL fixtures under `harness/tests/fixtures/codex_app_server/`).
`script_from_transcript` turns it into steps:

- `c2s` request -> `expect` (the next client frame must be that method; the
  following `s2c` response is sent back with the client's own id);
- `c2s` notification -> `expect_notification`;
- `c2s` answer to a server request -> `await_answer` (records the decision
  the driver actually sent -- tests assert on it, the fixture's value is
  only the contract example);
- `s2c` notification / server request -> `send`.

Extra hand-written steps: `raw` (bytes, for malformed or oversize lines),
`exit` (close stdout early), `hang` (send nothing until stdin closes).
"""

import json
import os
import threading
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex_app_server"


def load_transcript(name):
    frames = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            frames.append(json.loads(line))
    return frames


def script_from_transcript(frames):
    steps = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        msg = frame["msg"]
        if frame["dir"] == "c2s":
            if "method" in msg and "id" in msg:
                # Its response is the next s2c frame without a method; any
                # notifications in between are sent before the response.
                pending = []
                cursor = index + 1
                while cursor < len(frames) and "method" in frames[cursor]["msg"]:
                    pending.append(frames[cursor]["msg"])
                    cursor += 1
                response = frames[cursor]["msg"] if cursor < len(frames) else {"result": {}}
                steps.append(
                    {
                        "expect": msg["method"],
                        "before": pending,
                        "reply": {k: v for k, v in response.items() if k != "id"},
                    }
                )
                index = cursor + 1
                continue
            if "method" in msg:
                steps.append({"expect_notification": msg["method"]})
            else:
                steps.append({"await_answer": msg["id"]})
        else:
            steps.append({"send": msg})
        index += 1
    return steps


class FakeContainer:
    """The driver side of the pipes plus the server thread."""

    def __init__(self, steps, *, name="harness-impl-fake"):
        self.name = name
        self.steps = list(steps)
        self.received = []
        self.answers = {}
        self.errors = []
        self.closed_stdin = False
        self.killed = False
        self.reaped = False
        c2s_read, c2s_write = os.pipe()
        s2c_read, s2c_write = os.pipe()
        self.stdin = os.fdopen(c2s_write, "wb", buffering=0)
        self.stdout = os.fdopen(s2c_read, "rb")
        self._server_in = os.fdopen(c2s_read, "rb")
        self._server_out = os.fdopen(s2c_write, "wb", buffering=0)
        self._out_closed = False
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    # --- the InteractiveContainer surface the driver/job use -------------

    def close_stdin(self):
        self.closed_stdin = True
        try:
            self.stdin.close()
        except OSError:
            pass

    def kill(self):
        self.killed = True
        self.close_stdin()
        self._close_out()

    def reap(self, timeout=5):
        self.reaped = True
        self.thread.join(timeout)

    # --- server -------------------------------------------------------------

    def _close_out(self):
        with self._lock:
            if not self._out_closed:
                self._out_closed = True
                try:
                    self._server_out.close()
                except OSError:
                    pass

    def _write(self, data):
        with self._lock:
            if self._out_closed:
                return
            try:
                self._server_out.write(data)
            except (BrokenPipeError, OSError, ValueError):
                self._out_closed = True

    def _send(self, message):
        self._write((json.dumps(message) + "\n").encode("utf-8"))

    def _read_client(self):
        line = self._server_in.readline()
        if not line:
            return None
        message = json.loads(line)
        self.received.append(message)
        return message

    def _serve(self):
        try:
            for step in self.steps:
                if not self._run_step(step):
                    break
            else:
                # Script done: wait for the driver to close stdin.
                while self._read_client() is not None:
                    pass
        except Exception as exc:  # recorded for the test, never raised in a thread
            self.errors.append(repr(exc))
        finally:
            self._close_out()

    def _run_step(self, step):
        if "expect" in step:
            message = self._read_client()
            if message is None or message.get("method") != step["expect"]:
                self.errors.append(f"expected {step['expect']}, got {message!r}")
                return False
            for frame in step.get("before", ()):
                self._send(frame)
            self._send({"id": message.get("id"), **step["reply"]})
            return True
        if "expect_notification" in step:
            message = self._read_client()
            if message is None or message.get("method") != step["expect_notification"]:
                self.errors.append(f"expected {step['expect_notification']}, got {message!r}")
                return False
            return True
        if "await_answer" in step:
            message = self._read_client()
            if message is None:
                return False
            self.answers[json.dumps(message.get("id"))] = (message.get("result") or {}).get(
                "decision"
            )
            return True
        if "send" in step:
            self._send(step["send"])
            return True
        if "raw" in step:
            self._write(step["raw"])
            return True
        if "exit" in step:
            self._close_out()
            return False
        if "hang" in step:
            while self._read_client() is not None:
                pass
            return False
        raise ValueError(f"unknown step {step!r}")

    def answer(self, request_id):
        return self.answers.get(json.dumps(request_id))
