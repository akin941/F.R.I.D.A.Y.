"""The voice pipeline (Phase 11 brief D8, D14): from capture frames to
`play` frames.

```
host audio ─▶ RingBuffer(2 s) ─▶ WakeGate ─▶ TurnCapture(VAD + smart-turn)
          ─▶ FridaySTT ─▶ SelfTriggerFilter ─▶ FridayChatLLM(SSE)
          ─▶ Segmenter ─▶ FridayTTS(lang route) ─▶ play frames ─▶ host
```

`VoicePipeline` is the stage engine the child's protocol shell
(`friday_voice.child.ChildCore`) drives: pure, clock-injected and single-
threaded. It never blocks on the network: each call to Friday is a job run
by a worker (`friday_voice.workers`) whose events come back through
`on_worker`. The stages:

- **WakeGate**, armed only under the D11 arming policy
  (`friday_voice.arming`: local mode `always_on`, the server's policy for
  the loaded model at the package threshold, link up, mute readable and
  off, no storm mute, verified models): the utterance starts at the first
  frame after the detection frame. More than `WAKE_STORM_MAX` wakes in
  `WAKE_STORM_WINDOW_S` without a dispatched turn is a wake storm:
  `storm_muted`, `release_mic`, re-armed only by a mute on -> off cycle
  (D12);
- **TurnCapture**: push-to-talk (key down -> key up) or VAD + smart-turn;
- **FridaySTT**: the utterance as an in-memory WAV to
  `/api/v1/voice/transcriptions` with `X-Friday-Trace` and
  `X-Friday-Voice-Trigger`; an empty transcript ends the turn silently;
- **SelfTriggerFilter** (`friday_voice.self_trigger`, D10): a transcript
  that matches what Friday spoke in the last 60 s is discarded (content
  rule); a wake while playback runs or within 1.5 s after it, and every
  barge-in, is *suspect* (window rule). Discarded: no chat turn, nothing
  spoken, `wake_discards_today` + 1, the session stays open;
- **FridayChatLLM**: the transcript as a voice turn on Friday's ordinary
  chat endpoint (`input_mode: "voice"`), the SSE reply consumed as it
  streams: `chunk` feeds the segmenter, `draft` queues "Kart ekranda."
  after the reply, `done`/`error` end it. Never a provider;
- **Segmenter** + **language**: sentences of 20-300 characters, each with
  its own `tr`/`en` route;
- **FridayTTS**: one sentence per request, one in flight plus one
  prefetched (Phase 6 D7); the WAV is normalised to 16 kHz mono PCM16 and
  sent as one `play` utterance per sentence.
- **BargeInMonitor** (`friday_voice.barge_in`, D9): while Friday speaks,
  the VAD scores the AEC-cleaned capture; speech >= 0.7 for >= 250 ms,
  later than 300 ms into playback, stops the reply at once
  (`stop_playback` for every open utterance, the TTS jobs and the chat
  stream cancelled), records `interrupted_at_ms` and starts a new
  utterance (trigger `barge_in`) with a 300 ms pre-roll before the speech
  onset -- never reaching back before the session. With wake mode armed
  the wake detector hears the same signal, and a detection while Friday
  speaks interrupts the same way (suspect, D10).

Errors (D8): a 401 on a request sent before a newer credential arrived is
retried once with the new one, the mic kept; any other 401/403 releases
the mic (`credential_refused`: the host mints a new credential) and shows
`error` until a new credential arrives; 423 `voice.live_muted` ->
`storm_muted` (re-armed by a mute on -> off cycle on the Mac); 403
`voice.always_on_not_permitted` -> wake mode disarmed (`armed_ptt`); 429
and any other failure -> the error earcon, nothing spoken; a network error
is retried once for STT and TTS and never for the chat turn (a turn is
never sent twice).

Sessions (D12): a live session opens with the first turn's
`POST /voice/live/sessions` and closes after `SILENCE_CLOSE_S` without
activity or `SESSION_MAX_MINUTES` after it opened (at the end of the turn
then running), with the `end` earcon; the conversation id is kept for
reuse (D5). `link: down` closes it silently with the mic release.

Nothing here logs a transcript, reply text, audio or the token.
"""

import uuid

from . import config, language, log
from .arming import AllowAlwaysOn, ArmingPolicy, RefuseAlwaysOn
from .audio import encode_wav, play_chunks
from .barge_in import BargeInMonitor
from .client import CODE_ALWAYS_ON_NOT_PERMITTED, CODE_LIVE_MUTED, CODE_PIN, FridayError
from .policy import policy_job, poll_due, request_policy
from .segmenter import SentenceSegmenter
from .self_trigger import SelfTriggerFilter
from .session import derive_state
from .turn import END, NO_SPEECH, TurnCapture
from .wake import WakeGate
from .workers import Closer, Delay

__all__ = [
    "AllowAlwaysOn",
    "ArmingPolicy",
    "RefuseAlwaysOn",
    "VoicePipeline",
    "request_policy",
]

CAPTURING = "capturing"
SESSION = "session"
TRANSCRIBING = "transcribing"
REPLYING = "replying"


# --- jobs (run by a worker; see friday_voice.workers) ---


def _rotation_retry(error, client, rotated):
    """D8: a 401 on a request sent with a credential that has since been
    replaced by a newer `credential` frame is retried once."""
    return error.status == 401 and not rotated and client.generation != error.generation


def call_with_retries(client, call, *, network_retries):
    """Run `call()` (a client method returning a Reply) under the D8 retry
    rules. A generator: yields the scripted latency as a Delay, returns the
    Reply."""
    rotated = False
    retries = network_retries
    while True:
        try:
            reply = call()
        except FridayError as error:
            yield Delay(error.latency_ms)
            if _rotation_retry(error, client, rotated):
                rotated = True
                continue
            if error.network and retries > 0:
                retries -= 1
                continue
            raise
        yield Delay(reply.latency_ms)
        return reply


def session_job(client, trigger):
    reply = yield from call_with_retries(
        client, lambda: client.open_session(trigger), network_retries=1
    )
    yield {"kind": "session", **reply.data}


def stt_job(client, conversation_id, wav, trace_id, trigger):
    reply = yield from call_with_retries(
        client,
        lambda: client.transcribe(conversation_id, wav, trace_id, trigger),
        network_retries=config.STT_RETRIES,
    )
    yield {"kind": "transcript", **reply.data}


def chat_job(client, conversation_id, text, trace_id):
    # A rotation 401 means the turn was refused before it ran: one retry.
    # A network error is never retried: the turn might have been sent.
    stream = yield from call_with_retries(
        client, lambda: client.chat(conversation_id, text, trace_id), network_retries=0
    )
    yield Closer(stream.close)
    yield {"kind": "chat_open"}
    for latency, event in stream:
        yield Delay(latency)
        yield {"kind": "chat", "event": event}
    yield {"kind": "chat_end"}


def tts_job(client, conversation_id, text, lang, trace_id, timings, index):
    # A cancelled synthesis (barge-in, abort) is reported to a client that
    # can stop it; the HTTP client simply drops the late response.
    abort = getattr(client, "abort_speech", None)
    if abort is not None:
        yield Closer(lambda: abort(trace_id, index))
    reply = yield from call_with_retries(
        client,
        lambda: client.synthesize(conversation_id, text, lang, trace_id, timings),
        network_retries=config.TTS_RETRIES,
    )
    yield {"kind": "audio", "index": index, "wav": reply.data}


# --- the turn ---


class TurnTimer:
    """One turn's marks on the monotonic clock and the stage timings the
    child reports with its speech requests (D14; the browser's TurnTimer).
    Timings only: no text, no audio."""

    MAX_STAGE_MS = 600_000

    def __init__(self, clock):
        self.clock = clock
        self.marks = {}
        # Reported values that are not spans of this turn's marks:
        # `interrupted_at_ms` (D9, D14).
        self.extra = {}

    def mark(self, name, *, overwrite=False):
        if overwrite or name not in self.marks:
            self.marks[name] = self.clock.monotonic_ms()

    def span(self, start, end):
        if start not in self.marks or end not in self.marks:
            return None
        value = self.marks[end] - self.marks[start]
        return value if 0 <= value <= self.MAX_STAGE_MS else None

    def timings(self) -> dict:
        """What the speech request carries (`VoiceTurnTrace` client stages)."""
        out = {
            "end_of_turn_ms": self.span("released", "turn_end"),
            "llm_first_sentence_ms": self.span("dispatched", "first_sentence"),
            "playback_start_ms": self.span("released", "playback_start"),
            "total_ms": self.span("released", "last_request"),
            "wake_to_listen_ms": self.span("wake", "listen"),
            "interrupted_at_ms": self.extra.get("interrupted_at_ms"),
        }
        return {key: value for key, value in out.items() if value is not None}

    def stages(self) -> dict:
        """The replay report's per-stage numbers. `server_ms` is the time
        spent waiting on Friday on the critical path to the first audio;
        `overhead_ms` is everything else (what this pipeline controls)."""
        out = {
            "end_of_turn_ms": self.span("released", "turn_end"),
            "session_ms": self.span("session_sent", "session_done"),
            "stt_ms": self.span("stt_sent", "transcribed"),
            "llm_first_sentence_ms": self.span("dispatched", "first_sentence"),
            "tts_first_ms": self.span("tts_first_sent", "first_audio"),
            "playback_start_ms": self.span("released", "playback_start"),
        }
        server = [out[k] for k in ("session_ms", "stt_ms", "llm_first_sentence_ms", "tts_first_ms")]
        if out["playback_start_ms"] is not None:
            out["server_ms"] = sum(value or 0 for value in server)
            out["overhead_ms"] = out["playback_start_ms"] - out["server_ms"]
        return {key: value for key, value in out.items() if value is not None}


class Turn:
    def __init__(self, number, trigger, capture, clock, trace_id):
        self.number = number
        self.trigger = trigger
        self.capture = capture
        self.phase = CAPTURING
        self.trace_id = trace_id
        self.timer = TurnTimer(clock)
        self.jobs = set()
        self.wav = None
        self.transcript_language = ""
        self.segmenter = SentenceSegmenter()
        self.sentences = []  # (text, language)
        self.next_tts = 0
        self.tts_job = None
        self.utterances = {}  # utterance_id -> sentence index
        self.ended = set()  # sentence indexes whose playback ended or stopped
        self.open_utterances = set()  # sent to the host, not ended
        self.draft = False
        self.chat_done = False
        self.playing = False
        # D10 window rule: a wake while playback ran (or within 1.5 s after
        # it), and every barge-in.
        self.suspect = False
        self.interrupted = False

    def utterance_id(self, index):
        return f"t{self.number}s{index}"


class VoicePipeline:
    def __init__(
        self,
        clock,
        client,
        worker,
        *,
        vad=None,
        turn_end=None,
        wake_detector=None,
        arming=None,
        self_trigger=None,
        new_trace_id=None,
        log_stream=None,
    ):
        self.clock = clock
        self.client = client
        self.worker = worker
        if hasattr(worker, "deliver"):
            worker.deliver = self.deliver
        self.vad = vad
        self.turn_end = turn_end
        self.wake_gate = WakeGate(wake_detector, clock) if wake_detector is not None else None
        self.arming = arming or RefuseAlwaysOn()
        # D10: on unless a test's negative control passes `self_trigger=False`.
        if isinstance(self_trigger, SelfTriggerFilter):
            self.self_trigger = self_trigger
        else:
            self.self_trigger = SelfTriggerFilter(clock, enabled=self_trigger is not False)
        # D9: barge-in needs the VAD and the turn-end detector of a
        # VAD capture (always the case once the detectors are loaded).
        self.barge_in = (
            BargeInMonitor(vad, clock) if vad is not None and turn_end is not None else None
        )
        self.new_trace_id = new_trace_id or (lambda: str(uuid.uuid4()))
        self.log_stream = log_stream
        self.core = None  # set by ChildCore
        self.turn = None
        self.turns = 0
        self.completed = []  # finished turns (the replay report reads their timers)
        # Every barge-in: {"via", "onset_ms", "fired_ms"} (monotonic; timings only).
        self.barge_ins = []
        self.conversation_id = None
        self.session_id = None
        self.last_activity_ms = None
        self.refused = False  # a credential was refused: error until a new one
        self.storm_muted = False
        self._storm_unmute_seen = False
        self.wake_refused = False  # the server's 403 voice.always_on_not_permitted
        self.error_until = None
        self.policy_job = None  # the in-flight voice-policy read (D11)
        self._policy_failed = False
        self.wake_times = []  # monotonic ms of recent wakes without a dispatched turn (D12)
        self.session_started_ms = None  # the open live session's start (D12)
        self._outbox = []

    # --- wiring ---

    def attach(self, core):
        self.core = core

    def _log(self, code, detail=""):
        log.emit(code, detail, stream=self.log_stream)

    def _send(self, header, payload=b""):
        self._outbox.append((header, payload))

    def _drain(self):
        out, self._outbox = self._outbox, []
        return out

    def deliver(self, job_id, event):
        """A worker event, outside any host frame (SimWorker): processed at
        once, frames queued for the core to flush."""
        frames = self.on_worker(job_id, event)
        if self.core is not None:
            self.core.flush_pipeline(frames)

    def _submit(self, job):
        job_id = self.worker.submit(job)
        if self.turn is not None:
            self.turn.jobs.add(job_id)
        return job_id

    # --- what the shell asks ---

    def _session_expired(self) -> bool:
        """D12: `SILENCE_CLOSE_S` without activity, or `SESSION_MAX_MINUTES`
        since the session opened."""
        if self.session_started_ms is None or self.last_activity_ms is None:
            return True
        now = self.clock.monotonic_ms()
        if now - self.last_activity_ms >= config.SILENCE_CLOSE_S * 1000:
            return True
        return now - self.session_started_ms >= config.SESSION_MAX_MINUTES * 60_000

    @property
    def session_open(self) -> bool:
        """A live session is open while a turn runs and until it expires
        (D12). The conversation id is kept for reuse either way (D5)."""
        if self.conversation_id is None or self.session_started_ms is None:
            return False
        if self.turn is not None:
            return True
        return not self._session_expired()

    def accepting(self) -> bool:
        return not (self.refused or self.storm_muted)

    # --- the arming policy (D11) ---

    def _host(self, name, default):
        return getattr(self.core, name) if self.core is not None else default

    def wake_capable(self) -> bool:
        return self.wake_gate is not None and self.vad is not None and self.turn_end is not None

    def wake_armed(self, mode) -> bool:
        """D11 (a)-(f): local mode `always_on`, the server's policy for the
        loaded model, link up, mute readable and off, no storm mute, the
        models verified (the arming policy's `permitted()` holds (b) and
        (f))."""
        return (
            mode == "always_on"
            and self.wake_capable()
            and not self.wake_refused
            and not self.storm_muted
            and self._host("link_up", True) is True
            and self._host("muted", False) is False
            and self.arming.permitted()
        )

    def _maybe_poll(self, *, force=False):
        """Read the voice policy when wake mode could arm: a wake detector
        is loaded, the arming policy polls, the local mode is `always_on`
        and a credential is held. `force` at a session start and a mode
        change to `always_on`; after a failed read every `POLICY_RETRY_S`
        (disarmed meanwhile); otherwise every `POLICY_POLL_S`."""
        if not getattr(self.arming, "polls", False) or not self.wake_capable():
            return
        if self.policy_job is not None or self.refused:
            return
        if self._host("mode", None) != "always_on":
            return
        if self.core is not None and not self.core.credential_valid():
            return
        now = self.clock.monotonic_ms()
        due = poll_due(self.arming.checked_at_ms, now)
        retry = self._policy_failed and (
            self.arming.checked_at_ms is None
            or now - self.arming.checked_at_ms >= config.POLICY_RETRY_S * 1000
        )
        if not (force or due or retry):
            return
        self.policy_job = self.worker.submit(
            policy_job(self.client, self.wake_gate.detector.model_sha256)
        )

    def _disarm(self, reason):
        """The policy said no (or could not be read): wake mode is off at
        once; a wake utterance still being captured is dropped."""
        if self.wake_gate is not None:
            self.wake_gate.reset()
        turn = self.turn
        if turn is not None and turn.trigger == "wake" and turn.phase == CAPTURING:
            self._abort_turn(reason)

    def _on_policy(self, event):
        now = self.clock.monotonic_ms()
        if event["kind"] == "policy":
            self._policy_failed = False
            if self.arming.record(event["data"], now):
                self.wake_refused = False
                self._log("policy.permitted")
            else:
                self._log("policy.refused", ",".join(self.arming.server_reasons)[:80])
                self._disarm("policy_refused")
            return
        error = event.get("error")
        code = error.code if isinstance(error, FridayError) else type(error).__name__
        self._log("policy.read_failed", str(code)[:40])
        self._policy_failed = True
        self.arming.refuse("read_failed", now)
        self._disarm("policy_unreadable")

    def on_mode(self, mode):
        """The host's local mode changed (ADR-013 decision 5: only the Mac
        sets it). To `always_on`: read the policy now; away from it: a wake
        utterance still being captured is dropped."""
        if mode == "always_on":
            self._maybe_poll(force=True)
        else:
            self._disarm("mode_changed")
        return self._drain()

    def state_inputs(self, mode) -> dict:
        """The pipeline's inputs to `friday_voice.session.derive_state`."""
        turn = self.turn
        return {
            "storm_muted": self.storm_muted,
            "refused": self.refused,
            "error_hold": (
                self.error_until is not None and self.clock.monotonic_ms() < self.error_until
            ),
            "turn_phase": turn.phase if turn is not None else None,
            "playing": bool(turn is not None and turn.playing),
            "wake_armed": self.wake_armed(mode),
        }

    def state(self, mode):
        """The state with no shell blocking rule in force."""
        return derive_state(**self.state_inputs(mode))

    # --- inbound from the shell ---

    def on_credential(self, token, server_url, ca_sha256):
        self.client.set_credential(token, server_url, ca_sha256)
        self.refused = False
        # The first credential reads the policy; a new one after a failed
        # read (a refused token, say) reads it again at once.
        self._maybe_poll(force=self._policy_failed)
        return self._drain()

    def on_mute(self, muted):
        if self.storm_muted:
            if muted is True:
                self._storm_unmute_seen = True
            elif muted is False and self._storm_unmute_seen:
                self.storm_muted = False
                self._storm_unmute_seen = False
                self._log("storm.unmuted")
        return self._drain()

    def on_audio(self, pcm, mode):
        turn = self.turn
        if turn is None:
            if self.wake_armed(mode) and self.wake_gate.feed(pcm):
                self._on_wake()
            return self._drain()
        if turn.phase != CAPTURING:
            self._monitor(pcm, mode)
            return self._drain()
        turn.timer.mark("listen")
        before = turn.capture.last_speech_ms
        result = turn.capture.feed(pcm)
        if turn.capture.last_speech_ms != before:
            turn.timer.mark("released", overwrite=True)
        if result == END:
            if turn.capture.reason != "smart_turn" and "released" not in turn.timer.marks:
                turn.timer.mark("released")
            self._end_capture()
        elif result == NO_SPEECH:
            self._log("turn.no_speech")
            self.turn = None
        return self._drain()

    def _count(self, name):
        if self.core is not None:
            self.core.counters[name] = min(self.core.counters[name] + 1, 100_000)

    def _on_wake(self):
        now = self.clock.monotonic_ms()
        window = config.WAKE_STORM_WINDOW_S * 1000
        self.wake_times = [t for t in self.wake_times if now - t < window] + [now]
        if len(self.wake_times) > config.WAKE_STORM_MAX:
            self._wake_storm()
            return
        self._count("wake_accepts_today")
        suspect = self.self_trigger.suspect_at(self.clock.monotonic_ms())
        capture = TurnCapture(mode="vad", vad=self.vad, turn_end=self.turn_end)
        self._new_turn("wake", capture)
        self.turn.suspect = suspect
        self.turn.timer.mark("wake")
        self._send({"type": "earcon", "earcon": "wake"})

    # --- barge-in (D9) ---

    def _monitor(self, pcm, mode):
        """A consumed frame while the turn is past its capture: the reply
        phase feeds the barge-in monitor (and, with wake mode armed, the
        wake detector) on the AEC-cleaned signal; other phases drop it."""
        turn = self.turn
        if self.barge_in is None or turn.phase != REPLYING:
            return
        if not turn.playing:
            self.barge_in.remember(pcm)
            return
        if self.wake_armed(mode) and self.wake_gate.feed(pcm):
            # D10: the detector is fed the echo-cancelled signal while
            # Friday speaks; a hit interrupts like a barge-in and is suspect.
            self._count("wake_accepts_today")
            now = self.clock.monotonic_ms()
            self._record_barge_in("wake", now, now)
            self._interrupt("wake")
            self.turn.timer.mark("wake")
            self._send({"type": "earcon", "earcon": "wake"})
            return
        event = self.barge_in.feed(pcm)
        if event is not None:
            self._record_barge_in("vad", event.onset_ms, event.fired_ms)
            self._interrupt("vad", preroll=event.preroll)

    def _record_barge_in(self, via, onset_ms, fired_ms):
        self.barge_ins.append({"via": via, "onset_ms": onset_ms, "fired_ms": fired_ms})
        self.barge_ins = self.barge_ins[-50:]

    def _interrupt(self, via, *, preroll=b""):
        """Stop the reply now and listen to the owner (D9): stop_playback
        first, then the TTS jobs and the chat stream are cancelled; the new
        utterance (trigger `barge_in`, suspect) starts with the pre-roll."""
        old = self.turn
        interrupted_at = self._interrupted_at(old)
        self._abort_turn(f"barge_in_{via}")
        old.interrupted = True
        self.completed.append(old)
        self.completed = self.completed[-50:]
        self.last_activity_ms = self.clock.monotonic_ms()
        capture = TurnCapture(
            mode="vad",
            vad=self.vad,
            turn_end=self.turn_end,
            preroll=preroll,
            preroll_speech=bool(preroll),
        )
        self._new_turn("barge_in", capture)
        turn = self.turn
        turn.suspect = True
        if interrupted_at is not None:
            turn.timer.extra["interrupted_at_ms"] = interrupted_at
        turn.timer.mark("listen")
        if preroll:
            turn.timer.mark("released")

    def _interrupted_at(self, turn):
        """`interrupted_at_ms` (D14): ms after the turn's playback started,
        recorded on the interrupted turn and carried by the next one (the
        interrupted turn makes no further request to report it with)."""
        started = turn.timer.marks.get("playback_start")
        if started is None:
            return None
        value = min(max(0, self.clock.monotonic_ms() - started), TurnTimer.MAX_STAGE_MS)
        turn.timer.mark("interrupted")
        turn.timer.extra["interrupted_at_ms"] = value
        return value

    def _playing(self, turn, playing):
        """Track the turn's playback edge for the D9 guard and D10 window."""
        if playing == turn.playing:
            return
        turn.playing = playing
        if playing:
            self.self_trigger.playback_started()
            if self.barge_in is not None:
                self.barge_in.playback_started()
        else:
            self.self_trigger.playback_ended()
            if self.barge_in is not None:
                self.barge_in.playback_stopped()

    def _new_turn(self, trigger, capture):
        self.turns += 1
        self.turn = Turn(self.turns, trigger, capture, self.clock, self.new_trace_id())

    def on_ptt(self, key):
        if key == "down":
            interrupted_at = None
            if self.turn is not None:
                old = self.turn
                if old.playing:  # the hotkey cut a reply: an interruption too (D14)
                    interrupted_at = self._interrupted_at(old)
                self._abort_turn("ptt_restart")
            self._new_turn("ptt", TurnCapture(mode="ptt"))
            if interrupted_at is not None:
                self.turn.timer.extra["interrupted_at_ms"] = interrupted_at
            self.turn.timer.mark("listen")
        else:
            turn = self.turn
            if turn is not None and turn.phase == CAPTURING and turn.capture.mode == "ptt":
                turn.timer.mark("released")
                if turn.capture.finish() == END:
                    self._end_capture()
                else:
                    self.turn = None
        return self._drain()

    def on_playback(self, utterance_id, state):
        turn = self.turn
        if turn is None or utterance_id not in turn.utterances:
            return self._drain()
        index = turn.utterances[utterance_id]
        if state == "started":
            self._playing(turn, True)
            turn.timer.mark("playback_start")
        else:
            turn.open_utterances.discard(utterance_id)
            turn.ended.add(index)
            self._playing(turn, bool(turn.open_utterances))
            self._pump()
            self._maybe_finish()
        return self._drain()

    def tick(self):
        self._close_expired_session()
        self._maybe_poll()
        return self._drain()

    def _close_expired_session(self):
        """D12: close the session after `SILENCE_CLOSE_S` of silence or at
        the `SESSION_MAX_MINUTES` cap (never in the middle of a turn), with
        the `end` earcon. The conversation id stays for reuse."""
        if self.session_started_ms is None or self.turn is not None:
            return
        if not self._session_expired():
            return
        self.session_started_ms = None
        self._log("session.closed")
        if self.core is None or self.core.consuming():
            self._send({"type": "earcon", "earcon": "end"})

    def abort(self, reason):
        """A fail-closed input (link down, mute, mode off, shutdown, EOF,
        a protocol error, expiry): cancel the turn, stop its playback.
        `link_down` also closes the session (ADR-013 decision 6), silently:
        the mic is already released."""
        if self.turn is not None:
            self._abort_turn(reason)
        if reason == "link_down":
            self.session_started_ms = None
        return self._drain()

    # --- the turn's steps ---

    def _end_capture(self):
        turn = self.turn
        turn.timer.mark("turn_end")
        turn.wav = encode_wav(bytes(turn.capture.pcm))
        turn.capture.pcm.clear()
        idle = (
            None
            if self.last_activity_ms is None
            else self.clock.monotonic_ms() - self.last_activity_ms
        )
        if self.conversation_id is None or idle is None or self._session_expired():
            turn.phase = SESSION
            turn.timer.mark("session_sent")
            self._submit(session_job(self.client, turn.trigger))
        else:
            self._start_stt()

    def _start_stt(self):
        turn = self.turn
        turn.phase = TRANSCRIBING
        turn.timer.mark("stt_sent")
        wav, turn.wav = turn.wav, None
        self._submit(stt_job(self.client, self.conversation_id, wav, turn.trace_id, turn.trigger))

    def on_worker(self, job_id, event):
        if job_id is not None and job_id == self.policy_job:
            self.policy_job = None
            self._on_policy(event)
            return self._drain()
        turn = self.turn
        if turn is None or job_id not in turn.jobs:
            return self._drain()  # a cancelled or finished turn's late event
        kind = event["kind"]
        if kind == "error":
            turn.jobs.discard(job_id)
            self._on_error(event["error"])
        elif kind == "session":
            turn.jobs.discard(job_id)
            turn.timer.mark("session_done")
            self.conversation_id = str(event["conversation_id"])
            self.session_id = event.get("session_id")
            self.last_activity_ms = self.clock.monotonic_ms()
            self.session_started_ms = self.last_activity_ms
            self._maybe_poll(force=True)  # D11: the policy is re-read at session start
            self._start_stt()
        elif kind == "transcript":
            turn.jobs.discard(job_id)
            self._on_transcript(event)
        elif kind == "chat":
            self._on_chat_event(event["event"])
        elif kind == "chat_end":
            turn.jobs.discard(job_id)
            if not turn.chat_done:
                self._on_chat_event({"type": "error", "error_type": "stream_ended"})
        elif kind == "audio":
            turn.jobs.discard(job_id)
            self._on_audio_ready(event)
        return self._drain()

    def _on_transcript(self, event):
        turn = self.turn
        turn.timer.mark("transcribed")
        text = (event.get("text") or "").strip()
        self.last_activity_ms = self.clock.monotonic_ms()
        if not text:
            self._log("turn.empty_transcript")
            self._finish_turn()
            return
        reason = self.self_trigger.check(text, suspect=turn.suspect)
        if reason is not None:
            # D10: Friday heard itself. No chat turn, nothing spoken.
            self._log("turn.self_trigger_discarded", reason)
            self._count("wake_discards_today")
            self._finish_turn()
            return
        turn.transcript_language = language.normalise(event.get("language"))
        turn.phase = REPLYING
        if self.barge_in is not None:
            self.barge_in.begin_reply()
        turn.timer.mark("dispatched")
        self.wake_times.clear()  # a dispatched turn ends any wake-storm count (D12)
        self._submit(chat_job(self.client, self.conversation_id, text, turn.trace_id))

    def _queue_sentences(self, sentences):
        turn = self.turn
        for sentence in sentences:
            if not turn.sentences:
                turn.timer.mark("first_sentence")
            turn.sentences.append((sentence, language.detect(sentence, turn.transcript_language)))

    def _on_chat_event(self, event):
        turn = self.turn
        kind = event.get("type")
        if turn.chat_done:
            return
        if kind == "chunk":
            self._queue_sentences(turn.segmenter.push(str(event.get("text") or "")))
        elif kind == "draft":
            turn.draft = True
        elif kind == "done":
            turn.chat_done = True
            self._queue_sentences(turn.segmenter.flush())
            if turn.draft:
                self._queue_sentences([config.DRAFT_SENTENCE])
        elif kind == "error":
            turn.chat_done = True
            self._log("chat.error", str(event.get("error_type") or "")[:40])
            self._send({"type": "earcon", "earcon": "error"})
            self.error_until = self.clock.monotonic_ms() + config.ERROR_HOLD_MS
        else:
            return
        self._pump()
        self._maybe_finish()

    def _pump(self):
        """Submit the next TTS request: one in flight, and at most
        `TTS_PREFETCH` sentences ahead of the one playing."""
        turn = self.turn
        if turn is None or turn.tts_job is not None or turn.next_tts >= len(turn.sentences):
            return
        if turn.next_tts > len(turn.ended) + config.TTS_PREFETCH:
            return
        index = turn.next_tts
        turn.next_tts += 1
        text, lang = turn.sentences[index]
        if index == 0:
            turn.timer.mark("tts_first_sent")
        turn.timer.mark("last_request", overwrite=True)
        turn.tts_job = self._submit(
            tts_job(
                self.client,
                self.conversation_id,
                text,
                lang,
                turn.trace_id,
                turn.timer.timings(),
                index,
            )
        )

    def _on_audio_ready(self, event):
        from .audio import decode_wav

        turn = self.turn
        turn.tts_job = None
        index = event["index"]
        try:
            pcm = decode_wav(event["wav"])
        except ValueError:
            self._on_error(FridayError(200, "bad_audio"))
            return
        turn.timer.mark("first_audio")
        self.self_trigger.spoken(turn.sentences[index][0])
        utterance_id = turn.utterance_id(index)
        turn.utterances[utterance_id] = index
        turn.open_utterances.add(utterance_id)
        chunks = play_chunks(pcm)
        for seq, chunk in enumerate(chunks):
            self._send(
                {
                    "type": "play",
                    "utterance_id": utterance_id,
                    "seq": seq,
                    "last": seq == len(chunks) - 1,
                },
                chunk,
            )
        self._pump()

    def _maybe_finish(self):
        turn = self.turn
        if turn is None or not turn.chat_done or turn.tts_job is not None:
            return
        if turn.next_tts < len(turn.sentences) or turn.open_utterances:
            return
        if len(turn.ended) < len(turn.utterances):
            return
        self._finish_turn()

    def _finish_turn(self):
        turn = self.turn
        for job_id in list(turn.jobs):
            self.worker.cancel(job_id)
        self.completed.append(turn)
        self.completed = self.completed[-50:]
        self.last_activity_ms = self.clock.monotonic_ms()
        self.turn = None

    def _abort_turn(self, reason):
        turn = self.turn
        for job_id in list(turn.jobs):
            self.worker.cancel(job_id)
        for utterance_id in sorted(turn.open_utterances):
            self._send({"type": "stop_playback", "utterance_id": utterance_id})
        turn.open_utterances.clear()
        self._playing(turn, False)
        turn.capture.pcm.clear()
        turn.wav = None
        self._log("turn.aborted", reason)
        self.turn = None

    def _on_error(self, error):
        if not isinstance(error, FridayError):
            self._log("turn.failed", type(error).__name__)
            error = FridayError(0, "internal")
        else:
            self._log("friday.error", f"{error.status} {error.code}".strip())
        self._abort_turn("error")
        if error.status == 423 and error.code == CODE_LIVE_MUTED:
            self.storm_muted = True
            self._storm_unmute_seen = False
            if self.core is not None:
                counters = self.core.counters
                counters["storm_mutes_today"] = min(counters["storm_mutes_today"] + 1, 100_000)
            self._release("storm")
        elif error.status == 403 and error.code == CODE_ALWAYS_ON_NOT_PERMITTED:
            # The server's backstop (D11): disarm to push-to-talk until a
            # later policy read permits again.
            self.wake_refused = True
            self.arming.refuse("server_refused_wake", self.clock.monotonic_ms())
            if self.wake_gate is not None:
                self.wake_gate.reset()
        elif error.status in (401, 403):
            self.refused = True
            self._release("credential_refused")
        elif error.code == CODE_PIN:
            self.error_until = self.clock.monotonic_ms() + config.ERROR_HOLD_MS
            self._send({"type": "earcon", "earcon": "error"})
            self._release("error")
        else:  # 429 (budget, throttle), 5xx, network: the earcon only
            self.error_until = self.clock.monotonic_ms() + config.ERROR_HOLD_MS
            self._send({"type": "earcon", "earcon": "error"})

    def _wake_storm(self):
        """D12 client rule: more than `WAKE_STORM_MAX` wakes within
        `WAKE_STORM_WINDOW_S` without a dispatched turn. Stop consuming
        audio, release the mic, show `storm_muted` until a mute on -> off
        cycle on the Mac."""
        self.wake_times.clear()
        self.storm_muted = True
        self._storm_unmute_seen = False
        if self.wake_gate is not None:
            self.wake_gate.reset()
        if self.core is not None:
            counters = self.core.counters
            counters["storm_mutes_today"] = min(counters["storm_mutes_today"] + 1, 100_000)
        self._log("storm.wake")
        self._release("storm")

    def _release(self, reason):
        if self.core is not None:
            self.core.drop_buffers()
        self._send({"type": "release_mic", "reason": reason})
