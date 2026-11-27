"""Branch 3a replay harness (brief A4/A5): synthetic tone/silence clips
through capture/segment and the gateway's fake STT/TTS (one ModelUsage per
call, `voice.eval_*` purposes, owner's personal workspace, no text in the
usage row); the latency regression check trips on a 25% slowdown."""

import pytest

from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelUsage
from voice.eval import fixtures, replay, runner
from voice.eval.manifest import Entry, sha256_hex

pytestmark = pytest.mark.django_db


@pytest.fixture
def scripted_stt(monkeypatch):
    """The fake STT provider answers per clip (by sha256)."""
    script = {}

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        text = script.get(sha256_hex(bytes(audio)), "")
        return TranscribeAdapterResult(
            text=text, audio_seconds=1.0, output_tokens=1, raw_usage={"fake": True}
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return script


def test_capture_and_segment_find_the_tone():
    frames = replay.capture_frames(fixtures.clip(lead=0.3, speech=1.0, tail=0.6))
    assert len(frames) == 95  # 1.9 s in 20 ms frames
    seg = replay.segment(frames)
    assert seg.has_speech
    assert seg.speech_start_ms == 300 and seg.speech_end_ms == 1300
    assert seg.end_of_turn_ms == 1300 + replay.FINISH_MS


def test_silence_has_no_speech():
    assert not replay.segment(replay.capture_frames(fixtures.clip(speech=0))).has_speech


def test_first_sentence():
    assert replay.first_sentence("Tamam. Görev eklendi!") == "Tamam."
    assert replay.first_sentence("  ") == ""


def _clips(script):
    rows = [
        ("q1.wav", "tr-quiet", "yarın saat onda diş hekimi", "yarın saat onda diş hekimi", 440),
        ("q2.wav", "tr-quiet", "bugünkü görevlerimi oku", "bugünkü görevleri oku", 480),
        ("n1.wav", "tr-noise", "İstanbul'a gidiyorum", "istanbula gidiyorum", 520),
        ("c1.wav", "cs-quiet", "PR'ı merge et", "PR'ı merge et.", 560),
        ("e1.wav", "en-quiet", "read me today's tasks", "read me todays task", 600),
    ]
    clips = []
    for name, condition, reference, hypothesis, freq in rows:
        wav = fixtures.clip(speech=0.4, frequency=freq)
        script[sha256_hex(wav)] = hypothesis
        clips.append((Entry(name, sha256_hex(wav), condition, reference), wav))
    return clips


def test_replay_through_the_gateway(owner, workspace, speech_providers, scripted_stt):
    clips = _clips(scripted_stt)
    report = runner.run_wer(clips=clips, owner=owner, workspace=workspace, run_id="r1")
    groups = report["groups"]
    assert groups["tr-quiet"]["wer"] == round(1 / 8, 4)  # görevlerimi -> görevleri
    assert groups["tr-quiet"]["verdict"] == runner.FAIL  # 12.5% > 12%
    assert groups["tr-noise"]["wer"] == 0.0 and groups["tr-noise"]["verdict"] == runner.PASS
    assert groups["code-switched"]["wer"] == 0.0
    assert groups["en"]["target"] is None and "verdict" not in groups["en"]
    assert report["verdict"] == runner.FAIL
    assert report["stt_providers"] == ["fake-stt-local"]
    assert report["tts_providers"] == ["fake-tts-local"]
    assert report["latency"]["stt_ms"]["n"] == 5 and report["latency"]["turn_ms"]["n"] == 5

    stt_rows = ModelUsage.objects.filter(purpose=replay.PURPOSE_EVAL_STT)
    tts_rows = ModelUsage.objects.filter(purpose=replay.PURPOSE_EVAL_TTS)
    assert stt_rows.count() == 5 and tts_rows.count() == 5
    for row in [*stt_rows, *tts_rows]:
        assert row.workspace_id == workspace.pk and row.success
        report_keys = set(row.context_report)
        assert row.context_report["voice_eval_run"] == "r1"
        assert row.context_report["personal_data"] is True
        # numbers and ids only: no reference, no transcript in the row
        dumped = str(row.context_report)
        assert "gidiyorum" not in dumped and "merge" not in dumped
        assert {"voice_eval_run", "condition"} <= report_keys


def test_stt_only_makes_no_tts_call(owner, workspace, speech_providers, scripted_stt):
    clips = _clips(scripted_stt)[:2]
    report = runner.run_wer(clips=clips, owner=owner, workspace=workspace, run_id="r2", tts=False)
    assert not ModelUsage.objects.filter(purpose=replay.PURPOSE_EVAL_TTS).exists()
    assert report["latency"]["tts_first_ms"]["n"] == 0


def test_no_provider_aborts_the_run(owner, workspace, make_provider, scripted_stt):
    from ai_core.models import ModelProvider

    make_provider("cloud-stt", ModelProvider.Capability.STT, allowed=False)
    with pytest.raises(replay.ReplayAborted):
        runner.run_wer(
            clips=_clips(scripted_stt)[:1], owner=owner, workspace=workspace, run_id="r3"
        )


def test_invalid_clip_scores_as_all_deletions(owner, workspace, speech_providers, scripted_stt):
    bad = fixtures.clip(rate=22_050)
    entry = Entry("bad.wav", sha256_hex(bad), "tr-quiet", "üç kelime burada")
    report = runner.run_wer(clips=[(entry, bad)], owner=owner, workspace=workspace, run_id="r4")
    row = report["results"][0]
    assert row["error"] == "invalid_audio:wrong_sample_rate" and row["deletions"] == 3
    assert report["groups"]["tr-quiet"]["verdict"] == runner.FAIL
    assert not ModelUsage.objects.filter(purpose=replay.PURPOSE_EVAL_STT).exists()


def test_self_trigger_fixture_replays_to_its_transcript(
    owner, workspace, speech_providers, scripted_stt
):
    wav = fixtures.self_trigger_clip()
    scripted_stt[sha256_hex(wav)] = fixtures.SELF_TRIGGER_TEXT
    entry = Entry("self.wav", sha256_hex(wav), "cs-quiet", fixtures.SELF_TRIGGER_TEXT)
    report = runner.run_wer(clips=[(entry, wav)], owner=owner, workspace=workspace, run_id="r5")
    assert report["results"][0]["hypothesis"] == fixtures.SELF_TRIGGER_TEXT
    assert report["results"][0]["has_speech"] is True


# --- latency baseline (A5 nightly rule) ---------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _timed_pipeline(stt_ms, tts_ms):
    clock = FakeClock()

    def stt(wav, *, condition):
        clock.now += stt_ms / 1000
        return "merhaba dünya", stt_ms

    def tts(text, *, language, condition=""):
        clock.now += tts_ms / 1000
        return b"", tts_ms

    return replay.Pipeline(stt=stt, tts=tts, clock=clock)


def _latency(stt_ms, tts_ms):
    wav = fixtures.clip(speech=0.2)
    clips = [(Entry(f"{i}.wav", "", "tr-quiet", "merhaba dünya"), wav) for i in range(5)]
    results = replay.replay(_timed_pipeline(stt_ms, tts_ms), clips)
    return replay.latency_stats(results, replay.LATENCY_STAGES)


def test_regression_check_trips_on_a_25_percent_slowdown():
    baseline = replay.make_baseline(_latency(400, 200), run_id="base", created_at="t")
    assert baseline["p50"] == {"stt_ms": 400, "tts_first_ms": 200, "turn_ms": 600}
    slow = _latency(500, 250)  # +25%
    regressions = replay.compare_to_baseline(slow, baseline)
    assert {stage for stage, _b, _c in regressions} == {"stt_ms", "tts_first_ms", "turn_ms"}
    assert replay.compare_to_baseline(_latency(460, 230), baseline) == []  # +15%: ok


def test_regression_fails_the_run_verdict(owner, workspace):
    baseline = replay.make_baseline(_latency(400, 200), run_id="base", created_at="t")
    wav = fixtures.clip(speech=0.2)
    clips = [(Entry("a.wav", "", "tr-quiet", "merhaba dünya"), wav)]
    report = runner.run_wer(
        clips=clips,
        owner=owner,
        workspace=workspace,
        run_id="r6",
        baseline=baseline,
        pipeline=_timed_pipeline(500, 250),
    )
    assert report["regressions"] and report["verdict"] == runner.FAIL


def test_percentile():
    assert replay.percentile([], 0.5) is None
    assert replay.percentile([100], 0.95) == 100
    assert replay.percentile([100, 200, 300, 400], 0.5) == 250
    assert replay.percentile(list(range(1, 101)), 0.95) == 95
