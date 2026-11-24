"""`FakeFriday`: the scripted stand-in for Friday's five endpoints (Phase 11
brief D8). Same interface as `HttpFridayClient`; no network at all.

Scripts:
- `transcripts`: one entry per transcription call, a string, a
  `{"text", "language"}` dict or a `FridayError` (the last entry repeats);
- `replies`: one entry per chat turn, a list of SSE events (`chunk` /
  `draft` / `done` / `error` dicts, or `(latency_ms, event)` pairs) or a
  `FridayError`;
- `latencies`: scripted server latencies in ms (`session`, `stt`,
  `chat_first` -- before the first event --, `chat_chunk` -- between
  events --, `tts`, `policy`); the worker applies them as `Delay`s, so the
  replay harness measures them on a manual clock;
- `speech`: `{text: wav bytes}` -- what synthesis returns for that exact
  text (the self-trigger clip, branch 6); any other text is a `tone_wav`;
- `fail_next(endpoint, error)`: queue a `FridayError` for the next call;
- `revoked`: tokens refused with 401 (a rotation test revokes the old one).

It records every request (endpoint, token, trace id, trigger, body fields,
the uploaded WAV's PCM), every chat stream closed before its end
(`aborts`) and every synthesis cancelled while in flight (`speech_aborts`,
`(trace_id, sentence index)`; barge-in, D9), so the tests can assert what
left the child and when.
"""

import io
import math
import wave

import numpy as np

from .client import ChatStream, FridayError, Reply

DEFAULT_LATENCIES = {
    "session": 30,
    "stt": 250,
    "chat_first": 450,
    "chat_chunk": 40,
    "tts": 120,
    "policy": 20,
}
DEFAULT_REPLY = [
    {"type": "chunk", "text": "Bugün iki toplantın var. "},
    {"type": "chunk", "text": "İlki saat onda, ikincisi öğleden sonra üçte."},
    {"type": "done", "message_id": "m-1"},
]


def tone_wav(text, *, rate=22_050, ms_per_char=40, frequency=440.0) -> bytes:
    """A synthetic "spoken" sentence: a tone whose length follows the text
    (never a real voice). 22.05 kHz on purpose: the child must normalise
    the server's WAV to 16 kHz before playing it."""
    seconds = max(0.2, len(text) * ms_per_char / 1000)
    count = int(rate * seconds)
    samples = 0.2 * np.sin(2 * math.pi * frequency * np.arange(count) / rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((samples * 32767).astype("<i2").tobytes())
    return out.getvalue()


def _wav_pcm(data) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        return wav.readframes(wav.getnframes())


class FakeFriday:
    def __init__(self, *, transcripts=("bugün ne var",), replies=None, latencies=None, speech=None):
        self.transcripts = list(transcripts)
        self.speech = dict(speech or {})
        self.replies = [DEFAULT_REPLY] if replies is None else list(replies)
        self.latencies = {**DEFAULT_LATENCIES, **(latencies or {})}
        self.requests = []
        self.aborts = 0
        self.speech_aborts = []
        self.revoked = set()
        self.policy = {"always_on_permitted": False, "reasons": ["disabled"]}
        self._failures = {}
        self._generation = 0
        self._credential = None
        self._stt_calls = 0
        self._chat_calls = 0
        self.conversation_id = "11111111-1111-4111-8111-111111111111"
        self.sessions = 0

    # --- the client interface ---

    @property
    def generation(self) -> int:
        return self._generation

    def set_credential(self, token, server_url, ca_sha256) -> None:
        self._generation += 1
        self._credential = (token, server_url, ca_sha256, self._generation)

    def fail_next(self, endpoint, error) -> None:
        self._failures.setdefault(endpoint, []).append(error)

    def _begin(self, endpoint, **fields):
        if self._credential is None:
            raise FridayError(401, "credential_missing")
        token, _url, _ca, generation = self._credential
        self.requests.append({"endpoint": endpoint, "token": token, **fields})
        latency = self.latencies.get(endpoint if endpoint != "chat" else "chat_first", 0)
        queued = self._failures.get(endpoint)
        if queued:
            error = queued.pop(0)
            if isinstance(error, int):
                error = FridayError(error, "", network=error == 0)
            raise self._stamp(error, generation, latency)
        if token in self.revoked:
            raise self._stamp(FridayError(401, "not_authenticated"), generation, latency)
        return generation, latency

    @staticmethod
    def _stamp(error, generation, latency):
        stamped = FridayError(
            error.status, error.code, generation=generation, network=error.network
        )
        stamped.latency_ms = latency
        return stamped

    def open_session(self, trigger) -> Reply:
        generation, latency = self._begin("session", trigger=trigger)
        self.sessions += 1
        return Reply(
            {"conversation_id": self.conversation_id, "session_id": f"s-{self.sessions}"},
            generation,
            latency,
        )

    def transcribe(self, conversation_id, wav, trace_id, trigger) -> Reply:
        generation, latency = self._begin(
            "stt",
            conversation_id=conversation_id,
            trace_id=trace_id,
            trigger=trigger,
            pcm=_wav_pcm(wav),
        )
        script = self.transcripts[min(self._stt_calls, len(self.transcripts) - 1)]
        self._stt_calls += 1
        if isinstance(script, FridayError):
            raise self._stamp(script, generation, latency)
        if isinstance(script, str):
            script = {"text": script, "language": "tr"}
        return Reply(
            {"text": script["text"], "language": script.get("language", "")}, generation, latency
        )

    def chat(self, conversation_id, text, trace_id) -> ChatStream:
        generation, first = self._begin(
            "chat", conversation_id=conversation_id, trace_id=trace_id, text=text
        )
        script = self.replies[min(self._chat_calls, len(self.replies) - 1)]
        self._chat_calls += 1
        if isinstance(script, FridayError):
            raise self._stamp(script, generation, first)
        state = {"finished": False}
        stream = ChatStream([], generation)

        def events():
            for index, item in enumerate(script):
                latency, event = item if isinstance(item, tuple) else (None, item)
                if latency is None:
                    latency = first if index == 0 else self.latencies["chat_chunk"]
                yield latency, event
            state["finished"] = True

        def close():
            if not state["finished"]:
                self.aborts += 1
                state["finished"] = True

        stream.events = events()
        stream._close = close
        return stream

    def synthesize(self, conversation_id, text, language, trace_id, timings=None) -> Reply:
        generation, latency = self._begin(
            "tts",
            conversation_id=conversation_id,
            trace_id=trace_id,
            text=text,
            language=language,
            timings=dict(timings or {}),
        )
        wav = self.speech.get(text)
        return Reply(wav if wav is not None else tone_wav(text), generation, latency)

    def abort_speech(self, trace_id, index) -> None:
        """The pipeline cancelled a synthesis still in flight."""
        self.speech_aborts.append((trace_id, index))

    def voice_policy(self, model_sha256, threshold, refractory_ms) -> Reply:
        generation, latency = self._begin(
            "policy", model_sha256=model_sha256, threshold=threshold, refractory_ms=refractory_ms
        )
        return Reply(dict(self.policy), generation, latency)

    def close(self) -> None:
        pass

    # --- assertions helpers ---

    def calls(self, endpoint) -> list:
        return [request for request in self.requests if request["endpoint"] == endpoint]
