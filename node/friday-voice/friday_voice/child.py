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
- always-on arms only under the D11 arming policy (`friday_voice.arming`,
  checked by the pipeline); without a pipeline, or without a loaded and
  verified wake detector, `always_on` runs as push-to-talk (`armed_ptt`).

With a `VoicePipeline` attached (branch 3, `friday_voice.pipeline`) the
shell hands it every consumed frame, the push-to-talk key, the host's
playback reports and every credential, and the pipeline adds its states
(`listening`, `thinking`, `speaking`, `error`, `storm_muted`) and frames
(`play`, `stop_playback`, `earcon`, its own `release_mic`). Every
fail-closed rule above stays the shell's and runs first: a release aborts
the pipeline's turn (its jobs cancelled, its playback stopped). Without a
pipeline the shell behaves exactly as the branch 2 reference child (the
conformance runner's in-process child).
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
from .session import derive_state

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
    def __init__(self, clock, *, version=__version__, log_stream=None, pipeline=None):
        self.clock = clock
        self.pipeline = pipeline
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
        self._pending = []  # pipeline frames produced between host frames
        if pipeline is not None:
            pipeline.attach(self)

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
        return (
            self.phase == RUNNING
            and self.blocking_reason() is None
            and (self.pipeline is None or self.pipeline.accepting())
        )

    def current_state(self) -> str:
        """`friday_voice.session.derive_state` over the shell's blocking
        rules and the pipeline's turn (one precedence, one place)."""
        reason = self.blocking_reason()
        if reason is not None or self.pipeline is None:
            return derive_state(blocking_reason=reason, ptt_down=self.ptt_down)
        return derive_state(**self.pipeline.state_inputs(self.mode))

    def buffered_bytes(self) -> int:
        size = len(self.ring) + len(self.utterance)
        turn = self.pipeline.turn if self.pipeline is not None else None
        if turn is not None:
            size += len(turn.capture.pcm) + len(turn.wav or b"")
        return size

    # --- outbound helpers ---

    def _log(self, code, detail=""):
        log.emit(code, detail, stream=self.log_stream)

    def _session_open(self) -> bool:
        return bool(self.pipeline and self.pipeline.session_open)

    def _state_frame(self):
        state = self.current_state()
        session_open = self._session_open()
        self._last_state = (state, session_open)
        self._last_state_at = self.clock.monotonic_ms()
        return (
            {
                "type": "state",
                "state": state,
                "mode": self.mode,
                "session_open": session_open,
                **self.counters,
                "at_ms": self.clock.now_ms(),
            },
            b"",
        )

    def _state_if_changed(self):
        if self.phase != RUNNING:
            return []
        # A new state, or a live session opening or closing (D12, the HUD).
        if (self.current_state(), self._session_open()) != self._last_state:
            return [self._state_frame()]
        return []

    def _drop_buffers(self):
        self.ring.clear()
        self.utterance.clear()
        self.ptt_down = False

    def drop_buffers(self):
        """The pipeline released the mic itself (credential refused, storm)."""
        self._drop_buffers()

    def _release(self, reason):
        stops = []
        if self.pipeline is not None:
            stops = self.pipeline.abort(reason)
        self._drop_buffers()
        if self.phase != RUNNING:
            return []
        return [*stops, ({"type": "release_mic", "reason": reason}, b"")]

    def _take_pending(self):
        out, self._pending = self._pending, []
        return out

    def flush_pipeline(self, frames=()):
        """Pipeline frames produced outside a host frame (a worker event):
        kept until the next receive/tick/on_worker returns them."""
        if self.pipeline is None or self.finished:
            return
        self._pending += [*frames, *self.pipeline._drain()]
        self._pending += self._state_if_changed()

    def on_worker(self, job_id, event):
        """A worker event from the stdio runner's inbox."""
        if self.finished or self.pipeline is None:
            return []
        out = self._take_pending() + self.pipeline.on_worker(job_id, event)
        return out + self._state_if_changed()

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
        out = self._take_pending()
        if self.pipeline is not None:
            out += self.pipeline.tick()
        if self.token is not None and not self.credential_valid() and not self._expired_reported:
            self._expired_reported = True
            self._log("credential.expired")
            out += self._release("credential_expired")
        out += self._state_if_changed()
        # Only a `state` frame feeds the host's watchdog: an earcon or a
        # play frame in this tick must not suppress the keepalive.
        sent_state = any(header["type"] == "state" for header, _ in out)
        if not sent_state and self.clock.monotonic_ms() - self._last_state_at >= STATE_KEEPALIVE_MS:
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
        if self.pipeline is not None:
            self.pipeline.on_credential(self.token, self.server_url, self.ca_sha256)
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
        if self.pipeline is not None:
            return [*self.pipeline.on_audio(payload, self.mode), *self._state_if_changed()]
        if self.ptt_down and len(self.utterance) + len(payload) <= UTTERANCE_MAX_BYTES:
            self.utterance += payload
        return []

    def _on_playback(self, header, _payload):
        if self.pipeline is None:
            return []  # the shell alone never plays anything
        out = self.pipeline.on_playback(header["utterance_id"], header["state"])
        return [*out, *self._state_if_changed()]

    def _on_mode(self, header, _payload):
        self.mode = header["mode"]
        out = []
        if self.pipeline is not None:
            out = self.pipeline.on_mode(self.mode)
        if self.mode == "off":
            return [*out, *self._release("mode_off"), *self._state_if_changed()]
        return [*out, *self._state_if_changed()]

    def _on_mute(self, header, _payload):
        self.muted = header["muted"]
        if self.pipeline is not None:
            self._pending += self.pipeline.on_mute(self.muted)
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
        if self.pipeline is not None:
            if header["key"] == "down" and not self.consuming():
                return []
            return [*self.pipeline.on_ptt(header["key"]), *self._state_if_changed()]
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
        return [
            encode_frame(header, payload, direction=CHILD_TO_HOST) for header, payload in frames
        ]

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


def run_stdio(
    stdin, stdout, clock, *, core=None, make_pipeline=None, tick_seconds=TICK_SECONDS
) -> int:
    """Serve one host over a pair of binary streams until the core finishes.
    Returns the process exit code. `make_pipeline(post)` builds the voice
    pipeline whose worker posts `(job_id, event)` back into this loop's
    inbox, so the pipeline itself only ever runs on this thread."""
    inbox = queue.Queue()
    if core is None:
        pipeline = None
        if make_pipeline is not None:
            pipeline = make_pipeline(lambda job_id, event: inbox.put(("worker", (job_id, event))))
        core = ChildCore(clock, pipeline=pipeline)
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
        elif kind == "worker":
            send(core.on_worker(*value))
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
