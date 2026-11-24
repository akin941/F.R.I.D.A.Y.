"""Always-on (Phase 11 brief D11, D12; branch 5 Tests): the arming policy,
policy polling, the wake-storm rule, the session close and the fail-closed
inputs -- the reference host and the real child on a manual clock, with
`FakeFriday` scripting the voice-policy answer. No network, no model."""

import pytest
from test_pipeline import REPLY, SILENCE, marked

from friday_voice import config
from friday_voice.arming import ArmingPolicy
from friday_voice.client import FridayError
from friday_voice.fakes import FakeFriday
from friday_voice.replay import ReplaySession
from friday_voice.turn import EnergyVad, ScriptedTurnEnd
from friday_voice.wake import ScriptedWakeDetector

SHA = "e" * 64
PERMIT = {"always_on_permitted": True, "reasons": []}
REFUSE = {"always_on_permitted": False, "reasons": ["far_above_budget"]}


def world(
    scores=None,
    *,
    policy=PERMIT,
    mode="always_on",
    models_verified=True,
    replies=None,
    transcripts=("bugün ne var",),
):
    fake = FakeFriday(replies=replies or [REPLY] * 10, transcripts=transcripts)
    fake.policy = dict(policy)
    session = ReplaySession(
        client=fake,
        mode=mode,
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        wake_detector=ScriptedWakeDetector(scores or {}, model_sha256=SHA),
        arming=ArmingPolicy(models_verified=models_verified),
    ).start()
    return session, fake


def always(score=900):
    return lambda _index, _pcm: score


WAKE = marked(900)  # the one frame the marker detector scores as a wake


def on_marker(_index, pcm):
    return 900 if pcm == WAKE else 0


def reread(session):
    """Let the 10-minute policy re-read happen and land."""
    session.advance(config.POLICY_POLL_S * 1000)
    session.settle()


def speak(session, frames=10, start=0):
    for index in range(frames):
        session.frame(marked(start + index))
    session.silence(400)
    session.settle()


def wake_turn(session):
    """One frame that the detector scores as a wake, then an utterance."""
    session.frame(SILENCE)
    speak(session)


# --- the arming policy (a)-(f) --------------------------------------------------


def test_all_conditions_true_arms_wake_mode_and_a_wake_turn_completes():
    session, fake = world({0: 900})
    assert session.states()[-1] == "armed_wake"
    (policy,) = fake.calls("policy")
    assert policy["model_sha256"] == SHA
    assert (policy["threshold"], policy["refractory_ms"]) == (
        config.WAKE_THRESHOLD,
        config.WAKE_REFRACTORY_MS,
    )
    wake_turn(session)
    (stt,) = fake.calls("stt")
    assert stt["trigger"] == "wake"
    assert fake.calls("session")[0]["trigger"] == "wake"
    assert len(session.pipeline.completed) == 1 and session.sent("play")
    assert session.states()[-1] == "armed_wake"


def _mode_ptt(session):
    session.host.set_mode("push_to_talk")


def _mode_off(session):
    session.host.set_mode("off")


def _link_down(session):
    session.host.set_link(False)


def _muted(session):
    session.host.set_mute(True)


def _mute_unreadable(session):
    session.host.set_mute("unreadable")


def _storm(session):
    session.pipeline._wake_storm()
    session.core.flush_pipeline()


CONDITIONS = {
    "a_mode_push_to_talk": ({}, _mode_ptt, "armed_ptt"),
    "a_mode_off": ({}, _mode_off, "off"),
    "b_policy_refuses": ({"policy": REFUSE}, None, "armed_ptt"),
    "c_link_down": ({}, _link_down, "off"),
    "d_muted": ({}, _muted, "muted"),
    "d_mute_unreadable": ({}, _mute_unreadable, "muted"),
    "e_storm_muted": ({}, _storm, "storm_muted"),
    "f_models_unverified": ({"models_verified": False}, None, "armed_ptt"),
}


@pytest.mark.parametrize("name", sorted(CONDITIONS))
def test_each_condition_false_alone_keeps_wake_mode_off(name):
    kwargs, action, expected_state = CONDITIONS[name]
    session, fake = world(always(), **kwargs)
    if action is not None:
        action(session)
        session.pump()
        session.advance(20)
    assert session.states()[-1] == expected_state
    assert session.pipeline.wake_armed(session.core.mode) is False
    for index in range(30):
        session.host.feed_audio(marked(index))
        session.pump()
        session.advance(20)
    session.settle()
    assert fake.calls("session") == [] and fake.calls("stt") == []
    assert session.core.counters["wake_accepts_today"] == 0


@pytest.mark.parametrize("mode", ["push_to_talk", "off"])
def test_the_server_can_never_arm_when_the_host_mode_is_not_always_on(mode):
    session, fake = world(always(), mode=mode)
    session.advance(config.POLICY_POLL_S * 1000 * 2)
    assert fake.calls("policy") == []  # nothing to ask: the Mac chose this
    assert session.states()[-1] != "armed_wake"


def test_switching_the_mode_to_always_on_reads_the_policy_and_arms():
    session, fake = world(always(), mode="push_to_talk")
    assert session.states()[-1] == "armed_ptt"
    session.host.set_mode("always_on")
    session.pump()
    session.settle()
    assert len(fake.calls("policy")) == 1
    assert session.states()[-1] == "armed_wake"


def test_a_policy_flip_to_false_disarms_at_once():
    session, fake = world()
    assert session.states()[-1] == "armed_wake"
    fake.policy = dict(REFUSE)
    reread(session)  # the 10-minute re-read
    assert len(fake.calls("policy")) == 2
    assert session.states()[-1] == "armed_ptt"
    assert session.pipeline.arming.server_reasons == ("far_above_budget",)
    fake.policy = dict(PERMIT)
    reread(session)
    assert session.states()[-1] == "armed_wake"


def test_a_policy_flip_drops_a_wake_utterance_still_being_captured():
    session, fake = world({0: 900})
    session.frame(SILENCE)  # the wake
    session.frame(marked(1))
    assert session.states()[-1] == "listening"
    fake.policy = dict(REFUSE)
    session.pipeline.arming.checked_at_ms -= config.POLICY_POLL_S * 1000
    session.advance(20)
    session.settle()
    assert session.states()[-1] == "armed_ptt"
    assert fake.calls("stt") == []


def test_the_policy_is_not_read_again_before_ten_minutes():
    session, fake = world()
    session.advance(config.POLICY_POLL_S * 1000 - 1_000)
    assert len(fake.calls("policy")) == 1


def test_a_failed_policy_read_is_a_no_until_a_later_read_permits():
    session, fake = world(policy=PERMIT)
    fake.fail_next("policy", FridayError(0, "network", network=True))
    reread(session)
    assert session.states()[-1] == "armed_ptt"
    session.advance(config.POLICY_RETRY_S * 1000 - 1_000)
    assert session.states()[-1] == "armed_ptt"  # no hammering while it fails
    session.advance(1_000)  # the retry
    session.settle()
    assert session.states()[-1] == "armed_wake"


def test_only_a_literal_true_permits():
    session, _ = world(policy={"always_on_permitted": "true", "reasons": []})
    assert session.states()[-1] == "armed_ptt"


def test_the_policy_is_read_again_at_session_start():
    session, fake = world({0: 900})
    wake_turn(session)
    assert len(fake.calls("policy")) == 2  # first credential, then the session start


def test_the_servers_403_disarms_to_push_to_talk_until_a_permitting_read():
    session, fake = world({0: 900})
    fake.fail_next("stt", FridayError(403, "voice.always_on_not_permitted"))
    wake_turn(session)
    assert session.states()[-1] == "armed_ptt"
    assert session.sent("release_mic") == []  # push-to-talk keeps the mic
    reread(session)
    assert session.states()[-1] == "armed_wake"


# --- the wake storm (D12, client rule) -----------------------------------------------


def wake_without_speech(session):
    """A wake followed by no speech: back to armed after WAKE_NO_SPEECH_S,
    nothing uploaded."""
    session.frame(WAKE)
    session.silence(config.WAKE_NO_SPEECH_S * 1000 + 100)


def test_the_fourth_wake_in_a_minute_without_a_turn_storm_mutes():
    session, fake = world(on_marker)
    for _ in range(config.WAKE_STORM_MAX):
        wake_without_speech(session)
        assert session.states()[-1] == "armed_wake"
    session.frame(WAKE)  # the 4th activation
    assert session.states()[-1] == "storm_muted"
    assert session.sent("release_mic")[-1]["reason"] == "storm"
    assert session.sent("state")[-1]["storm_mutes_today"] == 1
    assert session.core.counters["wake_accepts_today"] == config.WAKE_STORM_MAX
    assert not session.host.mic_held
    # Nothing is consumed while storm-muted; the host's PTT does nothing.
    session.ptt("down")
    session.frames([marked(i) for i in range(20)])
    session.ptt("up")
    session.settle()
    assert fake.calls("stt") == [] and fake.calls("session") == []


def test_storm_muted_re_arms_only_after_an_unmute_cycle():
    session, _ = world(on_marker)
    for _ in range(config.WAKE_STORM_MAX):
        wake_without_speech(session)
    session.frame(WAKE)
    assert session.states()[-1] == "storm_muted"
    session.advance(config.WAKE_STORM_WINDOW_S * 1000 * 3)
    assert session.states()[-1] == "storm_muted"  # time alone never re-arms
    session.host.set_mute(False)  # an "unmute" without a mute first
    session.pump()
    assert session.states()[-1] == "storm_muted"
    session.host.set_mute(True)
    session.pump()
    session.host.set_mute(False)
    session.pump()
    session.advance(20)
    assert session.states()[-1] == "armed_wake"


def test_a_dispatched_turn_resets_the_storm_count():
    session, fake = world(on_marker)
    for _ in range(config.WAKE_STORM_MAX - 1):
        wake_without_speech(session)
    session.frame(WAKE)
    speak(session)  # a real turn: dispatched to chat
    assert len(fake.calls("chat")) == 1
    session.advance(config.WAKE_REFRACTORY_MS)
    for _ in range(config.WAKE_STORM_MAX - 1):
        wake_without_speech(session)
    assert session.states()[-1] != "storm_muted"


def test_wakes_spread_over_more_than_a_minute_are_no_storm():
    session, _ = world(on_marker)
    for _ in range(config.WAKE_STORM_MAX * 3):
        wake_without_speech(session)
        session.advance(config.WAKE_STORM_WINDOW_S * 1000 // config.WAKE_STORM_MAX)
    assert "storm_muted" not in session.states()


def test_a_server_storm_mute_423_is_storm_muted_on_the_child_too():
    session, fake = world({0: 900})
    fake.fail_next("stt", FridayError(423, "voice.live_muted"))
    wake_turn(session)
    assert session.states()[-1] == "storm_muted"
    assert session.sent("release_mic")[-1]["reason"] == "storm"


# --- the session close (D12) ------------------------------------------------------


def ptt_turn(session, start=0):
    session.ptt("down")
    session.frames([marked(start + i) for i in range(20)])
    session.ptt("up")
    session.settle()


def test_the_session_closes_after_30_s_of_silence_with_the_end_earcon():
    session, fake = world(mode="push_to_talk")
    ptt_turn(session)
    assert session.sent("state")[-1]["session_open"] is True
    session.advance(config.SILENCE_CLOSE_S * 1000 - 100)
    assert session.sent("state")[-1]["session_open"] is True
    assert {"type": "earcon", "earcon": "end"} not in session.sent("earcon")
    session.advance(200)
    assert session.sent("earcon")[-1] == {"type": "earcon", "earcon": "end"}
    assert session.sent("state")[-1]["session_open"] is False
    assert session.pipeline.conversation_id == fake.conversation_id  # kept for reuse
    assert session.host.failures == []  # the keepalive still reached the watchdog
    ptt_turn(session)
    assert len(fake.calls("session")) == 2


def test_the_session_closes_at_the_cap_after_its_turn(monkeypatch):
    monkeypatch.setattr(config, "SESSION_MAX_MINUTES", 1)
    session, fake = world(mode="push_to_talk")
    for turn in range(5):  # a turn every ~20 s: never 30 s of silence
        ptt_turn(session, start=turn * 20)
        session.advance(20_000 - 2_000)
    assert len(fake.calls("session")) == 2  # the cap opened a new session
    assert {"type": "earcon", "earcon": "end"} in session.sent("earcon")


def test_link_down_closes_the_session_without_an_earcon():
    session, fake = world(mode="push_to_talk")
    ptt_turn(session)
    session.host.set_link(False)
    session.pump()
    session.advance(20)
    assert session.pipeline.session_open is False
    assert {"type": "earcon", "earcon": "end"} not in session.sent("earcon")
    assert session.sent("release_mic")[-1]["reason"] == "link_down"


# --- fail closed (D6, D12) --------------------------------------------------------------


def _eof(session):
    session._to_host(session.link.close())


def _credential_refused(session):
    session.client.fail_next("stt", FridayError(401, "not_authenticated"))
    wake_turn(session)


FAIL_CLOSED = {
    "link_down": (_link_down, "link_down"),
    "mute_unreadable": (_mute_unreadable, "mute_unreadable"),
    "stdin_eof": (_eof, "stdin_closed"),
    "credential_refused": (_credential_refused, "credential_refused"),
}


@pytest.mark.parametrize("name", sorted(FAIL_CLOSED))
def test_every_fail_closed_input_releases_the_mic_and_stops_stt(name):
    action, reason = FAIL_CLOSED[name]
    session, fake = world(always())
    action(session)
    session.pump()
    assert session.sent("release_mic")[-1]["reason"] == reason
    stt_before = len(fake.calls("stt"))
    # Whatever arrives next -- a wake, a push-to-talk press -- uploads nothing.
    session.host.feed_audio(SILENCE)
    session.ptt("down")
    for index in range(20):
        session.host.feed_audio(marked(index))
        session.pump()
        if not session.core.finished:
            session.advance(20)
    session.ptt("up")
    if not session.core.finished:
        session.settle()
    assert len(fake.calls("stt")) == stt_before


def test_the_host_watchdog_releases_the_mic_and_no_stt_follows():
    session, fake = world(always())
    assert session.host.mic_held
    # The child goes silent (no tick reaches the host) past the watchdog.
    session.clock.advance(11_000)
    session.host.tick()
    assert ("watchdog", session.clock.monotonic_ms()) in session.host.failures
    assert not session.host.mic_held
    assert session.host.feed_audio(marked(0)) is False
    assert fake.calls("stt") == []


# --- units: the arming reasons and `friday-voice run`'s detector stack ----------------


def test_the_arming_reasons_name_each_failing_condition():
    policy = ArmingPolicy(models_verified=False)
    reasons = policy.reasons(
        mode="push_to_talk", link_up=False, muted="unreadable", storm_muted=True
    )
    assert reasons == ["mode", "policy", "link_down", "muted", "storm_muted", "models"]
    policy = ArmingPolicy(models_verified=True)
    assert policy.record(PERMIT, 0) is True
    assert policy.reasons(mode="always_on", link_up=True, muted=False, storm_muted=False) == []
    policy.refuse("read_failed", 1)
    assert policy.permitted() is False and policy.server_reasons == ("read_failed",)


def test_run_without_verified_models_stays_push_to_talk(tmp_path):
    from friday_voice.arming import RefuseAlwaysOn
    from friday_voice.cli import load_wake_stack, make_run_pipeline
    from friday_voice.ports import ManualClock

    assert load_wake_stack(tmp_path) is None  # an empty model directory
    pipeline = make_run_pipeline(ManualClock(), wake_stack_loader=lambda: None)(lambda *_: None)
    assert isinstance(pipeline.arming, RefuseAlwaysOn) and pipeline.wake_gate is None


def test_run_with_the_verified_stack_uses_the_real_arming_policy():
    from friday_voice.cli import make_run_pipeline
    from friday_voice.ports import ManualClock

    stack = (ScriptedWakeDetector({}, model_sha256=SHA), EnergyVad(), ScriptedTurnEnd())
    pipeline = make_run_pipeline(ManualClock(), wake_stack_loader=lambda: stack)(lambda *_: None)
    assert isinstance(pipeline.arming, ArmingPolicy) and pipeline.arming.polls
    assert pipeline.arming.models_verified and pipeline.wake_capable()


def test_a_loader_failure_leaves_no_wake_mode(monkeypatch, tmp_path):
    from friday_voice import cli, models

    monkeypatch.setattr(models, "wake_armable", lambda *_a, **_k: True)

    def boom(*_a, **_k):
        raise RuntimeError("onnx")

    monkeypatch.setattr("friday_voice.wake.OpenWakeWordDetector", boom)
    monkeypatch.setattr("friday_voice.turn.SileroVad", boom)
    assert cli.load_wake_stack(tmp_path) is None
