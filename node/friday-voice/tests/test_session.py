"""The session state machine (brief D9): `friday_voice.session.TRANSITIONS`
checked cell by cell against the real child.

For every state of D6's `state` message the real child (`ChildCore` +
`VoicePipeline`, behind the reference host with paced playback, a manual
clock and `FakeFriday`) is driven into that state, one host input is
applied, and the state it then reports and the frames it sent (other than
`state`; repeats of one type collapsed, e.g. a `stop_playback` per open
utterance) must equal the table's cell. 9 states x 16 inputs."""

import itertools

import numpy as np
import pytest

from friday_voice import config, session
from friday_voice.client import FridayError
from friday_voice.fakes import FakeFriday
from friday_voice.pipeline import AllowAlwaysOn
from friday_voice.protocol.messages import STATES
from friday_voice.replay import DEV_CREDENTIAL, ReplaySession
from friday_voice.turn import EnergyVad, ScriptedTurnEnd
from friday_voice.wake import ScriptedWakeDetector

FRAME = config.ms_to_bytes(20)
SILENCE = b"\x00" * FRAME
WAKE_MARK = 3_333


def loud(index=0):
    return np.full(FRAME // 2, 1000 + index, dtype="<i2").tobytes()


WAKE = np.full(FRAME // 2, WAKE_MARK, dtype="<i2").tobytes()

SLOW_REPLY = [
    {"type": "chunk", "text": "Bugün iki toplantın var, ikisi de öğleden sonra. "},
    {"type": "chunk", "text": "İlki saat üçte, ikincisi beşte başlıyor. "},
    (60_000, {"type": "done"}),
]


def wake_scores(_index, pcm):
    return 900 if np.frombuffer(pcm, dtype="<i2")[0] == WAKE_MARK else 0


def build(**latencies):
    fake = FakeFriday(replies=[SLOW_REPLY], latencies={"tts": 100, **latencies})
    replay = ReplaySession(
        client=fake,
        mode="push_to_talk",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        wake_detector=ScriptedWakeDetector(wake_scores),
        arming=AllowAlwaysOn(),
        paced_playback=True,
    ).start()
    return replay, fake


def ptt_turn(replay):
    replay.ptt("down")
    for index in range(20):
        replay.frame(loud(index))
    replay.ptt("up")


# --- reaching each state ---


def reach(state):
    if state == "thinking":
        replay, fake = build(stt=60_000)
    else:
        replay, fake = build()
    host = replay.host
    if state == "armed_wake":
        host.set_mode("always_on")
        replay.pump()
    elif state == "listening":
        replay.ptt("down")
    elif state == "thinking":
        ptt_turn(replay)
        replay.frame(SILENCE)
    elif state == "speaking":
        ptt_turn(replay)
        for _ in range(500):
            if replay.core.current_state() == "speaking":
                break
            replay.frame(SILENCE)
        replay.silence(config.BARGE_IN_AEC_GUARD_MS + 100)
    elif state == "muted":
        host.set_mute(True)
        replay.pump()
    elif state == "off":
        host.set_mode("off")
        replay.pump()
    elif state == "storm_muted":
        fake.fail_next("stt", FridayError(423, "voice.live_muted"))
        ptt_turn(replay)
        replay.settle()
    elif state == "error":
        fake.revoked.add(DEV_CREDENTIAL["token"])
        ptt_turn(replay)
        replay.settle()
    assert replay.core.current_state() == state
    return replay


# --- applying each input ---


def _playback_ended(replay):
    host = replay.host
    ids = [entry[0] for entry in host._queue] or ["t9s9"]
    host._queue.clear()
    for utterance_id in ids:
        host._utterances.pop(utterance_id, None)
        host._send({"type": "playback", "utterance_id": utterance_id, "state": "ended", "at_ms": 0})
    replay.pump()


def _speech(replay):
    for index in range(15):  # 300 ms
        replay.frame(loud(index))


FRESH = {**DEV_CREDENTIAL, "token": "jpat_" + "FreshSessionT0ken" * 2}

APPLY = {
    "mute_on": lambda r: (r.host.set_mute(True), r.pump()),
    "mute_unreadable": lambda r: (r.host.set_mute("unreadable"), r.pump()),
    "mute_off": lambda r: (r.host.set_mute(False), r.pump()),
    "link_down": lambda r: (r.host.set_link(False), r.pump()),
    "link_up": lambda r: (r.host.set_link(True), r.pump()),
    "mode_off": lambda r: (r.host.set_mode("off"), r.pump()),
    "mode_ptt": lambda r: (r.host.set_mode("push_to_talk"), r.pump()),
    "mode_always_on": lambda r: (r.host.set_mode("always_on"), r.pump()),
    "ptt_down": lambda r: r.ptt("down"),
    "ptt_up": lambda r: r.ptt("up"),
    "speech": _speech,
    "silence": lambda r: r.frame(SILENCE),
    "wake": lambda r: r.frame(WAKE),
    "playback_ended": _playback_ended,
    "credential": lambda r: r.credential(FRESH),
    "shutdown": lambda r: (r.host.shutdown(), r.pump()),
}


def observe(replay, before):
    sent = [frame["type"] for frame in replay.frames_to_host[before:] if frame["type"] != "state"]
    collapsed = tuple(kind for kind, _group in itertools.groupby(sent))
    state = session.EXIT if replay.core.finished else replay.core.current_state()
    return state, collapsed


def test_the_table_is_exhaustive():
    assert set(session.TRANSITIONS) == set(STATES)
    for state in STATES:
        assert set(session.TRANSITIONS[state]) == set(session.INPUTS)
        for next_state, frames in session.TRANSITIONS[state].values():
            assert next_state in (*STATES, session.EXIT)
            assert set(frames) <= {"release_mic", "stop_playback", "earcon", "play"}
    with pytest.raises(ValueError):
        session._row(mute_on=("muted", ()))
    with pytest.raises(KeyError):
        session.transition("armed_ptt", "teleport")


@pytest.mark.parametrize(
    ("state", "event"),
    [(state, event) for state in STATES for event in session.INPUTS],
    ids=lambda value: value,
)
def test_every_state_and_input(state, event):
    replay = reach(state)
    before = len(replay.frames_to_host)
    APPLY[event](replay)
    assert observe(replay, before) == session.transition(state, event)


def test_releases_carry_the_reason_of_the_input():
    replay = reach("speaking")
    replay.host.set_link(False)
    replay.pump()
    assert replay.sent("release_mic")[-1] == {"type": "release_mic", "reason": "link_down"}


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"blocking_reason": "muted", "storm_muted": True, "turn_phase": "replying"}, "muted"),
        ({"blocking_reason": "mute_unreadable"}, "muted"),
        ({"blocking_reason": "link_down", "refused": True}, "off"),
        ({"blocking_reason": "mode_off"}, "off"),
        ({"blocking_reason": "credential_expired", "turn_phase": "capturing"}, "error"),
        ({"storm_muted": True, "refused": True}, "storm_muted"),
        ({"refused": True, "turn_phase": "capturing"}, "error"),
        ({"error_hold": True, "wake_armed": True}, "error"),
        ({"turn_phase": "capturing", "playing": True}, "listening"),
        ({"turn_phase": "transcribing"}, "thinking"),
        ({"turn_phase": "replying", "playing": True}, "speaking"),
        ({"ptt_down": True}, "listening"),
        ({"wake_armed": True}, "armed_wake"),
        ({}, "armed_ptt"),
    ],
)
def test_derive_state_precedence(kwargs, expected):
    assert session.derive_state(**kwargs) == expected
