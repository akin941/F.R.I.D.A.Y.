"""The child's protocol shell (Phase 11 brief D6, D12): the minimal
reference child of `friday-voice/v1`, without the pipeline.

`ChildCore` is a pure, clock-injected state machine: it consumes decoded
host frames and returns the frames to send back. `run_stdio` binds it to
the stdin/stdout pipes the host gave the child. Branch 3 plugs the Pipecat
pipeline in behind the same core; every fail-closed rule here stays.

Rules (D6, D12), each pinned by a child conformance case:
- the first frame must be `hello`; nothing at all is sent before the first
  valid `credential` (then `ready` and a `state`);
- `link: down`, `mute: true`, `mute: unreadable`, `mode: off`, a credential
  that expires, a host protocol error, stdin EOF and `shutdown` all mean
  "stop consuming audio, drop every buffer, send `release_mic`";
- audio is consumed only while the credential is valid, the link is up,
  mute is readable and off and the mode is not `off`; pre-wake audio lives
  only in a 2 s in-memory ring buffer and is never written anywhere;
- a `state` frame goes out on every change and at least every
  STATE_KEEPALIVE_MS, so a healthy child never trips the host's 10 s
  watchdog;
- always-on is wired but not armed: the arming policy (server gate, D11)
  lands in branch 5, so `always_on` runs as push-to-talk (`armed_ptt`).
"""

import os
import queue
import sys
import threading
from datetime import datetime

from . import __version__, log
from .protocol.framing import FrameDecoder, encode_frame, read_frame
from .protocol.messages import (
    CHILD_TO_HOST,
    HOST_TO_CHILD,
    PROTOCOL,
    SAMPLE_RATE,
    ProtocolError,
    validate,
)

BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16 kHz mono PCM16
RING_BUFFER_BYTES = 2 * BYTES_PER_SECOND  # D8: at most 2 s of pre-wake audio
UTTERANCE_MAX_BYTES = 30 * BYTES_PER_SECOND  # ADR-011 upload cap
STATE_KEEPALIVE_MS = 5_000  # well inside the host's 10 s watchdog
TICK_SECONDS = 0.25

AWAIT_HELLO = "await_hello"
AWAIT_CREDENTIAL = "await_credential"
RUNNING = "running"
FINISHED = "finished"

EXIT_OK = 0
EXIT_PROTOCOL_ERROR = 2


def parse_expires_at(value) -> int:
    """`expires_at` (ISO 8601, UTC) as epoch milliseconds."""
    return int(datetime.fromisoformat(value).timestamp() * 1000)


class ChildCore:
    def __init__(self, clock, *, version=__version__, log_stream=None):
        self.clock = clock
        self.log_stream = log_stream
        self.version = version
        self.phase = AWAIT_HELLO
        self.exit_code = None
        self.mode = "off"
        self.muted = "unreadable"
        self.link_up = False
        self.ptt_down = False
        self.token = None
        self.server_url = None
        self.ca_sha256 = None
        self.expires_ms = None
        self.ring = bytearray()
        self.utterance = bytearray()
        self.dropped_audio_frames = 0
        self.counters = {"wake_accepts_today": 0, "wake_discards_today": 0, "storm_mutes_today": 0}
        self._last_state = None
        self._last_state_at = None
        self._expired_reported = False

    # --- derived state ---

    @property
    def finished(self):
        return self.phase == FINISHED

    def credential_valid(self) -> bool:
        return self.token is not None and self.clock.now_ms() < self.expires_ms

    def blocking_reason(self):
        """Why audio may not be consumed now (a `release_mic` reason), or None."""
        if self.muted == "unreadable":
            return "mute_unreadable"
        if self.muted is True:
            return "muted"
        if not self.link_up:
            return "link_down"
        if self.mode == "off":
            return "mode_off"
        if self.token is None:
            return "credential_missing"
        if not self.credential_valid():
            return "credential_expired"
        return None

    def consuming(self) -> bool:
        return self.phase == RUNNING and self.blocking_reason() is None

    def current_state(self) -> str:
        reason = self.blocking_reason()
        if reason in ("muted", "mute_unreadable"):
            return "muted"
        if reason in ("link_down", "mode_off"):
            return "off"
        if reason is not None:
            return "error"
        if self.ptt_down:
            return "listening"
        return "armed_ptt"

    def buffered_bytes(self) -> int:
        return len(self.ring) + len(self.utterance)

    # --- outbound helpers ---

    def _log(self, code, detail=""):
        log.emit(code, detail, stream=self.log_stream)

    def _state_frame(self):
        state = self.current_state()
        self._last_state = state
        self._last_state_at = self.clock.monotonic_ms()
        return (
            {
                "type": "state",
                "state": state,
                "mode": self.mode,
                "session_open": False,
                **self.counters,
                "at_ms": self.clock.now_ms(),
            },
            b"",
        )

    def _state_if_changed(self):
        if self.phase != RUNNING:
            return []
        if self.current_state() != self._last_state:
            return [self._state_frame()]
        return []

    def _drop_buffers(self):
        self.ring.clear()
        self.utterance.clear()
        self.ptt_down = False

    def _release(self, reason):
        self._drop_buffers()
        if self.phase != RUNNING:
            return []
        return [({"type": "release_mic", "reason": reason}, b"")]

    def _finish(self, code):
        self._drop_buffers()
        self.phase = FINISHED
        self.exit_code = code

    # --- inbound ---

    def protocol_error(self, code):
        """A host frame failed framing or schema checks: log the code,
        release, stop."""
        self._log(f"protocol.{code}")
        out = []
        if self.phase == RUNNING:
            out = [(log.log_message(f"protocol.{code}"), b""), *self._release("protocol_error")]
        self._finish(EXIT_PROTOCOL_ERROR)
        return out

    def eof(self):
        """stdin closed: the host is gone or dropped the link."""
        out = self._release("stdin_closed")
        self._finish(EXIT_OK)
        return out

    def receive(self, header, payload=b""):
        if self.finished:
            return []
        message_type = header["type"]
        if self.phase == AWAIT_HELLO:
            if message_type != "hello":
                return self.protocol_error("hello_expected")
            self.mode = header["mode"]
            self.muted = header["muted"]
            self.link_up = header["link_up"]
            self.phase = AWAIT_CREDENTIAL
            return []
        if message_type == "hello":
            return self.protocol_error("duplicate_hello")
        handler = getattr(self, f"_on_{message_type}")
        return handler(header, payload)

    def tick(self):
        if self.phase != RUNNING:
            return []
        out = []
        if self.token is not None and not self.credential_valid() and not self._expired_reported:
            self._expired_reported = True
            self._log("credential.expired")
            out += self._release("credential_expired")
        out += self._state_if_changed()
        if not out and self.clock.monotonic_ms() - self._last_state_at >= STATE_KEEPALIVE_MS:
            out.append(self._state_frame())
        return out

    def _on_credential(self, header, _payload):
        try:
            expires_ms = parse_expires_at(header["expires_at"])
        except ValueError:
            return self.protocol_error("schema_invalid")
        if expires_ms <= self.clock.now_ms():
            # An expired credential is a missing one: nothing changes, and
            # before `ready` nothing is sent.
            self._log("credential.expired_on_arrival")
            return []
        self.token = header["token"]
        self.server_url = header["server_url"]
        self.ca_sha256 = header["ca_sha256"]
        self.expires_ms = expires_ms
        self._expired_reported = False
        if self.phase == AWAIT_CREDENTIAL:
            self.phase = RUNNING
            return [
                ({"type": "ready", "protocol": PROTOCOL, "child_version": self.version}, b""),
                self._state_frame(),
            ]
        return self._state_if_changed()

    def _on_audio(self, _header, payload):
        if not self.consuming():
            self.dropped_audio_frames += 1
            return []
        self.ring += payload
        if len(self.ring) > RING_BUFFER_BYTES:
            del self.ring[: len(self.ring) - RING_BUFFER_BYTES]
        if self.ptt_down and len(self.utterance) + len(payload) <= UTTERANCE_MAX_BYTES:
            self.utterance += payload
        return []

    def _on_playback(self, _header, _payload):
        return []  # the shell never plays anything (the pipeline does, branch 3)

    def _on_mode(self, header, _payload):
        self.mode = header["mode"]
        if self.mode == "off":
            return [*self._release("mode_off"), *self._state_if_changed()]
        return self._state_if_changed()

    def _on_mute(self, header, _payload):
        self.muted = header["muted"]
        if self.muted == "unreadable":
            return [*self._release("mute_unreadable"), *self._state_if_changed()]
        if self.muted is True:
            return [*self._release("muted"), *self._state_if_changed()]
        return self._state_if_changed()

    def _on_link(self, header, _payload):
        self.link_up = header["state"] == "up"
        if not self.link_up:
            return [*self._release("link_down"), *self._state_if_changed()]
        return self._state_if_changed()

    def _on_ptt(self, header, _payload):
        if header["key"] == "down":
            if self.consuming():
                self.ptt_down = True
                self.utterance.clear()
        else:
            # Branch 3 transcribes the utterance here; the shell drops it.
            self.ptt_down = False
            self.utterance.clear()
        return self._state_if_changed()

    def _on_shutdown(self, _header, _payload):
        out = self._release("shutdown")
        self._finish(EXIT_OK)
        return out


class ChildLink:
    """In-process binding of a ChildCore to a host byte stream (the
    conformance runner and the tests): the same decode -> validate -> core
    path as `run_stdio`, without threads or a real clock."""

    def __init__(self, core):
        self.core = core
        self._decoder = FrameDecoder()

    @staticmethod
    def _encode(frames):
        return [encode_frame(header, payload, direction=CHILD_TO_HOST) for header, payload in frames]

    def feed(self, data) -> list:
        out = []
        if self.core.finished:
            return out
        frames, error = self._decoder.feed(data)
        for header, payload in frames:
            try:
                validate(header, HOST_TO_CHILD)
            except ProtocolError as exc:
                out += self.core.protocol_error(exc.code)
                return self._encode(out)
            out += self.core.receive(header, payload)
            if self.core.finished:
                return self._encode(out)
        if error:
            out += self.core.protocol_error(error)
        else:
            out += self.core.tick()
        return self._encode(out)

    def close(self) -> list:
        if self.core.finished:
            return []
        error = self._decoder.close()
        return self._encode(self.core.protocol_error(error) if error else self.core.eof())

    def tick(self) -> list:
        return self._encode(self.core.tick())


def _reader(stream, inbox):
    while True:
        try:
            frame = read_frame(stream, direction=HOST_TO_CHILD)
        except ProtocolError as exc:
            inbox.put(("error", exc.code))
            return
        except (OSError, ValueError):
            inbox.put(("eof", None))
            return
        if frame is None:
            inbox.put(("eof", None))
            return
        inbox.put(("frame", frame))


def run_stdio(stdin, stdout, clock, *, core=None, tick_seconds=TICK_SECONDS) -> int:
    """Serve one host over a pair of binary streams until the core finishes.
    Returns the process exit code."""
    core = core or ChildCore(clock)
    inbox = queue.Queue()
    threading.Thread(target=_reader, args=(stdin, inbox), daemon=True).start()

    def send(frames):
        for header, payload in frames:
            try:
                stdout.write(encode_frame(header, payload, direction=CHILD_TO_HOST))
                stdout.flush()
            except (OSError, ValueError):
                core._finish(EXIT_OK)  # the host closed our stdout: nothing to tell
                return

    while not core.finished:
        try:
            kind, value = inbox.get(timeout=tick_seconds)
        except queue.Empty:
            send(core.tick())
            continue
        if kind == "frame":
            send(core.receive(*value))
        elif kind == "error":
            send(core.protocol_error(value))
        else:
            send(core.eof())
        if not core.finished:
            send(core.tick())
    return core.exit_code


def protect_stdout():
    """Keep the protocol stream for the frames alone: return a private
    binary handle on fd 1, then point fd 1 and sys.stdout at stderr so a
    stray print from any library can never corrupt the framing."""
    sys.stdout.flush()
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out
