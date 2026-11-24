"""`friday-voice selftest` (Phase 11 brief D13; ADR-015 decision 4).

**`--ci` -- the self-trigger release gate.** Friday's own playback of
"Hey Friday, terminali aç" (`fixtures/selftrigger_hey_friday_terminali_ac.
wav`, synthesised by the fake TTS of `scripts/make_fixtures.py`: a
deterministic tone pattern with a scripted transcript, never a recording)
is played through the reference host (paced playback) while its
`EchoDouble` leaks it into the microphone at residual 0.0, 0.3 and 1.0, and
a scripted wake detector fires on it once "Hey Friday" has been played --
the worst case, as if the wake model accepted Friday's own voice whatever
the canceller left. The real child (protocol shell, pipeline, the real
arming policy against a permitting fake policy, the self-trigger filter)
runs on a manual clock against `FakeFriday`. For every residual: the wake
fired, no echo turn is posted, no `draft` reaches the child, nothing is
spoken but the clip, and every upload is discarded (`discarded_self` + 1 at
0.3 and 1.0; at 0.0 the perfect canceller leaves nothing to upload -- branch
4 note 7). The negative control runs the same script with the filter off
and **must** post the echo as a turn (and get the `node_action` draft the
real endpoint would propose), so the gate proves the filter, not the fake.

**`--nightly` -- the Mac's nightly run (M3).** The same scenario with the
real pieces: the pinned `hey_friday` model (`--model-dir`), the clip
synthesised by the real TTS through Friday (`--server`, `--token-file`: the
node voice credential), played and captured through the host's real audio
path with its voice-processing AEC (`--loopback-cmd`: a command that reads
a WAV on stdin, plays it on the speakers and writes the AEC-cleaned capture
as a WAV on stdout -- the Swift host provides it, Mac session M3). The
capture is scored by the real detector behind the pipeline's `WakeGate`;
every detection is transcribed by Friday and put through the self-trigger
filter, and one the filter would let through counts as a posted turn (the
selftest never posts a chat turn itself). Exit 0 only with no wake and no
posted turn. Real models never run in GitHub CI (CLAUDE.md rule 5).

Both modes can write a **one-line JSON result** (`--result-file`, written
atomically): the nightly launchd job points it at
`VOICE_LIVE_SELFTEST_RESULT_PATH` for the `voice_live_selftest` preflight
check (branch 8). Numbers and codes only: no audio, no transcript.

Exit codes: 0 pass, 1 fail (the gate bit), 2 refused or errored (a run
that could not judge is never green).
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .arming import ArmingPolicy
from .audio import decode_wav, encode_wav
from .fakes import FakeFriday
from .ports import ManualClock
from .reference.echo import EchoDouble
from .reference.host import FRAME_MS, wav_frames
from .replay import FIXTURES_DIR, ReplaySession
from .self_trigger import SelfTriggerFilter
from .turn import EnergyVad, ScriptedTurnEnd
from .wake import ScriptedWakeDetector, WakeGate

SCRIPT_PATH = FIXTURES_DIR / "selftrigger_hey_friday_terminali_ac.json"
QUESTION_WAV = "ptt_question.wav"
# The scripted detector fires once this much of the clip has been played
# ("Hey Fri" is on the air): before the VAD barge-in monitor could fire on a
# leaking echo (its 300 ms guard + 250 ms persistence), so the gate drives the
# WAKE path at every residual. The VAD path on Friday's own echo is branch
# 4's `echo_r03` / `echo_r10` replay scripts.
WAKE_AFTER_MS = 400
RESULT_SCHEMA = "friday-voice/selftest/v1"


def load_script(path=SCRIPT_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _events(script):
    return [tuple(item) if isinstance(item, list) else item for item in script]


# --- the CI release gate ---


def run_selftrigger(residual, *, self_trigger=True, script=None, fixtures_dir=FIXTURES_DIR):
    """One run of the self-trigger script at `residual`. Returns the
    observations (see `judge_gate` / `judge_control`)."""
    script = script or load_script()
    clip_text = script["clip"]["text"]
    clip_wav = (Path(fixtures_dir) / script["clip"]["wav"]).read_bytes()
    client = FakeFriday(
        transcripts=[script["question"], script["heard"]],
        replies=[_events(script["reply_to_question"]), _events(script["reply_to_echo"])],
        latencies=script.get("latencies"),
        speech={clip_text: clip_wav},
    )
    client.policy = {"always_on_permitted": True, "reasons": []}
    holder = {"played_ms": 0}

    def scores(_index, _pcm):
        # Fires on the echo of the clip: the first reply (turn 1) is the
        # clip; once "Hey Friday" is on the air the "model" accepts it.
        turn = holder["session"].pipeline.turn
        if turn is None or turn.number != 1 or not turn.playing:
            return 0
        holder["played_ms"] += FRAME_MS
        return 900 if holder["played_ms"] >= WAKE_AFTER_MS else 0

    session = ReplaySession(
        client=client,
        mode="always_on",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        wake_detector=ScriptedWakeDetector(scores),
        arming=ArmingPolicy(models_verified=True),
        echo=EchoDouble(residual=residual, delay_ms=script.get("echo_delay_ms", 0)),
        paced_playback=True,
        self_trigger=self_trigger,
    )
    holder["session"] = session
    session.start()
    armed = session.core.current_state() == "armed_wake"
    quiet = b"\x00" * config.ms_to_bytes(FRAME_MS)
    session.ptt("down")
    session.frames(wav_frames(Path(fixtures_dir) / QUESTION_WAV))
    session.ptt("up")
    for _ in range(30_000 // FRAME_MS):
        if session.core.current_state() == "speaking":
            break
        session.frame(quiet)
    # Long enough for a wake with nothing heard to time out (5 s).
    session.silence(config.WAKE_NO_SPEECH_S * 1000 + 1_000)
    session.settle()
    session.silence(500)
    spoken = [call["text"] for call in client.calls("tts")]
    chats = [call["text"] for call in client.calls("chat")]
    return {
        "residual": residual,
        "filter": bool(self_trigger),
        "armed": armed,
        "wakes": len([f for f in session.sent("earcon") if f["earcon"] == "wake"]),
        "uploads": len(client.calls("stt")) - 1,
        "echo_turns": max(0, len(chats) - 1),
        "drafts": sum(1 for turn in session.pipeline.completed if turn.draft),
        "replies_spoken": [text for text in spoken if text != clip_text],
        "foreign_play": sorted(
            {
                f["utterance_id"]
                for f in session.sent("play")
                if not f["utterance_id"].startswith("t1s")
            }
        ),
        "discarded": session.pipeline.self_trigger.discarded_self,
        "interrupted_via": [b["via"] for b in session.pipeline.barge_ins][:1],
        "clip_spoken": clip_text in spoken,
    }


def judge_gate(result) -> list:
    """Failures of the release gate (filter on) for one residual."""
    failures = []
    if not result["armed"]:
        failures.append("wake mode never armed (the scenario did not run as always-on)")
    if not result["clip_spoken"]:
        failures.append("the clip was never played")
    if result["wakes"] < 1 or result["interrupted_via"] != ["wake"]:
        failures.append("the scripted wake detector never fired on the echo")
    if result["echo_turns"]:
        failures.append(f"{result['echo_turns']} echo turn(s) posted")
    if result["drafts"]:
        failures.append(f"{result['drafts']} draft(s) reached the child")
    if result["replies_spoken"] or result["foreign_play"]:
        failures.append("something was spoken in reply to the echo")
    if result["discarded"] != result["uploads"]:
        failures.append(
            f"{result['uploads']} upload(s) but {result['discarded']} discarded as self-trigger"
        )
    expected = 0 if result["residual"] == 0 else 1
    if result["discarded"] != expected:
        failures.append(f"discarded_self {result['discarded']}, expected {expected}")
    return failures


def judge_control(result) -> list:
    """Failures of the negative control (filter off): the echo MUST post."""
    failures = []
    if result["echo_turns"] < 1:
        failures.append("the negative control posted no echo turn (the gate proves nothing)")
    if result["drafts"] < 1:
        failures.append("the negative control's echo turn got no draft")
    if result["discarded"]:
        failures.append("the disabled filter discarded something")
    return failures


def run_ci(*, script=None, out=print) -> dict:
    """The release gate at every residual plus the negative control.
    Returns `{"passed": bool, "runs": [...]}`."""
    script = script or load_script()
    runs = []
    for residual in script["residuals"]:
        result = run_selftrigger(residual, script=script)
        failures = judge_gate(result)
        runs.append({"kind": "gate", **result, "failures": failures})
    for residual in script["negative_control_residuals"]:
        result = run_selftrigger(residual, self_trigger=False, script=script)
        failures = judge_control(result)
        runs.append({"kind": "negative_control", **result, "failures": failures})
    for run in runs:
        verdict = "ok" if not run["failures"] else "FAIL"
        out(
            f"{run['kind']} residual={run['residual']}: {verdict} wakes={run['wakes']} "
            f"uploads={run['uploads']} echo_turns={run['echo_turns']} drafts={run['drafts']} "
            f"discarded={run['discarded']}"
        )
        for failure in run["failures"]:
            out(f"  {failure}")
    return {"passed": not any(run["failures"] for run in runs), "runs": runs}


# --- the nightly run (Mac, M3) ---


class LoopbackFailed(RuntimeError):
    """The host's audio path did not play and capture the clip."""


class CommandLoopback:
    """The host's real audio path as an external command: the clip WAV on
    its stdin, the AEC-cleaned capture as a WAV on its stdout (in memory;
    nothing is written to disk here). The Swift host provides the command
    (Mac session M3)."""

    def __init__(self, command, *, timeout_s=120):
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not self.argv:
            raise LoopbackFailed("empty loopback command")
        self.timeout_s = timeout_s

    def play_and_capture(self, pcm: bytes) -> bytes:
        try:
            done = subprocess.run(  # noqa: S603 -- the owner's configured host command
                self.argv,
                input=encode_wav(pcm),
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LoopbackFailed(type(exc).__name__) from exc
        if done.returncode != 0:
            raise LoopbackFailed(f"exit {done.returncode}")
        try:
            return decode_wav(done.stdout)
        except ValueError as exc:
            raise LoopbackFailed("the capture is not a PCM WAV") from exc


def detections_in(detector, pcm, *, frame_ms=FRAME_MS) -> list:
    """Byte offsets just after every detection frame (the pipeline's
    `WakeGate`, threshold and refractory period from `config`)."""
    detector.reset()
    clock = ManualClock()
    gate = WakeGate(detector, clock)
    size = config.ms_to_bytes(frame_ms)
    hits = []
    for start in range(0, len(pcm), size):
        frame = pcm[start : start + size]
        frame += b"\x00" * (size - len(frame))
        if gate.feed(frame):
            hits.append(start + size)
        clock.advance(frame_ms)
    return hits


def run_nightly(*, client, detector, loopback, script=None) -> dict:
    """One nightly run with real pieces (all injected, so it is tested with
    fakes). Returns `{"result": "pass"|"fail", "wakes", "posted_turns",
    "model_sha256"}`; raises on a failure to run."""
    script = script or load_script()
    clip_text = script["clip"]["text"]
    session = client.open_session("ptt").data
    conversation_id = session["conversation_id"]
    speech = client.synthesize(conversation_id, clip_text, "tr", str(uuid.uuid4()))
    clip = decode_wav(speech.data)
    captured = loopback.play_and_capture(clip)
    hits = detections_in(detector, captured)
    posted = 0
    limit = config.ms_to_bytes(config.UTTERANCE_MAX_S * 1000)
    for offset in hits:
        utterance = captured[offset : offset + limit]
        if not utterance:
            continue
        reply = client.transcribe(
            conversation_id, encode_wav(utterance), str(uuid.uuid4()), "barge_in"
        )
        text = str((reply.data or {}).get("text") or "").strip()
        if not text:
            continue
        clock = ManualClock()
        memory = SelfTriggerFilter(clock)
        memory.spoken(clip_text)
        if memory.check(text, suspect=True) is None:
            posted += 1
    return {
        "result": "pass" if not hits and not posted else "fail",
        "wakes": len(hits),
        "posted_turns": posted,
        "model_sha256": str(getattr(detector, "model_sha256", "")),
    }


# --- the result line ---


def result_line(mode, result, *, now=None) -> str:
    """The one-line JSON the preflight check reads (no audio, no text)."""
    at = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {
        "schema": RESULT_SCHEMA,
        "mode": mode,
        "result": result["result"],
        "at": at,
        "wakes": int(result.get("wakes", 0)),
        "posted_turns": int(result.get("posted_turns", 0)),
        "model_sha256": str(result.get("model_sha256", "")),
        "threshold": config.WAKE_THRESHOLD,
        "refractory_ms": config.WAKE_REFRACTORY_MS,
        "reason": str(result.get("reason", ""))[:60],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def write_result(path, line) -> None:
    """Replace the result file atomically (a reader never sees half a line)."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".selftest-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(line + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- the command ---


def _err(line):
    print(line, file=sys.stderr)


def _finish(args, mode, result, *, out=print, err=_err) -> int:
    line = result_line(mode, result)
    if args.result_file:
        write_result(args.result_file, line)
    out(line)
    return {"pass": 0, "fail": 1}.get(result["result"], 2)


def _nightly_parts(args):
    """The real client, detector and loopback for `--nightly`."""
    from .client import HttpFridayClient
    from .wake import OpenWakeWordDetector

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    client = HttpFridayClient(ca_file=args.ca_file)
    client.set_credential(token, args.server, args.ca_sha256)
    detector = OpenWakeWordDetector(args.model_dir)
    return client, detector, CommandLoopback(args.loopback_cmd)


def main(args, *, nightly_parts=_nightly_parts, out=print, err=_err) -> int:
    if args.ci == args.nightly:
        err("friday-voice selftest needs exactly one of --ci or --nightly")
        return 2
    if args.ci:
        report = run_ci(out=out)
        gate = [run for run in report["runs"] if run["kind"] == "gate"]
        result = {
            "result": "pass" if report["passed"] else "fail",
            "posted_turns": sum(run["echo_turns"] for run in gate),
        }
        out(f"friday-voice selftest ci: {'PASS' if report['passed'] else 'FAIL'}")
        return _finish(args, "ci", result, out=out, err=err)
    missing = [
        flag
        for flag, value in (
            ("--server", args.server),
            ("--token-file", args.token_file),
            ("--model-dir", args.model_dir),
            ("--loopback-cmd", args.loopback_cmd),
        )
        if not value
    ]
    if missing:
        err(f"friday-voice selftest --nightly refuses to run without {', '.join(missing)}")
        return _finish(args, "nightly", {"result": "error", "reason": "usage"}, out=lambda _l: None)
    client = None
    try:
        client, detector, loopback = nightly_parts(args)
        result = run_nightly(client=client, detector=detector, loopback=loopback)
    except Exception as exc:  # noqa: BLE001 -- any failure to run is an error, never green
        err(f"friday-voice selftest --nightly: could not run ({type(exc).__name__})")
        result = {"result": "error", "reason": type(exc).__name__}
    finally:
        if client is not None:
            client.close()
    return _finish(args, "nightly", result, out=out, err=err)
