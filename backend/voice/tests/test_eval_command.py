"""`manage.py voice_eval` end to end on synthetic fixtures and fake
providers (brief "3a"): fill hashes, run (report file 0600 outside the
repo, baseline written and compared), TTS listening sheet and its score,
latency from `VoiceTurnTrace`. Never a real model."""

import csv
import json
import os
import stat
import uuid
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelUsage
from voice.eval import fixtures, replay
from voice.eval.manifest import MANIFEST_MARKER, Entry, parse_manifest, render_table, sha256_hex
from voice.models import VoiceTurnTrace

pytestmark = pytest.mark.django_db
_TR_UPPER = str.maketrans({"i": "İ", "ı": "I"})


@pytest.fixture
def scripted_stt(monkeypatch):
    script = {}

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text=script.get(sha256_hex(bytes(audio)), ""),
            audio_seconds=1.0,
            output_tokens=1,
            raw_usage={"fake": True},
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return script


@pytest.fixture
def eval_env(tmp_path, settings, owner, speech_providers, scripted_stt):
    set_dir = tmp_path / "FridayEval" / "asr"
    set_dir.mkdir(parents=True, mode=0o700)
    os.chmod(tmp_path / "FridayEval", 0o700)
    os.chmod(set_dir, 0o700)
    rows = []
    for name, condition, reference, freq in (
        ("tr-quiet-001.wav", "tr-quiet", "Yarın saat onda diş hekimi.", 440),
        ("tr-noise-001.wav", "tr-noise", "Bugünkü görevlerimi oku.", 480),
        ("cs-quiet-001.wav", "cs-quiet", "PR'ı merge et.", 520),
        ("en-quiet-001.wav", "en-quiet", "Read me today's tasks.", 560),
    ):
        wav = fixtures.clip(speech=0.4, frequency=freq)
        (set_dir / name).write_bytes(wav)
        # Turkish upper case (i -> İ, ı -> I): casing must not count.
        scripted_stt[sha256_hex(wav)] = reference.translate(_TR_UPPER).upper()
        rows.append(Entry(name, "", condition, reference))
    rows.append(Entry("tr-quiet-002.wav", "", "tr-quiet", "Henüz kaydedilmedi."))
    manifest = tmp_path / "eval-manifest.md"
    manifest.write_text(f"# m\n\n{MANIFEST_MARKER}\n{render_table(rows)}\n", encoding="utf-8")
    settings.VOICE_EVAL_SET_DIR = str(set_dir)
    settings.VOICE_EVAL_MANIFEST = str(manifest)
    settings.VOICE_EVAL_RESULTS_DIR = str(tmp_path / "FridayEval" / "results")
    settings.VOICE_EVAL_BASELINE = str(tmp_path / "FridayEval" / "latency-baseline.json")
    return {"set": set_dir, "manifest": manifest, "root": tmp_path / "FridayEval"}


def _call(*args):
    out = StringIO()
    call_command("voice_eval", *args, stdout=out)
    return out.getvalue()


def test_fill_hashes_then_run(eval_env):
    out = _call("--fill-hashes")
    assert "filled 4 hash(es)" in out
    entries = parse_manifest(eval_env["manifest"].read_text(encoding="utf-8"))
    assert sum(1 for e in entries if e.sha256) == 4
    assert _call("--fill-hashes").startswith("filled 0 hash(es)")

    out = _call("--write-baseline")
    assert "4 clip(s) replayed, 1 unrecorded" in out
    assert "verdict: pass" in out
    # stdout never carries a reference or a transcript
    assert "hekimi" not in out.lower() and "merge" not in out.lower()
    reports = list((eval_env["root"] / "results").glob("voice-eval-*.json"))
    assert len(reports) == 1
    assert stat.S_IMODE(os.stat(reports[0]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(eval_env["root"] / "results").st_mode) == 0o700
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["groups"]["tr-quiet"]["wer"] == 0.0
    assert report["groups"]["code-switched"]["verdict"] == "pass"
    baseline = json.loads((eval_env["root"] / "latency-baseline.json").read_text())
    assert baseline["version"] == 1 and baseline["run_id"] == report["run_id"]
    assert ModelUsage.objects.filter(purpose=replay.PURPOSE_EVAL_STT).count() == 4

    out = _call("--stt-only")
    assert f"baseline: run {report['run_id']}" in out


def test_run_without_recordings_is_refused(eval_env):
    with pytest.raises(CommandError, match="no recorded clip"):
        _call()


def test_changed_recording_fails_the_run(eval_env):
    _call("--fill-hashes")
    (eval_env["set"] / "tr-quiet-001.wav").write_bytes(fixtures.clip(speech=0.9))
    with pytest.raises(CommandError, match="sha256 differs"):
        _call()


def test_missing_recording_needs_allow_missing(eval_env):
    _call("--fill-hashes")
    (eval_env["set"] / "en-quiet-001.wav").unlink()
    with pytest.raises(CommandError, match="missing from the set"):
        _call()
    assert "3 clip(s) replayed" in _call("--allow-missing")


def test_a_failing_group_exits_non_zero_after_writing_the_report(eval_env, scripted_stt):
    _call("--fill-hashes")
    wav = (eval_env["set"] / "tr-quiet-001.wav").read_bytes()
    scripted_stt[sha256_hex(wav)] = "tamamen başka bir cümle"
    with pytest.raises(CommandError, match="verdict: fail"):
        _call()
    assert list((eval_env["root"] / "results").glob("voice-eval-*.json"))


def test_world_readable_set_is_refused(eval_env):
    os.chmod(eval_env["set"], 0o755)
    with pytest.raises(CommandError, match="chmod 700"):
        _call("--fill-hashes")


def test_tts_sheet_and_score(eval_env):
    out = _call("--tts-sheet", "--tts-limit", "2")
    assert "fake-tts-local" in out
    sheets = list((eval_env["root"] / "results").glob("tts-*/ratings.csv"))
    assert len(sheets) == 1
    directory = sheets[0].parent
    assert sorted(p.name for p in directory.glob("*.wav")) == ["001.wav", "002.wav"]
    assert ModelUsage.objects.filter(purpose=replay.PURPOSE_EVAL_TTS).count() == 2
    rows = list(csv.DictReader(sheets[0].open(encoding="utf-8")))
    assert [r["condition"] for r in rows] == ["tr-quiet", "tr-noise"]

    filled = eval_env["root"] / "ratings-filled.csv"
    with filled.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row, (i, n) in zip(rows, [("5", "4"), ("4", "4")], strict=True):
            writer.writerow({**row, "intelligibility": i, "naturalness": n})
    out = _call("--score-tts", str(filled))
    assert "intelligibility 4.5, naturalness 4.0" in out and "tts verdict: pass" in out

    with filled.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerow({**rows[0], "intelligibility": "4", "naturalness": "3"})
    with pytest.raises(CommandError, match="tts verdict: fail"):
        _call("--score-tts", str(filled))


def test_score_refuses_an_unrated_sheet(tmp_path):
    sheet = tmp_path / "r.csv"
    sheet.write_text("item,condition,text,intelligibility,naturalness\n001.wav,tr-quiet,x,,\n")
    with pytest.raises(CommandError, match="must be 1-5"):
        _call("--score-tts", str(sheet))


def test_traces_latency(owner, workspace, conversation):
    out = _call("--traces")
    assert "0 turn(s)" in out and "latency verdict: incomplete" in out
    for ms in (900, 1200, 1400, 1500, 2000):
        VoiceTurnTrace.objects.create(
            workspace=workspace,
            conversation=conversation,
            trace_id=uuid.uuid4(),
            playback_start_ms=ms,
            stt_ms=300,
        )
    out = _call("--traces")
    assert "5 turn(s)" in out and "latency verdict: pass" in out
    VoiceTurnTrace.objects.create(
        workspace=workspace,
        conversation=conversation,
        trace_id=uuid.uuid4(),
        playback_start_ms=9000,
    )
    with pytest.raises(CommandError, match="latency verdict: fail"):
        _call("--traces")


def test_traces_are_scoped_to_the_owner_workspace(owner, workspace):
    from accounts.services import register_user
    from assistant.models import Conversation
    from workspaces.models import Workspace

    other = register_user(email="other@test.dev", password="Str0ngDevPass!42", name="O")
    other_ws = Workspace.objects.get(owner=other)
    convo = Conversation.objects.create(workspace=other_ws, created_by=other)
    VoiceTurnTrace.objects.create(
        workspace=other_ws, conversation=convo, trace_id=uuid.uuid4(), playback_start_ms=100
    )
    assert "0 turn(s)" in _call("--traces")


def test_no_owner_is_refused(db):
    with pytest.raises(CommandError, match="no installation owner"):
        _call("--traces")
