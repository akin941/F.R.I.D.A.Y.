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

- **WakeGate** (wired; the arming policy refuses always-on until branch
  5): the utterance starts at the first frame after the detection frame;
- **TurnCapture**: push-to-talk (key down -> key up) or VAD + smart-turn;
- **FridaySTT**: the utterance as an in-memory WAV to
  `/api/v1/voice/transcriptions` with `X-Friday-Trace` and
  `X-Friday-Voice-Trigger`; an empty transcript ends the turn silently;
- **SelfTriggerFilter**: a hook (`self_trigger`), pass-through until
  branch 4 adds the window and content rules;
- **FridayChatLLM**: the transcript as a voice turn on Friday's ordinary
  chat endpoint (`input_mode: "voice"`), the SSE reply consumed as it
  streams: `chunk` feeds the segmenter, `draft` queues "Kart ekranda."
  after the reply, `done`/`error` end it. Never a provider;
- **Segmenter** + **language**: sentences of 20-300 characters, each with
  its own `tr`/`en` route;
- **FridayTTS**: one sentence per request, one in flight plus one
  prefetched (Phase 6 D7); the WAV is normalised to 16 kHz mono PCM16 and
  sent as one `play` utterance per sentence.

Errors (D8): a 401 on a request sent before a newer credential arrived is
retried once with the new one, the mic kept; any other 401/403 releases
the mic (`credential_refused`: the host mints a new credential) and shows
`error` until a new credential arrives; 423 `voice.live_muted` ->
`storm_muted` (re-armed by a mute on -> off cycle on the Mac); 403
`voice.always_on_not_permitted` -> wake mode disarmed (`armed_ptt`); 429
and any other failure -> the error earcon, nothing spoken; a network error
is retried once for STT and TTS and never for the chat turn (a turn is
never sent twice).

Nothing here logs a transcript, reply text, audio or the token.
"""

import uuid

from . import config, language, log
from .audio import encode_wav, play_chunks
from .client import CODE_ALWAYS_ON_NOT_PERMITTED, CODE_LIVE_MUTED, CODE_PIN, FridayError
from .segmenter import SentenceSegmenter
from .turn import END, NO_SPEECH, TurnCapture
from .wake import WakeGate
from .workers import Closer, Delay

CAPTURING = "capturing"
SESSION = "session"
TRANSCRIBING = "transcribing"
REPLYING = "replying"


class RefuseAlwaysOn:
    """The arming policy until branch 5 (D11): always-on is never armed;
    `always_on` runs as push-to-talk."""

    def permitted(self) -> bool:
        return False


class AllowAlwaysOn:
    """Test double for the wake path (never used by `friday-voice run`)."""

    def permitted(self) -> bool:
        return True


def request_policy(client, model_sha256):
    """`GET /auth/nodes/voice-policy` for the loaded wake model at the
    threshold and refractory period the detector runs at -- read from
    `config` at call time, never from a copy (D8, D11; polled by the
    arming policy in branch 5)."""
    return client.voice_policy(model_sha256, config.WAKE_THRESHOLD, config.WAKE_REFRACTORY_MS)


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
        self.self_trigger = self_trigger  # branch 4: (transcript, turn) -> True to discard
        self.new_trace_id = new_trace_id or (lambda: str(uuid.uuid4()))
        self.log_stream = log_stream
        self.core = None  # set by ChildCore
        self.turn = None
        self.turns = 0
        self.completed = []  # finished turns (the replay report reads their timers)
        self.conversation_id = None
        self.session_id = None
        self.last_activity_ms = None
        self.refused = False  # a credential was refused: error until a new one
        self.storm_muted = False
        self._storm_unmute_seen = False
        self.wake_refused = False  # the server's 403 voice.always_on_not_permitted
        self.error_until = None
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

    @property
    def session_open(self) -> bool:
        """A live session is open while a turn runs and for
        `SILENCE_CLOSE_S` after the last activity (D12; the close itself,
        with its earcon, is branch 5). The conversation id is kept for
        reuse either way (D5)."""
        if self.conversation_id is None or self.last_activity_ms is None:
            return False
        if self.turn is not None:
            return True
        idle = self.clock.monotonic_ms() - self.last_activity_ms
        return idle < config.SILENCE_CLOSE_S * 1000

    def accepting(self) -> bool:
        return not (self.refused or self.storm_muted)

    def wake_armed(self, mode) -> bool:
        return (
            mode == "always_on"
            and self.wake_gate is not None
            and self.vad is not None
            and self.turn_end is not None
            and not self.wake_refused
            and self.arming.permitted()
        )

    def state(self, mode):
        """The pipeline's part of the `state` message, or None when the
        shell's blocking rules decide."""
        if self.storm_muted:
            return "storm_muted"
        if self.refused:
            return "error"
        if self.error_until is not None and self.clock.monotonic_ms() < self.error_until:
            return "error"
        turn = self.turn
        if turn is not None:
            if turn.phase == CAPTURING:
                return "listening"
            if turn.playing:
                return "speaking"
            return "thinking"
        return "armed_wake" if self.wake_armed(mode) else "armed_ptt"

    # --- inbound from the shell ---

    def on_credential(self, token, server_url, ca_sha256):
        self.client.set_credential(token, server_url, ca_sha256)
        self.refused = False
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
            return self._drain()  # branch 4: barge-in monitor
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

    def _on_wake(self):
        if self.core is not None:
            self.core.counters["wake_accepts_today"] = min(
                self.core.counters["wake_accepts_today"] + 1, 100_000
            )
        capture = TurnCapture(mode="vad", vad=self.vad, turn_end=self.turn_end)
        self._new_turn("wake", capture)
        self.turn.timer.mark("wake")
        self._send({"type": "earcon", "earcon": "wake"})

    def _new_turn(self, trigger, capture):
        self.turns += 1
        self.turn = Turn(self.turns, trigger, capture, self.clock, self.new_trace_id())

    def on_ptt(self, key):
        if key == "down":
            if self.turn is not None:
                self._abort_turn("ptt_restart")
            self._new_turn("ptt", TurnCapture(mode="ptt"))
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
            turn.playing = True
            turn.timer.mark("playback_start")
        else:
            turn.open_utterances.discard(utterance_id)
            turn.ended.add(index)
            turn.playing = bool(turn.open_utterances)
            self._pump()
            self._maybe_finish()
        return self._drain()

    def tick(self):
        return self._drain()

    def abort(self, reason):
        """A fail-closed input (link down, mute, mode off, shutdown, EOF,
        a protocol error, expiry): cancel the turn, stop its playback."""
        if self.turn is not None:
            self._abort_turn(reason)
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
        if self.conversation_id is None or idle is None or idle >= config.SILENCE_CLOSE_S * 1000:
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
        if self.self_trigger is not None and self.self_trigger(text, turn):
            self._log("turn.self_trigger_discarded")
            if self.core is not None:
                counters = self.core.counters
                counters["wake_discards_today"] = min(counters["wake_discards_today"] + 1, 100_000)
            self._finish_turn()
            return
        turn.transcript_language = language.normalise(event.get("language"))
        turn.phase = REPLYING
        turn.timer.mark("dispatched")
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
            self.wake_refused = True
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

    def _release(self, reason):
        if self.core is not None:
            self.core.drop_buffers()
        self._send({"type": "release_mic", "reason": reason})
