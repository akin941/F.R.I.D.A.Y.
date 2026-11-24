"""The reference parent double (Phase 11 brief D6): a Python implementation
of the Swift host's side of `friday-voice/v1`. It is the executable spec the
Swift `VoiceChildHost` mirrors (Mac session M1).

`HostCore` is pure and clock-injected: feed it the bytes the child wrote
(`child_bytes`), the child's exit (`child_exited`), local events (mode,
mute, link, push-to-talk) and capture frames (`feed_audio`); read what it
would write to the child from `take_outbound()` and what it shows from
`mic_held` / `indicator`. `ProcessHost` binds it to a real child process.

Host rules (D6, D12), each pinned by a host conformance case:
- the child's first frame must be `ready`; then `state` at least every
  WATCHDOG_MS, else the watchdog fires;
- a malformed, over-length or unknown frame, a policy-shaped name (refused
  by the frozen sets and `additionalProperties: false`), odd-length PCM, a
  missing `state` for 10 s and the child's exit all mean: release the mic,
  show `off`, drop the link and restart the child with backoff 1 s -> 60 s;
- `play` must open an utterance at seq 0 and continue it seq by seq; a
  frame for an unknown, closed or out-of-order utterance is dropped and
  counted, the link stays up;
- the host releases the mic itself on its own fail-closed inputs (node
  link down, mute on or unreadable, mode off) whatever the child does, and
  only (re)acquires it when the child reports an armed or listening state
  while the local mode, mute and link allow it;
- capture frames reach the child only while the mic is held.
"""

import subprocess
import threading
import wave

from ..protocol.framing import FrameDecoder, decode_frame, encode_frame
from ..protocol.messages import CHILD_TO_HOST, HOST_TO_CHILD, PROTOCOL, ProtocolError, validate

WATCHDOG_MS = 10_000
BACKOFF_START_MS = 1_000
BACKOFF_MAX_MS = 60_000
BACKOFF_RESET_AFTER_MS = 60_000  # a child healthy this long restarts the backoff
ACQUIRE_STATES = frozenset({"armed_ptt", "armed_wake", "listening", "thinking", "speaking"})
FRAME_MS = 20
FRAME_BYTES = 16000 * 2 * FRAME_MS // 1000  # 640 bytes of 16 kHz PCM16


class HostCore:
    def __init__(
        self,
        clock,
        *,
        credential=None,
        mode="push_to_talk",
        muted=False,
        link_up=True,
        host_version="0.1.0-reference",
        echo=None,
    ):
        self.clock = clock
        self.credential = credential
        self.mode = mode
        self.muted = muted
        self.link_up = link_up
        self.host_version = host_version
        self.echo = echo
        self.mic_held = False
        self.indicator = "off"
        self.child_running = False
        self.child_ready = False
        self.link_open = False
        self.child_state = None
        self.backoff_ms = BACKOFF_START_MS
        self.restart_at = None
        self.restart_in_ms = None
        self.starts = 0
        self.failures = []  # (code, at) -- protocol codes, "watchdog", "child_exit"
        # Every reason the host released the mic: a local event ("muted"),
        # "child:<reason>" for the child's release_mic, "failure:<code>" when
        # the host failed the child closed.
        self.releases = []
        self.dropped_play = 0
        self.dropped_stop = 0
        self.played = []  # (utterance_id, seq, pcm) accepted for playback
        self.earcons = []
        self.logs = []
        self.audio_sent = 0
        self.frames_received = 0
        self._utterances = {}  # utterance_id -> next expected seq (open)
        self._closed = set()
        self._outbound = []
        self._decoder = None
        self._ready_at = None
        self._last_state_at = None
        self._seq = 0
        self.stopping = False

    # --- lifecycle ---

    @property
    def last_failure(self):
        return self.failures[-1][0] if self.failures else None

    def start_child(self):
        """The driver spawned a fresh child: greet it."""
        self.starts += 1
        self.child_running = True
        self.child_ready = False
        self.link_open = True
        self.child_state = None
        self.restart_at = None
        self.restart_in_ms = None
        self._decoder = FrameDecoder()
        self._utterances.clear()
        self._closed.clear()
        self._last_state_at = self.clock.monotonic_ms()
        self._send(
            {
                "type": "hello",
                "protocol": PROTOCOL,
                "host_version": self.host_version,
                "sample_rate": 16000,
                "mode": self.mode,
                "muted": self.muted,
                "link_up": self.link_up,
            }
        )
        if self.credential is not None:
            self.send_credential(self.credential)

    def send_credential(self, credential):
        self.credential = credential
        self._send({"type": "credential", **credential})

    def restart_due(self) -> bool:
        return (
            not self.child_running
            and self.restart_at is not None
            and self.clock.monotonic_ms() >= self.restart_at
        )

    def child_exited(self, code=0):
        if not self.child_running:
            return
        if self.stopping:
            self._stopped()
            return
        self._fail("child_exit")

    def shutdown(self):
        """The host is quitting: tell the child, release, and treat its
        exit as expected (no restart)."""
        self.stopping = True
        if self.link_open:
            self._send({"type": "shutdown"})
        self._release("shutdown")
        self.indicator = "off"

    def _stopped(self):
        self.link_open = False
        self.child_running = False
        self.child_ready = False

    # --- outbound ---

    def _send(self, header, payload=b""):
        if not self.link_open:
            return False
        self._outbound.append(encode_frame(header, payload, direction=HOST_TO_CHILD))
        return True

    def take_outbound(self) -> list:
        frames, self._outbound = self._outbound, []
        return frames

    # --- the mic ---

    def _local_ok(self) -> bool:
        return self.mode != "off" and self.muted is False and self.link_up

    def _release(self, reason):
        self.releases.append(reason)
        self.mic_held = False

    def _maybe_acquire(self):
        if (
            not self.mic_held
            and not self.stopping
            and self.child_ready
            and self.link_open
            and self.child_state in ACQUIRE_STATES
            and self._local_ok()
        ):
            self.mic_held = True

    def _fail(self, code):
        """Fail closed on the child: release, show off, drop the link,
        schedule a restart with backoff."""
        self.failures.append((code, self.clock.monotonic_ms()))
        self._release(f"failure:{code}")
        self.indicator = "off"
        self.link_open = False
        self.child_running = False
        self.child_ready = False
        self._outbound.clear()
        if self.echo is not None:
            self.echo.stopped()
        self.restart_in_ms = self.backoff_ms
        self.restart_at = self.clock.monotonic_ms() + self.backoff_ms
        self.backoff_ms = min(self.backoff_ms * 2, BACKOFF_MAX_MS)

    # --- inbound from the child ---

    def child_bytes(self, data):
        if not self.link_open:
            return
        frames, error = self._decoder.feed(data)
        self.frames_received += len(frames) + bool(error)
        for header, payload in frames:
            if not self.link_open:
                return
            try:
                validate(header, CHILD_TO_HOST)
                self._handle(header, payload)
            except ProtocolError as exc:
                self._fail(exc.code)
                return
        if error and self.link_open:
            self._fail(error)

    def child_stream_closed(self):
        """The child's stdout ended: bytes left inside a frame are a short
        read; a clean end is the child exiting."""
        if not self.link_open:
            return
        error = self._decoder.close()
        if self.stopping and not error:
            self._stopped()
            return
        self._fail(error or "child_exit")

    def _handle(self, header, payload):
        message_type = header["type"]
        if not self.child_ready:
            if message_type != "ready":
                raise ProtocolError("ready_expected")
            self.child_ready = True
            self._ready_at = self.clock.monotonic_ms()
            return
        if message_type == "ready":
            raise ProtocolError("duplicate_ready")
        getattr(self, f"_on_{message_type}")(header, payload)

    def _on_state(self, header, _payload):
        self.child_state = header["state"]
        self._last_state_at = self.clock.monotonic_ms()
        if self._local_ok():
            self.indicator = self.child_state
        self._maybe_acquire()

    def _on_play(self, header, payload):
        utterance_id, seq = header["utterance_id"], header["seq"]
        expected = self._utterances.get(utterance_id)
        if expected is None:
            if seq != 0 or utterance_id in self._closed:
                self.dropped_play += 1
                return
            expected = 0
        if seq != expected:
            self.dropped_play += 1
            return
        self.played.append((utterance_id, seq, payload))
        if self.echo is not None and payload:
            self.echo.played(payload)
        if seq == 0:
            self._send(
                {
                    "type": "playback",
                    "utterance_id": utterance_id,
                    "state": "started",
                    "at_ms": self.clock.now_ms(),
                }
            )
        if header["last"]:
            self._utterances.pop(utterance_id, None)
            self._closed.add(utterance_id)
            self._send(
                {
                    "type": "playback",
                    "utterance_id": utterance_id,
                    "state": "ended",
                    "at_ms": self.clock.now_ms(),
                }
            )
        else:
            self._utterances[utterance_id] = seq + 1

    def _on_stop_playback(self, header, _payload):
        utterance_id = header["utterance_id"]
        if utterance_id not in self._utterances:
            self.dropped_stop += 1
            return
        del self._utterances[utterance_id]
        self._closed.add(utterance_id)
        if self.echo is not None:
            self.echo.stopped()
        self._send(
            {
                "type": "playback",
                "utterance_id": utterance_id,
                "state": "stopped",
                "at_ms": self.clock.now_ms(),
            }
        )

    def _on_earcon(self, header, _payload):
        self.earcons.append(header["earcon"])

    def _on_release_mic(self, header, _payload):
        self._release(f"child:{header['reason']}")

    def _on_log(self, header, _payload):
        self.logs.append(header["code"])

    # --- local events (the node UI, the hotkey, the node WebSocket) ---

    def set_link(self, up):
        self.link_up = bool(up)
        if not self.link_up:
            self._release("link_down")
            self.indicator = "off"
        self._send({"type": "link", "state": "up" if up else "down"})

    def set_mute(self, muted):
        self.muted = muted
        if muted is not False:
            self._release("mute_unreadable" if muted == "unreadable" else "muted")
            self.indicator = "muted"
        self._send({"type": "mute", "muted": muted})

    def set_mode(self, mode):
        self.mode = mode
        if mode == "off":
            self._release("mode_off")
            self.indicator = "off"
        self._send({"type": "mode", "mode": mode})

    def ptt(self, key):
        if self.child_ready:
            self._send({"type": "ptt", "key": key})

    def feed_audio(self, pcm, *, aec="on") -> bool:
        """One captured frame. Reaches the child only while the mic is held;
        the echo double mixes in what was played."""
        if not (self.mic_held and self.child_ready and self.link_open):
            return False
        if self.echo is not None:
            pcm = self.echo.mix(pcm)
        sent = self._send(
            {
                "type": "audio",
                "seq": self._seq,
                "captured_at_ms": self.clock.now_ms(),
                "aec": aec,
            },
            pcm,
        )
        if sent:
            self._seq = (self._seq + 1) % 4294967296
            self.audio_sent += 1
        return sent

    # --- time ---

    def tick(self):
        now = self.clock.monotonic_ms()
        if self.child_running and now - self._last_state_at >= WATCHDOG_MS:
            self._fail("watchdog")
            return
        if (
            self.child_ready
            and self._ready_at is not None
            and now - self._ready_at >= BACKOFF_RESET_AFTER_MS
        ):
            self.backoff_ms = BACKOFF_START_MS


def wav_frames(path, *, frame_ms=FRAME_MS):
    """Split a 16 kHz mono PCM16 WAV fixture into capture frames (the last
    one zero-padded). Fixtures are synthetic (tones, silence) and read from
    disk only; the host double never writes audio anywhere."""
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("a fixture is 16 kHz mono PCM16")
        data = wav.readframes(wav.getnframes())
    size = 16000 * 2 * frame_ms // 1000
    frames = []
    for start in range(0, len(data), size):
        chunk = data[start : start + size]
        frames.append(chunk + b"\x00" * (size - len(chunk)))
    return frames


def feed_wav(core, path, *, frame_ms=FRAME_MS, clock=None):
    """Feed a fixture through `core.feed_audio` at an accelerated clock
    (`clock.advance` per frame when given). Returns how many frames reached
    the child."""
    sent = 0
    for frame in wav_frames(path, frame_ms=frame_ms):
        sent += bool(core.feed_audio(frame))
        if clock is not None:
            clock.advance(frame_ms)
            core.tick()
    return sent


class ProcessHost:
    """Drive a real child process (`friday-voice run`, or the hostile
    `friday-voice conformance-child`) with a HostCore over its stdio pipes.
    Used by the runtime tests and the live conformance runs; the clock is
    the caller's (a RealClock for a live child)."""

    def __init__(self, core, argv, *, env=None, cwd=None):
        self.core = core
        self.argv = list(argv)
        self.env = env
        self.cwd = cwd
        self.process = None
        self.lock = threading.RLock()
        self.stderr_lines = []
        self.sent_types = []  # every frame type written to the child, in order
        self._reader = None

    def start(self):
        self.process = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
        )
        with self.lock:
            self.core.start_child()
            self.flush()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def _read_stdout(self):
        stream = self.process.stdout
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(4096)
            with self.lock:
                if not chunk:
                    self.core.child_stream_closed()
                    self._after_failure()
                    return
                self.core.child_bytes(chunk)
                if not self.core.link_open:
                    self._after_failure()
                    return
                self.flush()

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line.decode("utf-8", "replace").rstrip("\n"))

    def _after_failure(self):
        """The link is dropped: close the child's stdin and stop it."""
        try:
            self.process.stdin.close()
        except OSError:
            pass

    def flush(self):
        frames = self.core.take_outbound()
        if not frames or self.process is None:
            return
        self.sent_types += [decode_frame(frame)[0]["type"] for frame in frames]
        try:
            for frame in frames:
                self.process.stdin.write(frame)
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass

    def call(self, method, *args, **kwargs):
        """Run one HostCore method under the lock and flush its frames."""
        with self.lock:
            result = getattr(self.core, method)(*args, **kwargs)
            self.flush()
            return result

    def stop(self, timeout=5):
        if self.process is None:
            return None
        with self.lock:
            if self.core.link_open:
                self.core.shutdown()
                self.flush()
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(timeout=timeout)
