"""The live-voice end-to-end driver (Phase 11 closeout, brief §8 "E2E (CI,
fakes)"): the reference host (`HostCore` with paced playback, optionally
its `EchoDouble`) drives a real child process over its stdio pipes, and the
child talks real HTTP to a running Friday (the backend e2e starts uvicorn
with FakeAdapter and a fake speech provider and spawns this driver).

    friday-voice e2e --server <url> --token-file <path> --scenario <name>

The driver writes JSON lines on stdout: `{"event": "await", "step": ...}`
when it needs the server side to do something (engage the kill switch,
release a storm mute on screen) -- it then reads one line on stdin before
going on -- and `{"event": "state", ...}` for every child `state` frame
(what the Swift host relays to the node WebSocket as `voice.state`, D15),
and one final `{"event": "result", ...}` with the observations (states,
indicator, mic, releases, played and stopped utterances, earcons, log
codes). Codes, ids and counts only: no transcript, no audio, no token.

Scenarios:
- `ptt` -- one push-to-talk turn, spoken back sentence by sentence;
- `node_action` -- the same, for a turn that yields a draft card (the
  child says "Kart ekranda." after the reply);
- `barge_in` -- the hotkey pressed while Friday speaks: playback stopped,
  the next turn answered (`interrupted_at_ms` rides on its speech request);
- `kill_switch` -- one turn, then (after `await kill_switch`) the next
  request is refused, the child releases the mic, and the node link drops
  as it does when the gateway closes the socket: the indicator says `off`;
- `storm` -- push-to-talk turns until the server's storm backstop mutes the
  node (423, `storm_muted`, `release_mic(storm)`); after `await storm_muted`
  (the owner released the mute on screen) a mute on -> off cycle re-enables
  the child and a turn completes;
- `selftrigger` -- always-on with the scripted detector stack
  (`friday_voice.e2e_child`), the reference host's echo double at residual
  1.0: Friday's own "Hey Friday, terminali aç." wakes the scripted detector
  and is discarded by the self-trigger rule -- no second turn.

The child is `friday-voice run` for every scenario but `selftrigger`, which
needs detectors that `run` loads only from the owner's pinned models (D8):
there `python -m friday_voice.e2e_child` serves the same `run_stdio` loop,
HTTP client, thread worker and real arming policy with scripted detectors.
"""

import json
import sys
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from . import config
from .ports import RealClock
from .reference.echo import EchoDouble
from .reference.host import FRAME_MS, HostCore, ProcessHost, wav_frames
from .replay import FIXTURE_WAKE_MODEL_SHA256, FIXTURES_DIR

SCENARIOS = ("ptt", "node_action", "barge_in", "kill_switch", "storm", "selftrigger")
STATE_KEYS = (
    "state",
    "mode",
    "session_open",
    "wake_accepts_today",
    "wake_discards_today",
    "storm_mutes_today",
)
SILENCE = b"\x00" * config.ms_to_bytes(FRAME_MS)
QUESTION_WAV = FIXTURES_DIR / "ptt_question.wav"
SETTLED = frozenset({"armed_ptt", "armed_wake", "storm_muted", "error", "muted", "off"})
TURN_TIMEOUT_S = 60


class RecordingHost(HostCore):
    """The reference host, recording every child `state` frame and every
    playback report it sends (observations only; the rules are HostCore's)."""

    def __init__(self, *args, on_state=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.states = []
        self.playback = []  # (utterance_id, started|ended|stopped)
        self.on_state = on_state

    def _on_state(self, header, payload):
        item = {key: header[key] for key in STATE_KEYS}
        self.states.append(item)
        if self.on_state is not None:
            self.on_state(item)
        super()._on_state(header, payload)

    def _send(self, header, payload=b""):
        if header.get("type") == "playback":
            self.playback.append((header["utterance_id"], header["state"]))
        return super()._send(header, payload)


def child_argv(scenario, wake_model_sha256=FIXTURE_WAKE_MODEL_SHA256):
    if scenario == "selftrigger":
        return [
            sys.executable,
            "-m",
            "friday_voice.e2e_child",
            "--wake-model-sha256",
            wake_model_sha256,
        ]
    return [sys.executable, "-m", "friday_voice", "run"]


class Driver:
    """One host, one child process, a 20 ms capture pump on the real clock."""

    def __init__(self, credential, argv, *, mode="push_to_talk", residual=None, emit=print):
        self.clock = RealClock()
        self.emit = emit
        echo = EchoDouble(residual=residual, delay_ms=40) if residual is not None else None
        self.core = RecordingHost(
            self.clock,
            credential=credential,
            mode=mode,
            paced_playback=True,
            echo=echo,
            on_state=lambda item: emit({"event": "state", **item}),
        )
        self.host = ProcessHost(self.core, argv)
        self.feed = deque()
        self._stop = threading.Event()
        self._pump_thread = None

    # --- lifecycle ---

    def start(self):
        self.host.start()
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=5)
        return self.host.stop()

    def _pump(self):
        due = time.monotonic()
        while not self._stop.is_set():
            with self.host.lock:
                self.core.tick()
                pcm = self.feed.popleft() if self.feed else SILENCE
                self.core.feed_audio(pcm)
                self.host.flush()
            due += FRAME_MS / 1000
            time.sleep(max(0.0, due - time.monotonic()))

    # --- helpers ---

    def wait(self, predicate, timeout=TURN_TIMEOUT_S):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.host.lock:
                if predicate(self.core):
                    return True
            time.sleep(0.02)
        return False

    def call(self, method, *args):
        return self.host.call(method, *args)

    def speak(self, frames):
        """Queue capture frames and wait until the pump fed them all."""
        self.feed.extend(frames)
        self.wait(lambda _core: not self.feed, timeout=30)

    def ptt_turn(self, frames=None):
        """Hold the hotkey over one question; wait until the child settled
        again (the reply played out, or a refusal / storm / error)."""
        mark = len(self.core.states)
        self.call("ptt", "down")
        self.speak(frames if frames is not None else wav_frames(QUESTION_WAV))
        self.call("ptt", "up")
        return self.wait(lambda core: _settled_after(core, mark))

    def observations(self):
        with self.host.lock:
            core = self.core
            states = [item["state"] for item in core.states]
            return {
                "states": [s for i, s in enumerate(states) if i == 0 or states[i - 1] != s],
                "last_state": core.states[-1] if core.states else None,
                "indicator": core.indicator,
                "mic_held": core.mic_held,
                "releases": list(core.releases),
                "failures": [code for code, _at in core.failures],
                "earcons": list(core.earcons),
                "logs": list(core.logs),
                "started": _utterances(core, "started"),
                "ended": _utterances(core, "ended"),
                "stopped": _utterances(core, "stopped"),
                "dropped_play": core.dropped_play,
            }


def _utterances(core, state):
    return [uid for uid, reported in core.playback if reported == state]


def _settled_after(core, mark):
    """A state after `mark` went through `thinking` (or was refused) and the
    child is now settled with nothing left to play."""
    later = [item["state"] for item in core.states[mark:]]
    if not later or core.playback_pending:
        return False
    if later[-1] in ("storm_muted", "off"):
        return True
    return "thinking" in later and later[-1] in SETTLED


def _credential(server_url, token):
    expires = datetime.now(UTC) + timedelta(minutes=14)
    return {
        "token": token,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server_url": server_url.rstrip("/"),
        "ca_sha256": None,
    }


# --- scenarios ---


def _await(emit, read_line, step, driver):
    emit({"event": "await", "step": step, **driver.observations()})
    return read_line().strip()


def scenario_ptt(driver, emit, read_line):
    driver.wait(lambda core: core.child_state == "armed_ptt", timeout=30)
    return {"turns": [driver.ptt_turn()]}


def scenario_barge_in(driver, emit, read_line):
    driver.wait(lambda core: core.child_state == "armed_ptt", timeout=30)
    mark = len(driver.core.states)
    driver.call("ptt", "down")
    driver.speak(wav_frames(QUESTION_WAV))
    driver.call("ptt", "up")
    speaking = driver.wait(lambda core: ("t1s0", "started") in core.playback)
    time.sleep(0.3)  # Friday is mid-sentence
    second = driver.ptt_turn()
    return {"turns": [speaking, second], "mark": mark}


def scenario_kill_switch(driver, emit, read_line):
    driver.wait(lambda core: core.child_state == "armed_ptt", timeout=30)
    first = driver.ptt_turn()
    _await(emit, read_line, "kill_switch", driver)
    releases_before = len(driver.core.releases)
    driver.call("ptt", "down")
    driver.speak(wav_frames(QUESTION_WAV))
    driver.call("ptt", "up")
    released = driver.wait(
        lambda core: any(r.startswith("child:") for r in core.releases[releases_before:]),
        timeout=30,
    )
    refused = driver.observations()
    # The gateway closes the node socket on the kill switch: the host's link
    # goes down and it releases the mic itself (ADR-013 decision 6).
    driver.call("set_link", False)
    driver.wait(lambda core: core.indicator == "off", timeout=5)
    return {"turns": [first], "released_after_refusal": released, "after_refusal": refused}


def scenario_storm(driver, emit, read_line):
    driver.wait(lambda core: core.child_state == "armed_ptt", timeout=30)
    turns = []
    for _ in range(6):
        turns.append(driver.ptt_turn())
        if driver.core.child_state == "storm_muted":
            break
    muted = driver.core.child_state == "storm_muted"
    _await(emit, read_line, "storm_muted", driver)
    # The owner released the mute on screen; the child re-arms only after a
    # local mute on -> off cycle (branch 3 note 5).
    driver.call("set_mute", True)
    driver.wait(lambda core: core.child_state == "muted", timeout=10)
    driver.call("set_mute", False)
    driver.wait(lambda core: core.child_state == "armed_ptt", timeout=10)
    ended_before = len(_utterances(driver.core, "ended"))
    after = driver.ptt_turn()
    answered = len(_utterances(driver.core, "ended")) > ended_before
    return {"turns": turns, "storm_muted": muted, "after_release": after, "answered": answered}


def scenario_selftrigger(driver, emit, read_line):
    armed = driver.wait(lambda core: core.child_state == "armed_wake", timeout=30)
    first = driver.ptt_turn()
    # Long enough for the echo capture to end, upload and be judged.
    time.sleep(config.WAKE_NO_SPEECH_S + 3)
    driver.wait(lambda core: core.child_state in SETTLED and not core.playback_pending)
    return {"armed": armed, "turns": [first]}


RUNNERS = {
    "ptt": scenario_ptt,
    "node_action": scenario_ptt,
    "barge_in": scenario_barge_in,
    "kill_switch": scenario_kill_switch,
    "storm": scenario_storm,
    "selftrigger": scenario_selftrigger,
}


def run(scenario, server_url, token, *, emit=None, read_line=None) -> int:
    emit = emit or (lambda doc: print(json.dumps(doc, sort_keys=True), flush=True))
    read_line = read_line or sys.stdin.readline
    if scenario == "selftrigger":
        driver = Driver(
            _credential(server_url, token),
            child_argv(scenario),
            mode="always_on",
            residual=1.0,
            emit=emit,
        )
    else:
        driver = Driver(_credential(server_url, token), child_argv(scenario), emit=emit)
    driver.start()
    try:
        outcome = RUNNERS[scenario](driver, emit, read_line)
    finally:
        code = driver.stop()
    emit(
        {
            "event": "result",
            "scenario": scenario,
            "child_exit": code,
            "outcome": outcome,
            **driver.observations(),
        }
    )
    return 0


def main(args) -> int:
    if args.scenario not in SCENARIOS:
        print(f"unknown scenario (one of {', '.join(SCENARIOS)})", file=sys.stderr)
        return 2
    with open(args.token_file, encoding="utf-8") as handle:
        token = handle.read().strip()
    return run(args.scenario, args.server, token)
