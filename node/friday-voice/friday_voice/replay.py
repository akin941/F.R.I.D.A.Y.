"""The recorded-audio replay harness (Phase 11 brief D8, D14).

`ReplaySession` wires the reference host (`friday_voice.reference.host.
HostCore`, optionally with its `EchoDouble`) to the real child
(`ChildCore` + `VoicePipeline`, through the same `ChildLink` decode ->
validate path as stdio) on one `ManualClock`, with a `SimWorker` running
the pipeline's calls to Friday. Every frame crosses the real framing and
schema checks in both directions.

`friday-voice replay` plays the fixture set (`node/friday-voice/fixtures/
replay.json`: synthetic WAVs -- tones and silence -- with scripted
transcripts, replies and server latencies at the §c.1 budgets) and
prints per-stage p50/p95. `--check` compares the pipeline's own overhead
(time to the first audio that is not a scripted server latency) with the
stored baseline (`baselines/replay.json`) and fails when its p50 grew more
than `config.REPLAY_REGRESSION_PCT` (20 %). `--server <url>` plays the
push-to-talk scenarios against a running Friday instead of `FakeFriday`
(the closeout e2e); it reports completion only -- real latencies are Mac
measurements (M4).

Fixtures are read from disk only; nothing here writes audio anywhere.
"""

import io
import json
import math
import time
import uuid
from pathlib import Path

from . import config
from .child import ChildCore, ChildLink
from .fakes import FakeFriday
from .pipeline import AllowAlwaysOn, RefuseAlwaysOn, VoicePipeline
from .ports import ManualClock
from .reference.echo import EchoDouble
from .reference.host import FRAME_MS, HostCore, wav_frames
from .turn import EnergyVad, ScriptedTurnEnd
from .wake import ScriptedWakeDetector
from .workers import SimWorker

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PACKAGE_ROOT / "fixtures"
SCENARIOS_PATH = FIXTURES_DIR / "replay.json"
BARGE_IN_PATH = FIXTURES_DIR / "barge_in.json"
BASELINE_PATH = PACKAGE_ROOT / "baselines" / "replay.json"
STAGES = (
    "end_of_turn_ms",
    "session_ms",
    "stt_ms",
    "llm_first_sentence_ms",
    "tts_first_ms",
    "playback_start_ms",
    "overhead_ms",
)

DEV_CREDENTIAL = {
    "token": "jpat_" + "replayFixtureT0ken" * 2,
    "expires_at": "2099-01-01T00:00:00Z",
    "server_url": "http://127.0.0.1:8001",
    "ca_sha256": None,
}


class ReplaySession:
    """One host and one child on a manual clock (see the module docstring)."""

    def __init__(
        self,
        *,
        client=None,
        mode="push_to_talk",
        credential=None,
        vad=None,
        turn_end=None,
        wake_detector=None,
        arming=None,
        echo=None,
        self_trigger=None,
        paced_playback=False,
        clock=None,
    ):
        self.clock = clock or ManualClock()
        self.client = client if client is not None else FakeFriday()
        self.worker = SimWorker(self.clock)
        self.log = io.StringIO()
        traces = iter(range(1, 1_000_000))
        self.pipeline = VoicePipeline(
            self.clock,
            self.client,
            self.worker,
            vad=vad,
            turn_end=turn_end,
            wake_detector=wake_detector,
            arming=arming,
            self_trigger=self_trigger,
            new_trace_id=lambda: f"00000000-0000-4000-8000-{next(traces):012d}",
            log_stream=self.log,
        )
        self.core = ChildCore(self.clock, log_stream=self.log, pipeline=self.pipeline)
        self.link = ChildLink(self.core)
        self.host = HostCore(
            self.clock,
            credential=credential or dict(DEV_CREDENTIAL),
            mode=mode,
            echo=echo,
            paced_playback=paced_playback,
        )
        self.frames_to_host = []  # every child frame header, in order
        self.frame_times = []  # the monotonic ms each of them reached the host

    # --- moving bytes ---

    def _to_host(self, encoded):
        from .protocol.framing import decode_frame

        for frame in encoded:
            self.frames_to_host.append(decode_frame(frame)[0])
            self.frame_times.append(self.clock.monotonic_ms())
            self.host.child_bytes(frame)

    def pump(self):
        """Exchange frames until both sides are quiet."""
        for _ in range(10_000):
            outbound = self.host.take_outbound()
            if not outbound:
                return
            for frame in outbound:
                self._to_host(self.link.feed(frame))
        raise RuntimeError("the host and the child never went quiet")

    def _tick(self):
        self._to_host(self.link.tick())
        self.host.tick()
        self.pump()

    def start(self):
        self.host.start_child()
        self.pump()
        return self

    # --- driving ---

    def advance(self, ms):
        self.worker.advance(ms, on_tick=self._tick)
        self._tick()

    def frame(self, pcm):
        """One 20 ms capture frame, then 20 ms of time."""
        self.host.feed_audio(pcm)
        self.pump()
        self.advance(FRAME_MS)

    def frames(self, frames):
        for pcm in frames:
            self.frame(pcm)

    def silence(self, ms):
        for _ in range(int(ms) // FRAME_MS):
            self.frame(b"\x00" * config.ms_to_bytes(FRAME_MS))

    def ptt(self, key):
        self.host.ptt(key)
        self.pump()

    def credential(self, credential):
        self.host.send_credential(credential)
        self.pump()

    def settle(self, limit_ms=120_000):
        """Run every job to its end and, with paced playback, let the host
        play out what it holds (the clock moves; no capture is fed)."""
        start = self.clock.monotonic_ms()
        while True:
            self.worker.run_until_idle(limit_ms=limit_ms, on_tick=self._tick)
            self._tick()
            if not self.host.playback_pending:
                return
            if self.clock.monotonic_ms() - start > limit_ms:
                raise RuntimeError("playback never ended")
            self.advance(FRAME_MS)

    # --- reading ---

    def sent(self, message_type):
        return [frame for frame in self.frames_to_host if frame["type"] == message_type]

    def sent_at(self, message_type):
        """`(monotonic ms, header)` of every child frame of that type."""
        return [
            (at, frame)
            for at, frame in zip(self.frame_times, self.frames_to_host, strict=True)
            if frame["type"] == message_type
        ]

    def states(self):
        return [frame["state"] for frame in self.sent("state")]


# --- the fixture set ---


def load_scenarios(path=SCENARIOS_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))["scenarios"]


def _session_for(scenario, client):
    if scenario["trigger"] == "wake":
        frames_before = scenario.get("wake_after_frames", 0)
        return ReplaySession(
            client=client,
            mode="always_on",
            vad=EnergyVad(),
            turn_end=ScriptedTurnEnd([True]),
            wake_detector=ScriptedWakeDetector({frames_before: 900}),
            # Scripted arming: the replay measures the wake path's stages;
            # the real arming policy refuses always-on until branch 5.
            arming=AllowAlwaysOn(),
        )
    return ReplaySession(client=client, mode="push_to_talk", arming=RefuseAlwaysOn())


def run_scenario(scenario, *, client=None, fixtures_dir=FIXTURES_DIR):
    """Play one scenario; returns `{id, stages, turns, spoken, cpu_ms}`."""
    if client is None:
        client = FakeFriday(
            transcripts=[scenario["transcript"]],
            replies=[scenario["reply"]],
            latencies=scenario.get("latencies"),
        )
    session = _session_for(scenario, client).start()
    frames = wav_frames(fixtures_dir / scenario["wav"])
    started = time.perf_counter()
    if scenario["trigger"] == "ptt":
        session.ptt("down")
        session.frames(frames)
        session.ptt("up")
    else:
        session.frames(frames)
        session.silence(scenario.get("trailing_silence_ms", 2_000))
    session.settle()
    cpu_ms = round((time.perf_counter() - started) * 1000, 1)
    turns = session.pipeline.completed
    stages = turns[-1].timer.stages() if turns else {}
    spoken = [call["text"] for call in getattr(client, "calls", lambda _e: [])("tts")]
    return {
        "id": scenario["id"],
        "stages": stages,
        "turns": len(turns),
        "played_utterances": len({f["utterance_id"] for f in session.sent("play")}),
        "spoken": spoken,
        "cpu_ms": cpu_ms,
    }


# --- barge-in and echo scripts (D9, D10) ---


def load_barge_in_scenarios(path=BARGE_IN_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))["scenarios"]


def _events(script):
    """JSON keeps `(latency_ms, event)` pairs as lists; FakeFriday wants tuples."""
    return [tuple(item) if isinstance(item, list) else item for item in script]


def run_barge_in_scenario(scenario, *, fixtures_dir=FIXTURES_DIR):
    """Play one barge-in/echo script: a push-to-talk question, Friday's
    reply on the paced reference host with its EchoDouble, the owner's
    speech (if any) `owner_after_ms` into playback. Returns `{id, stopped,
    chat_turns, discarded, stop_ms, interrupted_at_ms, failures}`;
    `stop_ms` is barge-in onset (the 250 ms persistence met) ->
    `stop_playback` at the host."""
    client = FakeFriday(
        transcripts=scenario["transcripts"],
        replies=[_events(reply) for reply in scenario["replies"]],
        latencies=scenario.get("latencies"),
    )
    echo = EchoDouble(residual=scenario["residual"], delay_ms=scenario.get("echo_delay_ms", 0))
    session = ReplaySession(
        client=client,
        mode="push_to_talk",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        echo=echo,
        paced_playback=True,
    ).start()
    quiet = b"\x00" * config.ms_to_bytes(FRAME_MS)
    session.ptt("down")
    session.frames(wav_frames(fixtures_dir / scenario["wav"]))
    session.ptt("up")
    for _ in range(30_000 // FRAME_MS):
        if session.core.current_state() == "speaking":
            break
        session.frame(quiet)
    turn = session.pipeline.turn
    started = turn.timer.marks.get("playback_start") if turn is not None else None
    if started is not None:
        while session.clock.monotonic_ms() < started + scenario["owner_after_ms"]:
            session.frame(quiet)
    if scenario.get("owner_wav"):
        session.frames(wav_frames(fixtures_dir / scenario["owner_wav"]))
    session.silence(3_000)
    session.settle()
    session.silence(500)
    stops = session.sent_at("stop_playback")
    stop_ms = None
    if session.pipeline.barge_ins and stops:
        first = session.pipeline.barge_ins[0]
        onset = first["onset_ms"] + (config.BARGE_IN_MIN_MS if first["via"] == "vad" else 0)
        stop_ms = max(0, min(at for at, _frame in stops if at >= first["fired_ms"]) - onset)
    interrupted = [t for t in session.pipeline.completed if t.interrupted]
    result = {
        "id": scenario["id"],
        "stopped": bool(stops),
        "chat_turns": len(client.calls("chat")),
        "discarded": session.pipeline.self_trigger.discarded_self,
        "stop_ms": stop_ms,
        "interrupted_at_ms": (
            interrupted[0].timer.extra.get("interrupted_at_ms") if interrupted else None
        ),
    }
    failures = [
        f"{key}: expected {value}, got {result[key]}"
        for key, value in scenario["expect"].items()
        if result[key] != value
    ]
    if stop_ms is not None and stop_ms > config.BARGE_IN_STOP_BUDGET_MS:
        failures.append(
            f"stop_playback {stop_ms} ms after onset > {config.BARGE_IN_STOP_BUDGET_MS} ms"
        )
    result["failures"] = failures
    return result


def percentile(values, pct):
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def report(results) -> dict:
    stages = {}
    for stage in STAGES:
        values = [r["stages"][stage] for r in results if stage in r["stages"]]
        if values:
            stages[stage] = {"p50": percentile(values, 50), "p95": percentile(values, 95)}
    return {"scenarios": len(results), "stages": stages}


def check(current, baseline, *, pct=config.REPLAY_REGRESSION_PCT) -> list:
    """Failures (empty: pass) of the overhead regression rule."""
    try:
        base = baseline["stages"]["overhead_ms"]["p50"]
        now = current["stages"]["overhead_ms"]["p50"]
    except KeyError:
        return ["overhead_ms p50 missing from the report or the baseline"]
    limit = base * (100 + pct) / 100
    if now > limit:
        return [f"pipeline overhead p50 {now} ms > {limit:.0f} ms (baseline {base} ms + {pct} %)"]
    return []


def load_baseline(path=BASELINE_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_baseline(current) -> str:
    doc = {
        "$comment": (
            "Phase 11 brief D14: the replay harness's stage numbers on the fixture set "
            "(manual clock, FakeFriday latencies at the §c.1 budgets). `friday-voice replay "
            "--check` fails when overhead_ms p50 grows more than 20 %. Regenerate with "
            "`friday-voice replay --write-baseline` only for a deliberate change."
        ),
        **current,
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def run_server(
    server_url, token, *, ca_file=None, ca_sha256=None, scenarios=None, out=print
) -> int:
    """Play the push-to-talk scenarios against a running Friday (real HTTP,
    the node voice token). Exit code 0 when every turn produced audio."""
    from .client import HttpFridayClient

    failures = 0
    for scenario in scenarios or load_scenarios():
        if scenario["trigger"] != "ptt":
            continue
        client = HttpFridayClient(ca_file=ca_file)
        credential = {
            **DEV_CREDENTIAL,
            "token": token,
            "server_url": server_url.rstrip("/"),
            "ca_sha256": ca_sha256,
        }
        session = ReplaySession(client=client, mode="push_to_talk", credential=credential)
        session.pipeline.new_trace_id = lambda: str(uuid.uuid4())  # real rows on a real server
        session.start()
        session.ptt("down")
        session.frames(wav_frames(FIXTURES_DIR / scenario["wav"]))
        session.ptt("up")
        session.settle()
        played = len({f["utterance_id"] for f in session.sent("play")})
        ok = played > 0 and len(session.pipeline.completed) == 1
        failures += not ok
        out(f"{scenario['id']}: {'ok' if ok else 'FAILED'} ({played} sentence(s) played)")
        client.close()
    return 1 if failures else 0
