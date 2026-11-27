"""The voice pipeline with fakes (Phase 11 brief D8, branch 3 Tests): the
reference host and the real child on a manual clock (`ReplaySession`),
`FakeFriday` for the five endpoints. No network, no model, no real clock."""

import numpy as np
import pytest

from friday_voice import config
from friday_voice.client import FridayError
from friday_voice.fakes import FakeFriday
from friday_voice.pipeline import AllowAlwaysOn, request_policy
from friday_voice.replay import DEV_CREDENTIAL, ReplaySession
from friday_voice.turn import EnergyVad, ScriptedTurnEnd
from friday_voice.wake import ScriptedWakeDetector

FRAME = config.ms_to_bytes(20)
SILENCE = b"\x00" * FRAME


def marked(index):
    """A 20 ms frame whose every sample is 1000 + index: loud enough to be
    speech for EnergyVad, and identifiable in an uploaded WAV."""
    return np.full(FRAME // 2, 1000 + index, dtype="<i2").tobytes()


def frame_indexes(pcm):
    """The marker of every 20 ms frame in an uploaded utterance."""
    samples = np.frombuffer(pcm, dtype="<i2")
    return [int(samples[i]) - 1000 for i in range(0, len(samples), FRAME // 2)]


REPLY = [
    {"type": "chunk", "text": "Bugün iki toplantın var, ikisi de öğleden sonra. "},
    {"type": "chunk", "text": "The first one is the quarterly planning review. "},
    {"type": "chunk", "text": "Akşam serbestsin."},
    {"type": "done", "message_id": "m-1"},
]


def ptt_session(**fake_kwargs):
    fake = FakeFriday(**({"replies": [REPLY]} | fake_kwargs))
    return ReplaySession(client=fake).start(), fake


def ptt_turn(session, frames=20, *, settle=True):
    session.ptt("down")
    for index in range(frames):
        session.frame(marked(index))
    session.ptt("up")
    if settle:
        session.settle()


def credential(token_body):
    return {**DEV_CREDENTIAL, "token": "jpat_" + token_body * 2}


# --- push-to-talk, end to end ---


def test_a_push_to_talk_turn_end_to_end():
    session, fake = ptt_session()
    ptt_turn(session)

    assert [call["endpoint"] for call in fake.requests] == ["session", "stt", "chat"] + ["tts"] * 3
    (stt,) = fake.calls("stt")
    assert frame_indexes(stt["pcm"]) == list(range(20))  # every frame while the key was down
    assert stt["trigger"] == "ptt" and stt["token"] == DEV_CREDENTIAL["token"]
    (chat,) = fake.calls("chat")
    assert chat["text"] == "bugün ne var"
    trace = stt["trace_id"]
    assert trace and chat["trace_id"] == trace
    tts = fake.calls("tts")
    assert all(call["trace_id"] == trace for call in tts)
    assert [(call["text"], call["language"]) for call in tts] == [
        ("Bugün iki toplantın var, ikisi de öğleden sonra.", "tr"),
        ("The first one is the quarterly planning review.", "en"),
        ("Akşam serbestsin.", "tr"),
    ]
    plays = session.sent("play")
    assert [p["utterance_id"] for p in plays if p["seq"] == 0] == ["t1s0", "t1s1", "t1s2"]
    for utterance_id in ("t1s0", "t1s1", "t1s2"):
        seqs = [p["seq"] for p in plays if p["utterance_id"] == utterance_id]
        assert seqs == list(range(len(seqs)))
        assert [p["last"] for p in plays if p["utterance_id"] == utterance_id][-1] is True
    assert session.host.dropped_play == 0
    states = session.states()
    # A `state` also goes out when the session opens (same state, session_open).
    distinct = [s for i, s in enumerate(states) if i == 0 or states[i - 1] != s]
    assert distinct[:4] == ["armed_ptt", "listening", "thinking", "speaking"]
    assert states[-1] == "armed_ptt"
    assert session.sent("release_mic") == [] and session.host.mic_held
    assert session.sent("state")[-1]["session_open"] is True


def test_the_played_audio_is_16khz_pcm16_of_the_synthesized_sentences():
    session, fake = ptt_session()
    ptt_turn(session)
    played = b"".join(
        pcm for utterance_id, _seq, pcm in session.host.played if utterance_id == "t1s0"
    )
    sentence = fake.calls("tts")[0]["text"]
    seconds = max(0.2, len(sentence) * 40 / 1000)  # fakes.tone_wav at 22.05 kHz
    assert abs(len(played) / 2 / 16000 - seconds) < 0.01


def test_the_second_turn_reuses_the_session_conversation():
    session, fake = ptt_session(replies=[REPLY, REPLY])
    ptt_turn(session)
    ptt_turn(session)
    assert len(fake.calls("session")) == 1
    assert {call["conversation_id"] for call in fake.calls("stt")} == {fake.conversation_id}
    trace_ids = [call["trace_id"] for call in fake.calls("stt")]
    assert len(set(trace_ids)) == 2


def test_a_session_idle_past_the_silence_close_reopens():
    session, fake = ptt_session(replies=[REPLY, REPLY])
    ptt_turn(session)
    session.advance(config.SILENCE_CLOSE_S * 1000)
    assert session.sent("state")[-1]["session_open"] is False
    ptt_turn(session)
    assert len(fake.calls("session")) == 2


def test_timings_ride_on_every_speech_request():
    session, fake = ptt_session()
    ptt_turn(session)
    first, *_rest, last = fake.calls("tts")
    assert first["timings"]["end_of_turn_ms"] == 0
    assert first["timings"]["llm_first_sentence_ms"] == fake.latencies["chat_first"]
    assert "playback_start_ms" not in first["timings"]  # nothing played yet
    assert last["timings"]["playback_start_ms"] > 0
    assert last["timings"]["total_ms"] >= last["timings"]["playback_start_ms"]
    assert set(last["timings"]) <= {
        "end_of_turn_ms",
        "llm_first_sentence_ms",
        "playback_start_ms",
        "total_ms",
        "wake_to_listen_ms",
    }


def test_a_draft_event_yields_kart_ekranda_and_nothing_else():
    draft = {"type": "draft", "kind": "tool", "tool_name": "node_action", "payload": {}}
    session, fake = ptt_session(replies=[[draft, {"type": "done"}]])
    ptt_turn(session)
    assert [call["text"] for call in fake.calls("tts")] == [config.DRAFT_SENTENCE]
    assert {p["utterance_id"] for p in session.sent("play")} == {"t1s0"}


def test_a_draft_is_announced_after_the_reply():
    draft = {"type": "draft", "kind": "task", "title": "x"}
    reply = [{"type": "chunk", "text": "Görevi taslak olarak hazırladım, ekrandan onayla."}, draft]
    session, fake = ptt_session(replies=[[*reply, {"type": "done"}]])
    ptt_turn(session)
    spoken = [call["text"] for call in fake.calls("tts")]
    assert spoken == ["Görevi taslak olarak hazırladım, ekrandan onayla.", "Kart ekranda."]


def test_an_empty_transcript_yields_nothing():
    session, fake = ptt_session(transcripts=["   "])
    ptt_turn(session)
    assert fake.calls("chat") == [] and fake.calls("tts") == []
    assert session.sent("play") == []
    assert session.states()[-1] == "armed_ptt"


def test_a_slip_of_the_key_uploads_nothing():
    session, fake = ptt_session()
    ptt_turn(session, frames=config.PTT_MIN_MS // 20 - 1)
    assert fake.requests == []
    assert session.states()[-1] == "armed_ptt"


def test_the_utterance_is_capped_at_30_seconds():
    session, fake = ptt_session()
    session.ptt("down")
    for index in range(config.UTTERANCE_MAX_S * 50 + 25):
        session.frame(marked(index % 500))
    assert session.states()[-1] in ("thinking", "speaking", "armed_ptt")
    session.ptt("up")
    session.settle()
    (stt,) = fake.calls("stt")
    assert len(stt["pcm"]) == config.ms_to_bytes(config.UTTERANCE_MAX_S * 1000)
    assert len(session.pipeline.completed) == 1


def test_ptt_down_during_a_reply_cuts_it_and_listens_again():
    slow = [(400, {"type": "chunk", "text": "Uzun bir cevap geliyor ama yavaş geliyor. "})] + [
        (3_000, {"type": "chunk", "text": "Devamı çok sonra gelir. "}),
        (10, {"type": "done"}),
    ]
    session, fake = ptt_session(replies=[slow, REPLY])
    ptt_turn(session, settle=False)
    session.advance(1_500)  # the first sentence is speaking, the stream still open
    session.ptt("down")
    assert fake.aborts == 1
    assert session.states()[-1] == "listening"
    for index in range(20):
        session.frame(marked(index))
    session.ptt("up")
    session.settle()
    assert len(fake.calls("chat")) == 2
    assert "Devamı çok sonra gelir." not in [call["text"] for call in fake.calls("tts")]


# --- errors (D8) ---


def test_a_401_after_a_newer_credential_is_retried_once_and_the_mic_kept():
    session, fake = ptt_session(latencies={"stt": 300})
    fake.fail_next("stt", FridayError(401, "not_authenticated"))  # the old token, mid-rotation
    ptt_turn(session, settle=False)
    session.advance(100)  # the transcription with the old token is in flight
    rotated = credential("R0tatedT0kenAbc")
    session.credential(rotated)
    session.settle()
    stt = fake.calls("stt")
    assert [call["token"] for call in stt] == [DEV_CREDENTIAL["token"], rotated["token"]]
    assert fake.calls("chat")[0]["token"] == rotated["token"]
    assert session.sent("release_mic") == []
    assert session.host.mic_held
    assert len(session.pipeline.completed) == 1


def test_any_other_401_asks_for_a_credential_then_releases():
    session, fake = ptt_session()
    fake.revoked.add(DEV_CREDENTIAL["token"])
    ptt_turn(session)
    assert session.sent("release_mic") == [{"type": "release_mic", "reason": "credential_refused"}]
    assert session.states()[-1] == "error"
    assert not session.host.mic_held
    assert fake.calls("chat") == []
    # Nothing is consumed until a new credential arrives.
    session.ptt("down")
    assert session.pipeline.turn is None
    session.credential(credential("FreshT0kenAbcdef"))
    assert session.states()[-1] == "armed_ptt"
    ptt_turn(session)
    assert len(fake.calls("chat")) == 1


def test_a_refused_new_credential_releases_again():
    session, fake = ptt_session()
    fresh = credential("FreshT0kenAbcdef")
    fake.revoked |= {DEV_CREDENTIAL["token"], fresh["token"]}
    ptt_turn(session)
    session.credential(fresh)
    ptt_turn(session)
    reasons = [frame["reason"] for frame in session.sent("release_mic")]
    assert reasons == ["credential_refused", "credential_refused"]
    assert session.states()[-1] == "error"


def test_423_live_muted_is_storm_muted_until_an_unmute_cycle():
    session, fake = ptt_session()
    fake.fail_next("stt", FridayError(423, "voice.live_muted"))
    ptt_turn(session)
    assert session.states()[-1] == "storm_muted"
    assert session.sent("release_mic")[-1]["reason"] == "storm"
    assert session.sent("state")[-1]["storm_mutes_today"] == 1
    assert fake.calls("chat") == []
    session.ptt("down")
    assert session.pipeline.turn is None  # nothing consumed while storm-muted
    session.host.set_mute(False)  # not a cycle
    session.pump()
    assert session.states()[-1] == "storm_muted"
    session.host.set_mute(True)
    session.pump()
    session.host.set_mute(False)
    session.pump()
    assert session.states()[-1] == "armed_ptt"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_refused_or_failed_turn_plays_the_error_earcon_and_speaks_nothing(status):
    session, fake = ptt_session()
    fake.fail_next("stt", FridayError(status, "voice.budget_exceeded"))
    ptt_turn(session)
    assert session.sent("earcon") == [{"type": "earcon", "earcon": "error"}]
    assert fake.calls("chat") == [] and fake.calls("tts") == []
    assert session.sent("play") == []
    assert session.states()[-1] == "error"
    assert session.sent("release_mic") == []
    session.advance(config.ERROR_HOLD_MS)
    assert session.states()[-1] == "armed_ptt"


@pytest.mark.parametrize("code", ["tls_pin_mismatch", "tls_ca_file_unreadable"])
def test_a_tls_refusal_releases_the_mic_with_the_error_earcon(code):
    session, fake = ptt_session()
    fake.fail_next("stt", FridayError(0, code))
    ptt_turn(session)
    assert session.sent("earcon") == [{"type": "earcon", "earcon": "error"}]
    assert session.sent("release_mic")[-1] == {"type": "release_mic", "reason": "error"}
    assert len(fake.calls("stt")) == 1  # never retried: not a network error
    assert fake.calls("chat") == []
    assert session.states()[-1] == "error"


def test_a_429_on_speech_stops_the_reply():
    session, fake = ptt_session()
    fake.fail_next("tts", FridayError(429, "throttled"))
    ptt_turn(session)
    assert session.sent("play") == []
    assert session.sent("earcon")[-1]["earcon"] == "error"
    assert len(fake.calls("tts")) == 1


def test_a_chat_network_error_is_never_retried():
    session, fake = ptt_session()
    fake.fail_next("chat", 0)
    ptt_turn(session)
    assert len(fake.calls("chat")) == 1
    assert fake.calls("tts") == []
    assert session.sent("earcon")[-1]["earcon"] == "error"


def test_a_chat_stream_error_event_ends_the_turn_with_the_earcon():
    reply = [{"type": "chunk", "text": "Yarım"}, {"type": "error", "error_type": "provider"}]
    session, fake = ptt_session(replies=[reply])
    ptt_turn(session)
    assert fake.calls("tts") == []
    assert session.sent("earcon")[-1]["earcon"] == "error"
    assert session.pipeline.turn is None


@pytest.mark.parametrize("endpoint", ["stt", "tts"])
def test_speech_network_errors_are_retried_once(endpoint):
    session, fake = ptt_session()
    fake.fail_next(endpoint, 0)
    ptt_turn(session)
    assert len(fake.calls("chat")) == 1
    assert session.sent("earcon") == []
    assert len(session.pipeline.completed) == 1


def test_two_speech_network_errors_fail_the_turn():
    session, fake = ptt_session()
    fake.fail_next("stt", 0)
    fake.fail_next("stt", 0)
    ptt_turn(session)
    assert len(fake.calls("stt")) == 2 and fake.calls("chat") == []
    assert session.sent("earcon")[-1]["earcon"] == "error"


# --- fail closed mid-turn ---


class HoldingHost:
    """Make the reference host keep utterances open (playback never ends
    by itself), so playback state can be scripted."""

    @staticmethod
    def install(session):
        host = session.host
        original = host._on_play

        def on_play(header, payload):
            original({**header, "last": False}, payload)

        host._on_play = on_play
        return session

    @staticmethod
    def end(session, utterance_id):
        session.host._utterances.pop(utterance_id, None)
        session.host._send(
            {"type": "playback", "utterance_id": utterance_id, "state": "ended", "at_ms": 0}
        )
        session.pump()


def test_one_sentence_is_prefetched_and_no_more():
    session, fake = ptt_session()
    HoldingHost.install(session)
    ptt_turn(session)
    assert [call["text"] for call in fake.calls("tts")][:2] == [
        "Bugün iki toplantın var, ikisi de öğleden sonra.",
        "The first one is the quarterly planning review.",
    ]
    assert len(fake.calls("tts")) == 2  # the third waits for the first to end
    HoldingHost.end(session, "t1s0")
    session.settle()
    assert len(fake.calls("tts")) == 3
    assert session.states()[-1] == "speaking"
    HoldingHost.end(session, "t1s1")
    HoldingHost.end(session, "t1s2")
    session.settle()
    assert session.states()[-1] == "armed_ptt"


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (lambda host: host.set_link(False), "link_down"),
        (lambda host: host.set_mute(True), "muted"),
        (lambda host: host.set_mute("unreadable"), "mute_unreadable"),
        (lambda host: host.set_mode("off"), "mode_off"),
    ],
)
def test_a_fail_closed_input_stops_playback_cancels_the_turn_and_releases(event, reason):
    slow_tail = [*REPLY[:1], (5_000, {"type": "chunk", "text": "Geç gelen kısım."})]
    session, fake = ptt_session(replies=[[*slow_tail, {"type": "done"}]])
    HoldingHost.install(session)
    ptt_turn(session, settle=False)
    session.advance(1_200)
    assert session.states()[-1] == "speaking"
    before = len(session.frames_to_host)
    event(session.host)
    session.pump()
    after = session.frames_to_host[before:]
    assert after[0] == {"type": "stop_playback", "utterance_id": "t1s0"}
    assert after[1] == {"type": "release_mic", "reason": reason}
    assert fake.aborts == 1
    session.settle()
    assert len(fake.calls("tts")) == 1
    assert session.pipeline.turn is None


def test_shutdown_mid_turn_cancels_every_job():
    session, fake = ptt_session(latencies={"stt": 2_000})
    ptt_turn(session, settle=False)
    session.advance(500)
    session.host.shutdown()
    session.pump()
    assert session.core.finished
    session.settle()
    assert fake.calls("chat") == []
    assert session.worker.cancelled


# --- wake gate (scripted detector, scripted arming; the real policy: test_always_on.py) ---


def wake_session(scores, **fake_kwargs):
    fake = FakeFriday(**({"replies": [REPLY]} | fake_kwargs))
    session = ReplaySession(
        client=fake,
        mode="always_on",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd([True]),
        wake_detector=ScriptedWakeDetector(scores),
        arming=AllowAlwaysOn(),
    ).start()
    return session, fake


def test_always_on_without_the_arming_policy_stays_push_to_talk():
    fake = FakeFriday()
    detector = ScriptedWakeDetector({0: 1000, 1: 1000, 2: 1000})
    session = ReplaySession(
        client=fake,
        mode="always_on",
        vad=EnergyVad(),
        turn_end=ScriptedTurnEnd(),
        wake_detector=detector,
    ).start()
    for index in range(30):
        session.frame(marked(index))
    session.settle()
    assert session.states() == ["armed_ptt"]
    assert detector.calls == 0 and fake.requests == []


def test_wake_has_no_preroll_and_pre_wake_audio_never_leaves():
    session, fake = wake_session({5: 900})
    assert session.states() == ["armed_wake"]
    for index in range(40):
        session.frame(marked(index))
    session.silence(400)
    session.settle()
    (stt,) = fake.calls("stt")
    uploaded = [i for i in frame_indexes(stt["pcm"]) if i >= 0]
    assert uploaded[0] == 6  # the first frame after the detection frame
    assert not set(range(6)) & set(uploaded)  # nothing from before the wake
    assert stt["trigger"] == "wake"
    assert session.sent("earcon")[0] == {"type": "earcon", "earcon": "wake"}
    assert session.sent("state")[-1]["wake_accepts_today"] == 1
    assert session.states()[-1] == "armed_wake"
    assert fake.calls("tts")[0]["timings"]["wake_to_listen_ms"] == 20


def test_a_wake_without_speech_uploads_nothing():
    session, fake = wake_session({2: 900})
    session.silence(config.WAKE_NO_SPEECH_S * 1000 + 200)
    session.settle()
    assert fake.requests == []
    assert session.states()[-1] == "armed_wake"
    assert "listening" in session.states()


def test_the_refractory_period_ignores_a_second_detection():
    session, fake = wake_session({0: 900, 1: 900, 2: 900})
    gate = session.pipeline.wake_gate
    session.silence(60)
    assert gate.detections == 1


def test_a_server_refusal_of_wake_disarms_to_push_to_talk():
    session, fake = wake_session({3: 900})
    fake.fail_next("session", FridayError(403, "voice.always_on_not_permitted"))
    for index in range(20):
        session.frame(marked(index))
    session.silence(400)
    session.settle()
    assert session.states()[-1] == "armed_ptt"
    assert session.sent("release_mic") == []
    assert fake.calls("stt") == []


def test_the_policy_request_is_built_from_the_package_constants(monkeypatch):
    fake = FakeFriday()
    fake.set_credential(DEV_CREDENTIAL["token"], DEV_CREDENTIAL["server_url"], None)
    request_policy(fake, "a" * 64)
    monkeypatch.setattr(config, "WAKE_THRESHOLD", 650)
    monkeypatch.setattr(config, "WAKE_REFRACTORY_MS", 2_500)
    request_policy(fake, "a" * 64)
    first, second = fake.calls("policy")
    assert (first["threshold"], first["refractory_ms"]) == (500, 2_000)
    assert (second["threshold"], second["refractory_ms"]) == (650, 2_500)


def test_the_wake_gate_reads_the_package_constants(monkeypatch):
    from friday_voice.ports import ManualClock
    from friday_voice.wake import WakeGate

    monkeypatch.setattr(config, "WAKE_THRESHOLD", 777)
    gate = WakeGate(ScriptedWakeDetector({0: 776, 1: 777}), ManualClock())
    assert gate.threshold == 777
    assert gate.feed(SILENCE) is False
    assert gate.feed(SILENCE) is True


# --- the shell's guarantees hold with the pipeline attached ---


def test_nothing_is_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session, _fake = ptt_session()
    ptt_turn(session)
    assert list(tmp_path.iterdir()) == []


def test_the_log_never_holds_transcript_reply_or_token():
    session, fake = ptt_session(transcripts=["gizli kanarya cümlesi"])
    fake.revoked.add("nothing")
    ptt_turn(session)
    fake.fail_next("tts", FridayError(500, "x"))
    ptt_turn(session)
    text = session.log.getvalue()
    for secret in ("kanarya", "toplantın", DEV_CREDENTIAL["token"], "planning"):
        assert secret not in text
