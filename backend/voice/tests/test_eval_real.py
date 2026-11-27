"""Opt-in real-model run of the branch-3a harness (never in CI; CLAUDE.md
rule 5). Skipped unless `VOICE_EVAL_REAL_BASE_URL` names a local speech
server (speaches), e.g.

    VOICE_EVAL_REAL_BASE_URL=http://127.0.0.1:8000/v1 VOICE_EVAL_REAL_API_KEY=voice-local \\
      uv run pytest voice/tests/test_eval_real.py -s

It synthesizes a few Turkish sentences through the gateway
(`voice.eval_tts`), resamples them to 16 kHz mono in memory, writes them to
a private temporary set, and runs `voice_eval` end to end against the real
STT model in the TEST database. Synthetic speech, deleted with pytest's
temporary directory; nothing is committed."""

import io
import json
import os
import struct
import wave
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from ai_core.audio import pcm_to_wav
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage
from voice.eval import replay
from voice.eval.manifest import MANIFEST_MARKER, Entry, render_table

BASE_URL = os.environ.get("VOICE_EVAL_REAL_BASE_URL", "")
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not BASE_URL, reason="opt-in: set VOICE_EVAL_REAL_BASE_URL"),
]
SENTENCES = (
    ("tr-quiet-001.wav", "tr-quiet", "Yarın saat onda diş hekimine gitmem gerekiyor."),
    ("tr-quiet-002.wav", "tr-quiet", "Bugünkü görevlerimi oku."),
    ("tr-noise-001.wav", "tr-noise", "İstanbul'daki toplantıyı perşembeye ertele."),
    ("cs-quiet-001.wav", "cs-quiet", "Repo'yu aç ve son commit'i göster."),
)


def to_stt_wav(data):
    """Any PCM16 WAV -> 16 kHz mono (linear interpolation; test helper)."""
    with wave.open(io.BytesIO(data), "rb") as reader:
        rate, channels = reader.getframerate(), reader.getnchannels()
        raw = reader.readframes(reader.getnframes())
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    if channels > 1:
        samples = [
            sum(samples[i : i + channels]) // channels for i in range(0, len(samples), channels)
        ]
    count = int(len(samples) * 16_000 / rate)
    out = []
    for i in range(count):
        pos = i * rate / 16_000
        low = int(pos)
        high = min(low + 1, len(samples) - 1)
        out.append(int(samples[low] + (samples[high] - samples[low]) * (pos - low)))
    return pcm_to_wav(struct.pack(f"<{len(out)}h", *out), sample_rate=16_000)


@pytest.fixture
def real_rows(monkeypatch, settings):
    monkeypatch.setenv("VOICE_EVAL_REAL_API_KEY", os.environ.get("VOICE_EVAL_REAL_API_KEY", "x"))
    settings.AI_FAKE_PROVIDER = False
    for name, capability, model in (
        ("real-stt", ModelProvider.Capability.STT, "Systran/faster-whisper-small"),
        ("real-tts", ModelProvider.Capability.TTS, "speaches-ai/piper-tr_TR-dfki-medium"),
    ):
        ModelProvider.objects.create(
            name=name,
            provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
            capability=capability,
            model_id=model,
            base_url=BASE_URL,
            api_key_env_var="VOICE_EVAL_REAL_API_KEY",
            priority=0,
            personal_data_allowed=True,
        )


def test_real_run_end_to_end(tmp_path, settings, owner, workspace, real_rows):
    set_dir = tmp_path / "asr"
    set_dir.mkdir(mode=0o700)
    os.chmod(set_dir, 0o700)
    rows = []
    for name, condition, text in SENTENCES:
        speech = InferenceGateway().synthesize(
            replay.PURPOSE_EVAL_TTS, text, params={"language": "tr"}, workspace=workspace
        )
        (set_dir / name).write_bytes(to_stt_wav(speech.audio))
        rows.append(Entry(name, "", condition, text))
    manifest = tmp_path / "manifest.md"
    manifest.write_text(f"{MANIFEST_MARKER}\n{render_table(rows)}\n", encoding="utf-8")
    results = tmp_path / "results"
    common = ["--set", str(set_dir), "--manifest", str(manifest)]
    call_command("voice_eval", "--fill-hashes", *common, stdout=StringIO())
    out = StringIO()
    try:
        call_command(
            "voice_eval",
            *common,
            "--results-dir",
            str(results),
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--write-baseline",
            stdout=out,
        )
    except CommandError:  # a WER fail is a measurement, not a harness error
        pass
    print(out.getvalue())
    report = json.loads(next(results.glob("voice-eval-*.json")).read_text(encoding="utf-8"))
    print(
        json.dumps(
            [{k: r[k] for k in ("file", "wer", "hypothesis")} for r in report["results"]],
            ensure_ascii=False,
            indent=1,
        )
    )
    assert report["clips"] == len(SENTENCES)
    assert report["stt_providers"] == ["real-stt"]
    assert all(not r["error"] for r in report["results"])
    usage = ModelUsage.objects.filter(purpose__startswith="voice.eval_", success=True)
    assert usage.filter(purpose=replay.PURPOSE_EVAL_STT).count() == len(SENTENCES)
