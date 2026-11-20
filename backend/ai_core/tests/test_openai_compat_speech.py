"""`OpenAICompatibleAdapter.transcribe/synthesize/generation_cost` against
`httpx.MockTransport` (Phase 6 branch 1): the real `openai` client builds
real requests that never leave the process -- no speech server, no
OpenRouter."""

import io
import json
import wave
from decimal import Decimal

import httpx
import pytest

from ai_core.adapters.base import AdapterError
from ai_core.adapters.openai_compat import OpenAICompatibleAdapter
from ai_core.models import ModelProvider

KEY_ENV = "FRIDAY_TEST_SPEECH_KEY"


def make_wav(*, seconds=1.0, rate=16_000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * channels * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def speech_env(monkeypatch, settings):
    monkeypatch.setenv(KEY_ENV, "placeholder-key")
    settings.VOICE_MAX_UTTERANCE_SECONDS = 30
    settings.VOICE_TTS_MAX_SECONDS = 60


def adapter_for(handler, monkeypatch, *, capability, base_url="http://speech.test/v1"):
    captured = []

    def recording(request):
        captured.append(request)
        return handler(request)

    monkeypatch.setattr(OpenAICompatibleAdapter, "http_transport", httpx.MockTransport(recording))
    provider = ModelProvider(
        name="speech",
        provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        capability=capability,
        model_id="speech-model-1",
        base_url=base_url,
        api_key_env_var=KEY_ENV,
    )
    return OpenAICompatibleAdapter(provider), captured


# --- transcribe ---


def test_transcribe_request_shape_and_header_duration(monkeypatch):
    def handler(request):
        # The response LIES about the duration: audio_seconds must come
        # from the validated WAV header, not from the server.
        return httpx.Response(200, json={"text": "merhaba dünya", "duration": 999})

    adapter, captured = adapter_for(handler, monkeypatch, capability="stt")
    result = adapter.transcribe(make_wav(seconds=2.5), params={"language": "tr"})

    assert result.text == "merhaba dünya"
    assert result.audio_seconds == pytest.approx(2.5)
    assert result.reported_cost is None
    assert result.usage_estimated is True
    (request,) = captured
    assert request.method == "POST"
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer placeholder-key"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'name="model"' in body and b"speech-model-1" in body
    assert b'name="language"' in body and b"\r\ntr\r\n" in body
    assert b'name="response_format"' in body and b"json" in body
    assert b'filename="utterance.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b"RIFF" in body  # the WAV bytes travel as the file part


def test_transcribe_without_language_sends_none(monkeypatch):
    adapter, captured = adapter_for(
        lambda r: httpx.Response(200, json={"text": "hi"}), monkeypatch, capability="stt"
    )
    adapter.transcribe(make_wav())
    assert b'name="language"' not in captured[0].content


def test_transcribe_passes_provider_reported_cost_and_generation_id(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            headers={"X-Generation-Id": "gen-123"},
            json={
                "text": "selam",
                "usage": {"seconds": 2, "cost": 0.00042, "output_tokens": 3, "input_tokens": 0},
            },
        )

    adapter, _ = adapter_for(handler, monkeypatch, capability="stt")
    result = adapter.transcribe(make_wav(seconds=2))
    assert result.reported_cost == Decimal("0.00042")
    assert result.generation_id == "gen-123"
    assert result.output_tokens == 3
    assert result.usage_estimated is False
    assert "text" not in result.raw_usage


@pytest.mark.parametrize(
    "data",
    [
        make_wav(channels=2),
        make_wav(rate=44_100),
        make_wav(seconds=31),
        b"ID3\x04mp3",
    ],
)
def test_transcribe_rejects_bad_audio_before_any_request(monkeypatch, data):
    adapter, captured = adapter_for(
        lambda r: httpx.Response(200, json={"text": "x"}), monkeypatch, capability="stt"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.transcribe(data)
    assert excinfo.value.error_type == "invalid_audio"
    assert excinfo.value.retryable is False
    assert captured == []


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [
        (401, "auth_error", False),
        (429, "rate_limit", True),
        (500, "provider_error", True),
        (400, "provider_error", False),
    ],
)
def test_transcribe_error_mapping(monkeypatch, status, error_type, retryable):
    adapter, _ = adapter_for(
        lambda r: httpx.Response(status, json={"error": {"message": "secret-ish detail"}}),
        monkeypatch,
        capability="stt",
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.transcribe(make_wav())
    assert (excinfo.value.error_type, excinfo.value.retryable) == (error_type, retryable)
    assert "secret-ish" not in str(excinfo.value)


def test_transcribe_connection_error_is_timeout(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    adapter, _ = adapter_for(handler, monkeypatch, capability="stt")
    with pytest.raises(AdapterError) as excinfo:
        adapter.transcribe(make_wav())
    assert excinfo.value.error_type == "timeout"
    assert excinfo.value.retryable is True


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"no_text": True}),
    ],
)
def test_transcribe_malformed_response(monkeypatch, response):
    adapter, _ = adapter_for(lambda r: response, monkeypatch, capability="stt")
    with pytest.raises(AdapterError) as excinfo:
        adapter.transcribe(make_wav())
    assert excinfo.value.error_type == "provider_error"


# --- synthesize ---


def test_synthesize_request_shape_and_checked_wav(monkeypatch):
    wav = make_wav(rate=22_050, seconds=1.2)
    adapter, captured = adapter_for(
        lambda r: httpx.Response(200, headers={"content-type": "audio/wav"}, content=wav),
        monkeypatch,
        capability="tts",
    )
    result = adapter.synthesize("Merhaba.", params={"voice": "tr_TR-dfki-medium"})
    assert result.audio == wav
    assert result.content_type == "audio/wav"
    assert result.audio_seconds == pytest.approx(1.2)
    (request,) = captured
    assert request.url.path == "/v1/audio/speech"
    assert json.loads(request.content) == {
        "model": "speech-model-1",
        "voice": "tr_TR-dfki-medium",
        "input": "Merhaba.",
        "response_format": "wav",
    }


def test_synthesize_pcm_is_wrapped_into_wav(monkeypatch):
    pcm = b"\x00\x00" * 24_000
    adapter, captured = adapter_for(
        lambda r: httpx.Response(
            200,
            headers={"content-type": "audio/pcm;rate=24000;channels=1", "X-Generation-Id": "g-9"},
            content=pcm,
        ),
        monkeypatch,
        capability="tts",
    )
    result = adapter.synthesize("Hi", params={"voice": "alloy", "response_format": "pcm"})
    assert result.audio[:4] == b"RIFF"
    assert result.audio_seconds == pytest.approx(1.0)
    assert result.generation_id == "g-9"
    assert json.loads(captured[0].content)["response_format"] == "pcm"


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfb\x90\x00mp3-bytes",  # MP3 where WAV was asked for
        make_wav(seconds=61),  # over VOICE_TTS_MAX_SECONDS
        make_wav()[:-10],  # truncated
    ],
)
def test_synthesize_rejects_non_wav_or_oversized_output(monkeypatch, content):
    adapter, _ = adapter_for(
        lambda r: httpx.Response(200, headers={"content-type": "audio/wav"}, content=content),
        monkeypatch,
        capability="tts",
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.synthesize("Merhaba", params={"voice": "v"})
    assert excinfo.value.error_type == "invalid_audio"


def test_synthesize_without_voice_never_calls(monkeypatch):
    adapter, captured = adapter_for(lambda r: httpx.Response(200), monkeypatch, capability="tts")
    with pytest.raises(AdapterError) as excinfo:
        adapter.synthesize("x", params={})
    assert excinfo.value.error_type == "invalid_request"
    assert captured == []


def test_synthesize_error_mapping(monkeypatch):
    adapter, _ = adapter_for(
        lambda r: httpx.Response(503, json={"error": "down"}), monkeypatch, capability="tts"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.synthesize("x", params={"voice": "v"})
    assert excinfo.value.error_type == "provider_error"
    assert excinfo.value.retryable is True


# --- generation lookup (A1/A3 cost correction) ---


def test_generation_cost_lookup(monkeypatch):
    def handler(request):
        assert request.url.path == "/api/v1/generation"
        assert request.url.params["id"] == "gen-1"
        assert request.headers["authorization"] == "Bearer placeholder-key"
        return httpx.Response(200, json={"data": {"id": "gen-1", "total_cost": 0.0123}})

    adapter, _ = adapter_for(
        handler, monkeypatch, capability="tts", base_url="https://openrouter.test/api/v1"
    )
    assert adapter.generation_cost("gen-1") == Decimal("0.0123")


def test_generation_cost_not_yet_available(monkeypatch):
    adapter, _ = adapter_for(lambda r: httpx.Response(404), monkeypatch, capability="tts")
    assert adapter.generation_cost("gen-x") is None


def test_generation_cost_server_error_is_retryable(monkeypatch):
    adapter, _ = adapter_for(lambda r: httpx.Response(502), monkeypatch, capability="tts")
    with pytest.raises(AdapterError) as excinfo:
        adapter.generation_cost("gen-x")
    assert excinfo.value.retryable is True
