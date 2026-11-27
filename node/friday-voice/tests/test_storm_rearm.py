"""Storm mute, the child's guarantee (Phase 11 brief D12; ADR-015 decision 5;
Mac session M1 review): after `release_mic(storm)` the child sends NO armed
or listening `state` -- whatever the host does, however long it waits --
until the host sends a mute on -> off cycle. The Swift host does not latch
`storm_muted` itself (the host case `host.child_release_mic` makes it
re-acquire on the child's next armed state), so this is the rule the Mac
relies on. Reference host, real child, FakeFriday, manual clock."""

import pytest
from test_always_on import WAKE, on_marker, wake_without_speech, world
from test_pipeline import marked, ptt_session, ptt_turn

from friday_voice import config
from friday_voice.client import FridayError

ARMED = {"armed_ptt", "armed_wake", "listening", "thinking", "speaking"}


def wake_storm():
    session, _ = world(on_marker)
    for _ in range(config.WAKE_STORM_MAX):
        wake_without_speech(session)
    session.frame(WAKE)
    return session


def server_storm():
    session, fake = ptt_session()
    fake.fail_next("stt", FridayError(423, "voice.live_muted"))
    ptt_turn(session)
    return session


def everything_but_a_mute_cycle(session):
    """Every host input that is not mute on -> off, and a lot of time."""
    host = session.host
    session.advance(config.WAKE_STORM_WINDOW_S * 1000 * 3)
    session.ptt("down")
    session.frames([marked(i) for i in range(30)])
    session.ptt("up")
    session.frame(WAKE)
    host.set_link(False)
    session.pump()
    host.set_link(True)
    session.pump()
    for mode in ("push_to_talk", "always_on", "off", "push_to_talk"):
        host.set_mode(mode)
        session.pump()
    session.credential(dict(host.credential))
    host.set_mute(False)  # an "unmute" without a mute first
    session.pump()
    host.set_mute("unreadable")  # unreadable is not "on"
    session.pump()
    host.set_mute(False)
    session.pump()
    session.advance(config.WAKE_STORM_WINDOW_S * 1000)
    session.settle()


@pytest.mark.parametrize("make", [wake_storm, server_storm], ids=["wake_storm", "server_423"])
def test_no_armed_state_after_a_storm_release_until_a_mute_cycle(make):
    session = make()
    releases = session.sent_at("release_mic")
    assert releases and releases[-1][1]["reason"] == "storm"
    released_at = session.frames_to_host.index(releases[-1][1])
    everything_but_a_mute_cycle(session)
    after = [
        frame["state"]
        for frame in session.frames_to_host[released_at + 1 :]
        if frame["type"] == "state"
    ]
    assert after, "the child keeps sending its state (keepalive)"
    assert not set(after) & ARMED, after
    assert not session.host.mic_held
    # The cycle, and only the cycle, re-arms.
    session.host.set_mute(True)
    session.pump()
    session.host.set_mute(False)
    session.pump()
    session.advance(20)
    assert session.states()[-1] in {"armed_ptt", "armed_wake"}
