"""Barge-in (brief D9) and the echo scenarios (D10): the reference host with
paced playback and its `EchoDouble`, the real child, `FakeFriday`, one
manual clock. No network, no model, no real clock."""

import numpy as np
import pytest

from friday_voice import config
from friday_voice.barge_in import BargeInMonitor
from friday_voice.fakes import FakeFriday
from friday_voice.pipeline import AllowAlwaysOn
from friday_voice.ports import ManualClock
from friday_voice.reference.echo import EchoDouble
from friday_voice.replay import ReplaySession
from friday_voice.turn import EnergyVad, ScriptedTurnEnd, ScriptedVad
from friday_voice.wake import ScriptedWakeDetector

FRAME_MS = 20
FRAME = config.ms_to_bytes(FRAME_MS)
SILENCE = b"\x00" * FRAME


def loud(index):
    """A speech frame (EnergyVad) carrying the marker 1000 + index."""
    return np.full(FRAME // 2, 1000 + index, dtype="<i2").tobytes()


def quiet(index):
    """A frame below the VAD level carrying the marker 100 + index."""
    return np.full(FRAME // 2, 100 + index, dtype="<i2").tobytes()


def markers(pcm):
    samples = np.frombuffer(pcm, dtype="<i2")
    return [int(samples[i]) for i in range(0, len(samples), FRAME // 2)]


SENTENCES = [
    "Bugün iki toplantın var, ikisi de öğleden sonra.",
    "İlki saat üçte, ikincisi beşte başlıyor.",
    "Akşam ise tamamen serbestsin.",
]
# The stream stays open after the first sentences, so a barge-in finds it.
SLOW_REPLY = [
    {"type": "chunk", "text": SENTENCES[0] + " "},
    {"type": "chunk", "text": SENTENCES[1] + " "},
    (20_000, {"type": "chunk", "text": SENTENCES[2]}),
    (10, {"type": "done"}),
]
SHORT_REPLY = [{"type": "chunk", "text": "Tamam, onu bırakıyorum."}, {"type": "done"}]


def session_for(
    *, replies=None, transcripts=("bugün ne var", "dur onu değil"), tts_ms=400, **kwargs
):
    fake = FakeFriday(
        transcripts=list(transcripts),
        replies=replies or [SLOW_REPLY, SHORT_REPLY],
        latencies={"tts": tts_ms},
    )
    options = {
        "client": fake,
        "vad": EnergyVad(),
        "turn_end": ScriptedTurnEnd([True]),
        "paced_playback": True,
    } | kwargs
    return ReplaySession(**options).start(), fake


def ptt_turn(session, *, before=()):
    for pcm in before:  # pre-session audio: never consumed into a turn
        session.frame(pcm)
    session.ptt("down")
    for index in range(20):
        session.frame(loud(index))
    session.ptt("up")


def until_speaking(session, *, limit_ms=10_000, frame=SILENCE):
    for _ in range(limit_ms // FRAME_MS):
        if session.states()[-1] == "speaking":
            return session.clock.monotonic_ms()
        session.frame(frame)
    raise AssertionError(f"never spoke: {session.states()}")


def stops(session):
    return session.sent_at("stop_playback")


# --- the monitor alone ---


def monitor(probabilities):
    clock = ManualClock()
    vad = ScriptedVad(probabilities)
    return clock, BargeInMonitor(vad, clock)


def test_the_monitor_fires_after_250_ms_of_speech_past_the_guard():
    clock, mon = monitor([1000])
    mon.begin_reply()
    mon.playback_started()
    clock.advance(config.BARGE_IN_AEC_GUARD_MS)
    fired = None
    frames = 0
    while fired is None:
        fired = mon.feed(SILENCE)
        frames += 1
        clock.advance(FRAME_MS)
    assert (
        frames * FRAME_MS == config.BARGE_IN_MIN_MS + (FRAME_MS - config.BARGE_IN_MIN_MS % 20) % 20
    )
    assert fired.onset_ms == config.BARGE_IN_AEC_GUARD_MS
    assert fired.fired_ms - fired.onset_ms == (frames - 1) * FRAME_MS
    assert not mon.active  # one barge-in per playback


def test_the_monitor_ignores_speech_inside_the_guard_and_below_the_threshold():
    clock, mon = monitor([config.BARGE_IN_VAD - 1])
    mon.playback_started()
    for _ in range(100):
        assert mon.feed(SILENCE) is None
        clock.advance(FRAME_MS)
    clock2, mon2 = monitor([1000])
    mon2.playback_started()
    for _ in range(config.BARGE_IN_AEC_GUARD_MS // FRAME_MS):
        assert mon2.feed(SILENCE) is None  # loud, but AEC is still converging
        clock2.advance(FRAME_MS)


def test_the_monitor_needs_consecutive_speech():
    pattern = [1000] * 12 + [0] + [1000] * 12 + [0]
    clock, mon = monitor(lambda index, _pcm: pattern[index % len(pattern)])
    mon.playback_started()
    clock.advance(config.BARGE_IN_AEC_GUARD_MS)
    for _ in range(200):
        assert mon.feed(SILENCE) is None
        clock.advance(FRAME_MS)


def test_the_monitor_does_nothing_while_nothing_plays():
    clock, mon = monitor([1000])
    for _ in range(50):
        assert mon.feed(SILENCE) is None
        clock.advance(FRAME_MS)


def test_the_preroll_is_bounded_and_starts_after_begin_reply():
    clock, mon = monitor(lambda _index, pcm: 1000 if markers(pcm)[0] >= 1000 else 0)
    mon.remember(quiet(99))  # an earlier phase
    mon.begin_reply()
    mon.playback_started()
    for index in range(40):
        assert mon.feed(quiet(index)) is None
        clock.advance(FRAME_MS)
    fired = None
    index = 0
    while fired is None:
        fired = mon.feed(loud(index))
        index += 1
        clock.advance(FRAME_MS)
    got = markers(fired.preroll)
    preroll_frames = config.BARGE_IN_PREROLL_MS // FRAME_MS
    assert got[:preroll_frames] == [100 + i for i in range(40 - preroll_frames, 40)]
    assert got[preroll_frames:] == [1000 + i for i in range(index)]
    assert 199 not in got


# --- barge-in through the reference host and the real child ---


@pytest.mark.parametrize("after_guard_ms", [0, 200, 1_000])
def test_speech_during_playback_stops_it_within_the_budget(after_guard_ms):
    session, fake = session_for()
    ptt_turn(session)
    started = until_speaking(session)
    for _ in range((config.BARGE_IN_AEC_GUARD_MS + after_guard_ms) // FRAME_MS):
        session.frame(SILENCE)
    assert stops(session) == []
    onset = session.clock.monotonic_ms()
    for index in range(20):
        session.frame(loud(index))
    (first_stop, header), *rest = stops(session)
    assert header["utterance_id"] == "t1s0"
    assert {frame["utterance_id"] for _at, frame in rest} <= {"t1s1", "t1s2"}
    # Onset -> stop_playback: the 250 ms persistence rule, then <= 100 ms.
    assert first_stop - (onset + config.BARGE_IN_MIN_MS) <= 100
    assert session.host.stopped_at[0] == ("t1s0", first_stop)
    # The run's last frame arrives one frame before the 250 ms of audio end.
    earliest = config.BARGE_IN_AEC_GUARD_MS + config.BARGE_IN_MIN_MS - FRAME_MS
    assert first_stop - started >= earliest
    assert session.states()[-1] == "listening"
    assert session.host.mic_held and session.sent("release_mic") == []


def test_barge_in_cancels_the_reply_stream_and_the_synthesis_in_flight():
    session, fake = session_for(tts_ms=2_000)  # the prefetched sentence is still in flight
    ptt_turn(session)
    until_speaking(session)
    session.silence(config.BARGE_IN_AEC_GUARD_MS)
    tts_before = len(fake.calls("tts"))
    for index in range(15):
        session.frame(loud(index))
    assert fake.aborts == 1  # the chat SSE closed before its end
    trace = fake.calls("stt")[0]["trace_id"]
    assert fake.speech_aborts and {t for t, _i in fake.speech_aborts} == {trace}
    session.silence(1_000)
    assert [c["text"] for c in fake.calls("tts")[:tts_before]] == SENTENCES[
        : len(fake.calls("tts")[:tts_before])
    ]
    assert SENTENCES[2] not in [c["text"] for c in fake.calls("tts")]
    assert session.pipeline.completed[0].interrupted is True


def test_the_next_utterance_has_the_preroll_and_nothing_from_before_the_session():
    session, fake = session_for()
    ptt_turn(session, before=[quiet(50 + i) for i in range(30)])
    until_speaking(session)
    for index in range(30):
        session.frame(quiet(index))
    for index in range(20):
        session.frame(loud(index))
    session.silence(400)
    session.settle()
    first, second = fake.calls("stt")
    assert second["trigger"] == "barge_in"
    assert second["conversation_id"] == first["conversation_id"]
    assert len(fake.calls("session")) == 1  # the session was open: no second wake needed
    got = markers(second["pcm"])
    preroll_frames = config.BARGE_IN_PREROLL_MS // FRAME_MS
    assert got[:preroll_frames] == [100 + i for i in range(30 - preroll_frames, 30)]
    assert got[preroll_frames : preroll_frames + 20] == [1000 + i for i in range(20)]
    assert not {100 + 50 + i for i in range(30)} & set(got)  # pre-session audio
    assert [c["text"] for c in fake.calls("chat")] == ["bugün ne var", "dur onu değil"]
    assert session.states()[-1] == "armed_ptt"


def test_interrupted_at_is_recorded_and_reported_with_the_next_turn():
    session, fake = session_for()
    ptt_turn(session)
    until_speaking(session)
    session.silence(500)
    for index in range(20):
        session.frame(loud(index))
    stop_at = stops(session)[0][0]
    session.silence(400)
    session.settle()
    interrupted = session.pipeline.completed[0]
    started = interrupted.timer.marks["playback_start"]
    assert interrupted.timer.extra["interrupted_at_ms"] == stop_at - started
    second_trace = fake.calls("stt")[1]["trace_id"]
    reported = [c["timings"] for c in fake.calls("tts") if c["trace_id"] == second_trace]
    assert reported and all(t["interrupted_at_ms"] == stop_at - started for t in reported)
    first_trace = fake.calls("stt")[0]["trace_id"]
    assert all(
        "interrupted_at_ms" not in c["timings"]
        for c in fake.calls("tts")
        if c["trace_id"] == first_trace
    )


def test_speech_shorter_than_250_ms_does_not_barge_in():
    session, fake = session_for()
    ptt_turn(session)
    until_speaking(session)
    session.silence(config.BARGE_IN_AEC_GUARD_MS)
    for _burst in range(3):
        for index in range(config.BARGE_IN_MIN_MS // FRAME_MS - 1):
            session.frame(loud(index))
        session.frame(SILENCE)
    assert stops(session) == []
    assert session.states()[-1] == "speaking"
    assert fake.aborts == 0


def test_speech_in_the_first_300_ms_of_playback_does_not_barge_in():
    session, fake = session_for()
    ptt_turn(session)
    until_speaking(session)
    for index in range(config.BARGE_IN_AEC_GUARD_MS // FRAME_MS):
        session.frame(loud(index))
    session.frame(SILENCE)
    assert stops(session) == []
    assert session.states()[-1] == "speaking"


def test_without_a_vad_there_is_no_barge_in_but_the_hotkey_still_cuts():
    session, fake = session_for(vad=None, turn_end=None)
    ptt_turn(session)
    until_speaking(session)
    for index in range(40):
        session.frame(loud(index))
    assert stops(session) == []
    session.ptt("down")
    assert stops(session)[0][1]["utterance_id"] == "t1s0"
    assert session.states()[-1] == "listening"


def test_an_echo_of_friday_after_a_barge_in_is_discarded_and_the_session_continues():
    session, fake = session_for(transcripts=["bugün ne var", SENTENCES[0]])
    ptt_turn(session)
    until_speaking(session)
    session.silence(config.BARGE_IN_AEC_GUARD_MS)
    for index in range(20):
        session.frame(loud(index))
    session.silence(400)
    session.settle()
    assert len(fake.calls("chat")) == 1  # the echo never became a turn
    assert session.sent("state")[-1]["wake_discards_today"] == 1
    assert session.pipeline.self_trigger.discarded == {"window": 1, "content": 0}
    assert session.sent("state")[-1]["session_open"] is True
    assert session.states()[-1] == "armed_ptt"


# --- echo scenarios (D10): residual 0.0 / 0.3 / 1.0 ---


def echo_session(residual, **kwargs):
    echo = EchoDouble(residual=residual, delay_ms=40)
    # STT hears what leaked: the start of the sentence Friday was saying.
    session, fake = session_for(
        transcripts=["bugün ne var", "bugün iki toplantın var ikisi de"],
        replies=[SLOW_REPLY[:2] + [{"type": "done"}], SHORT_REPLY],
        echo=echo,
        **kwargs,
    )
    return session, fake, echo


@pytest.mark.parametrize("residual", [0.3, 1.0])
def test_friday_never_answers_its_own_echo(residual):
    session, fake, echo = echo_session(residual)
    ptt_turn(session)
    until_speaking(session)
    session.silence(3_000)  # nobody speaks: only the echo reaches the mic
    session.settle()
    session.silence(500)
    # The leak is loud enough to stop playback (a failing canceller) ...
    assert stops(session)
    # ... but the content rule discards its transcript: no turn, nothing spoken.
    assert len(fake.calls("chat")) == 1
    assert [c["text"] for c in fake.calls("tts")] == SENTENCES[: len(fake.calls("tts"))]
    assert session.pipeline.self_trigger.discarded_self == 1
    assert session.sent("state")[-1]["wake_discards_today"] == 1
    assert session.sent("state")[-1]["session_open"] is True
    assert session.states()[-1] == "armed_ptt"
    assert session.host.mic_held
    # The session continues: the owner's next push-to-talk turn is answered.
    fake.transcripts.append("yarın ne var")
    ptt_turn(session)
    session.settle()
    session.silence(3_000)
    assert len(fake.calls("chat")) == 2


def test_a_perfect_canceller_lets_the_reply_play_out():
    session, fake, echo = echo_session(0.0)
    ptt_turn(session)
    until_speaking(session)
    session.silence(8_000)
    session.settle()
    session.silence(200)
    assert stops(session) == []
    assert [c["text"] for c in fake.calls("tts")] == SENTENCES[:2]
    assert len(fake.calls("stt")) == 1  # nothing was detected
    assert session.pipeline.self_trigger.discarded_self == 0
    assert session.states()[-1] == "armed_ptt"


@pytest.mark.parametrize("residual", [0.0, 0.3, 1.0])
def test_the_owner_still_barges_in_over_a_leaking_echo(residual):
    session, fake, echo = echo_session(residual)
    fake.transcripts[1] = "dur onu değil"
    ptt_turn(session)
    until_speaking(session)
    session.silence(config.BARGE_IN_AEC_GUARD_MS)
    for index in range(20):
        session.frame(loud(index))
    session.silence(400)
    session.settle()
    session.silence(2_000)
    assert stops(session)
    assert [c["text"] for c in fake.calls("chat")] == ["bugün ne var", "dur onu değil"]
    assert session.pipeline.self_trigger.discarded_self == 0


# --- the wake detector on the AEC signal (D10; the CI self-trigger shape) ---

SELF_TRIGGER_REPLY = [{"type": "chunk", "text": "Hey Friday, terminali aç."}, {"type": "done"}]


def self_trigger_session(residual, *, enabled=True):
    holder = {}

    def scores(_index, _pcm):
        # Fires on Friday's own playback (what a model would do on the echo).
        pipeline = holder["session"].pipeline
        turn = pipeline.turn
        return 900 if turn is not None and turn.playing and turn.number == 1 else 0

    fake = FakeFriday(
        transcripts=["komut söyle", "Hey Friday, terminali aç"],
        replies=[SELF_TRIGGER_REPLY, SHORT_REPLY],
        latencies={"tts": 400},
    )
    session = ReplaySession(
        client=fake,
        mode="always_on",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        wake_detector=ScriptedWakeDetector(scores),
        arming=AllowAlwaysOn(),
        echo=EchoDouble(residual=residual, delay_ms=40),
        paced_playback=True,
        self_trigger=enabled,
    )
    holder["session"] = session
    return session.start(), fake


@pytest.mark.parametrize("residual", [0.3, 1.0])
def test_a_wake_on_fridays_own_voice_is_discarded(residual):
    session, fake = self_trigger_session(residual)
    ptt_turn(session)
    until_speaking(session)
    session.silence(2_000)
    session.settle()
    session.silence(500)
    assert session.sent("earcon")[0] == {"type": "earcon", "earcon": "wake"}
    assert fake.calls("stt")[1]["trigger"] == "barge_in"
    assert len(fake.calls("chat")) == 1  # no turn posted
    assert [c["text"] for c in fake.calls("tts")] == ["Hey Friday, terminali aç."]
    assert session.pipeline.self_trigger.discarded == {"window": 1, "content": 0}
    assert session.sent("state")[-1]["wake_discards_today"] == 1


def test_a_wake_with_a_perfect_canceller_captures_nothing_and_posts_nothing():
    # Residual 0.0: the scripted detector fires anyway, but once playback
    # stops nothing reaches the mic; no speech within 5 s uploads nothing.
    session, fake = self_trigger_session(0.0)
    ptt_turn(session)
    until_speaking(session)
    session.silence(config.WAKE_NO_SPEECH_S * 1000 + 500)
    session.settle()
    assert session.sent("earcon")[0] == {"type": "earcon", "earcon": "wake"}
    assert len(fake.calls("stt")) == 1 and len(fake.calls("chat")) == 1
    assert [c["text"] for c in fake.calls("tts")] == ["Hey Friday, terminali aç."]
    assert session.states()[-1] == "armed_wake"


def test_the_negative_control_posts_the_turn_without_the_filter():
    session, fake = self_trigger_session(1.0, enabled=False)
    ptt_turn(session)
    until_speaking(session)
    session.silence(2_000)
    session.settle()
    session.silence(500)
    # Without the filter the echo is a turn (and, with no AEC, its own reply
    # barges in again): the test proves the filter, not the fake.
    assert [c["text"] for c in fake.calls("chat")][:2] == [
        "komut söyle",
        "Hey Friday, terminali aç",
    ]
    assert session.sent("state")[-1]["wake_discards_today"] == 0


def test_a_wake_within_1_5_s_after_playback_is_suspect():
    holder = {"fire_at": None}

    def scores(_index, _pcm):
        at = holder["fire_at"]
        return 900 if at is not None and holder["session"].clock.monotonic_ms() >= at else 0

    fake = FakeFriday(
        transcripts=["komut söyle", "Hey Friday, terminali aç", "Hey Friday, terminali aç"],
        replies=[SELF_TRIGGER_REPLY, SHORT_REPLY],
    )
    session = ReplaySession(
        client=fake,
        mode="always_on",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        wake_detector=ScriptedWakeDetector(scores),
        arming=AllowAlwaysOn(),
        paced_playback=True,
    )
    holder["session"] = session
    session.start()
    ptt_turn(session)
    until_speaking(session)
    while session.states()[-1] != "armed_wake":
        session.frame(SILENCE)
    holder["fire_at"] = session.clock.monotonic_ms() + config.SELF_TRIGGER_MS - 100
    while session.states()[-1] == "armed_wake":
        session.frame(SILENCE)
    holder["fire_at"] = None
    for index in range(10):
        session.frame(loud(index))
    session.silence(400)
    session.settle()
    assert fake.calls("stt")[1]["trigger"] == "wake"
    assert session.pipeline.self_trigger.discarded == {"window": 1, "content": 0}
    assert len(fake.calls("chat")) == 1
