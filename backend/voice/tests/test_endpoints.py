"""The two voice endpoints (Phase 6 branch 2, brief D1-D3/D5/D9, A3/A5):
role x endpoint against the five Phase 4 principals, workspace isolation,
the in-memory upload cap, WAV validation, error mapping, throttles, and the
"transcript never stored or logged by voice" invariant. No real speech
model: FakeAdapter through the real gateway."""

import json
import logging
import uuid
from decimal import Decimal
from unittest import mock

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from rest_framework.throttling import ScopedRateThrottle

from accounts.services import register_user
from ai_core.adapters.base import AdapterError
from ai_core.adapters.fake import FAKE_TRANSCRIPT, FakeAdapter
from ai_core.exceptions import BudgetExceeded
from ai_core.gateway import InferenceGateway
from ai_core.models import AIBudget, ModelProvider, ModelUsage
from assistant.models import Conversation
from voice.models import VoiceTurnTrace
from voice.tests.conftest import SPEECH, TRANSCRIPTIONS, make_wav
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PRINCIPALS = [
    ("owner-web", 200),
    ("owner-pat-cli", 403),
    ("owner-pat-openai", 403),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]
# Keys an audio ModelUsage report may never carry (D5: no audio, no text).
_CONTENT_KEYS = {"text", "audio", "transcript", "input", "messages", "content"}


def _upload(client, conversation_id, audio, **extra):
    return client.post(
        TRANSCRIPTIONS,
        {
            "conversation": str(conversation_id),
            "audio": SimpleUploadedFile("utterance.wav", audio, content_type="audio/wav"),
        },
        format="multipart",
        **extra,
    )


def _speak(client, conversation_id, text="Merhaba dünya.", **extra):
    body = {"conversation": str(conversation_id), "text": text}
    body.update(extra.pop("body", {}))
    return client.post(SPEECH, body, format="json", **extra)


# --- role x endpoint ---


@pytest.mark.parametrize(("principal", "expected"), PRINCIPALS)
def test_transcription_role_matrix(
    principal_client, conversation, speech_providers, principal, expected
):
    response = _upload(principal_client(principal), conversation.id, make_wav(seconds=1))
    assert response.status_code == expected
    calls = ModelUsage.objects.filter(purpose="voice.stt").count()
    assert calls == (1 if expected == 200 else 0)
    if expected == 403 and principal.startswith("owner-pat"):
        assert response.json()["code"] in {"channel_forbidden", "token_kind_forbidden"}


@pytest.mark.parametrize(("principal", "expected"), PRINCIPALS)
def test_speech_role_matrix(principal_client, conversation, speech_providers, principal, expected):
    response = _speak(principal_client(principal), conversation.id)
    assert response.status_code == expected
    assert ModelUsage.objects.filter(purpose="voice.tts").count() == (1 if expected == 200 else 0)


# --- happy paths ---


def test_transcription_returns_the_transcript_and_records_one_usage(
    owner, workspace, conversation, speech_providers, web_client
):
    response = _upload(web_client(owner), conversation.id, make_wav(seconds=2))
    assert response.status_code == 200
    body = response.json()
    assert body == {"text": FAKE_TRANSCRIPT, "audio_seconds": 2.0, "language": "tr"}
    assert response["Cache-Control"] == "no-store"
    usage = ModelUsage.objects.get(purpose="voice.stt")
    assert usage.workspace_id == workspace.id
    assert usage.success is True
    report = usage.context_report
    assert report["input_mode"] == "voice"
    assert report["language"] == "tr"
    assert report["input_bytes"] == len(make_wav(seconds=2))
    assert not _CONTENT_KEYS & set(report)
    # A3: the session budget key is server-held, derived from the conversation.
    assert report["budget_session"] == f"voice:{conversation.id}"


def test_speech_returns_a_checked_wav(owner, workspace, conversation, speech_providers, web_client):
    response = _speak(web_client(owner), conversation.id)
    assert response.status_code == 200
    assert response["Content-Type"] == "audio/wav"
    assert response["Cache-Control"] == "no-store"
    assert response.content[:4] == b"RIFF" and response.content[8:12] == b"WAVE"
    usage = ModelUsage.objects.get(purpose="voice.tts")
    assert usage.workspace_id == workspace.id
    assert usage.context_report["input_mode"] == "voice"
    assert not _CONTENT_KEYS & set(usage.context_report)


def test_usage_workspace_is_the_conversations_not_the_actors_personal_one(
    owner, speech_providers, web_client
):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel="web")
    conversation = Conversation.objects.create(workspace=firm, created_by=owner)
    client = web_client(owner)
    assert _upload(client, conversation.id, make_wav()).status_code == 200
    assert _speak(client, conversation.id).status_code == 200
    assert set(ModelUsage.objects.values_list("workspace_id", flat=True)) == {firm.id}


def test_budget_session_cannot_be_chosen_by_the_client(
    owner, conversation, speech_providers, web_client
):
    client = web_client(owner)
    _speak(client, conversation.id, body={"budget_session": "fresh-1"})
    response = client.post(
        TRANSCRIPTIONS,
        {
            "conversation": str(conversation.id),
            "budget_session": "fresh-2",
            "audio": SimpleUploadedFile("u.wav", make_wav(), content_type="audio/wav"),
        },
        format="multipart",
    )
    assert response.status_code == 200
    sessions = {u.context_report["budget_session"] for u in ModelUsage.objects.all()}
    assert sessions == {f"voice:{conversation.id}"}


# --- isolation ---


@pytest.mark.parametrize("endpoint", ["stt", "tts"])
def test_another_workspaces_conversation_is_404_with_no_gateway_call(
    owner, speech_providers, web_client, endpoint
):
    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    foreign = Conversation.objects.create(
        workspace=Workspace.objects.get(owner=stranger), created_by=stranger
    )
    client = web_client(owner)
    with (
        mock.patch.object(InferenceGateway, "transcribe") as transcribe,
        mock.patch.object(InferenceGateway, "synthesize") as synthesize,
    ):
        if endpoint == "stt":
            response = _upload(client, foreign.id, make_wav())
        else:
            response = _speak(client, foreign.id)
    assert response.status_code == 404
    transcribe.assert_not_called()
    synthesize.assert_not_called()
    assert not ModelUsage.objects.exists()


def test_unknown_conversation_is_404(owner, speech_providers, web_client):
    assert _upload(web_client(owner), uuid.uuid4(), make_wav()).status_code == 404


# --- D2: upload cap, never on disk ---


def test_upload_never_instantiates_the_temp_file_handler(
    owner, conversation, speech_providers, web_client
):
    with mock.patch.object(
        TemporaryFileUploadHandler, "__init__", side_effect=AssertionError("disk handler")
    ) as init:
        response = _upload(web_client(owner), conversation.id, make_wav(seconds=2))
    assert response.status_code == 200
    init.assert_not_called()


def test_oversized_declared_length_is_413_before_parsing(
    owner, conversation, speech_providers, web_client, settings
):
    settings.VOICE_MAX_UTTERANCE_SECONDS = 1
    with (
        mock.patch.object(TemporaryFileUploadHandler, "__init__") as init,
        mock.patch.object(InferenceGateway, "transcribe") as transcribe,
    ):
        response = _upload(web_client(owner), conversation.id, make_wav(seconds=3))
    assert response.status_code == 413
    assert response.json()["code"] == "voice.upload_too_large"
    init.assert_not_called()
    transcribe.assert_not_called()


def test_streamed_bytes_over_cap_abort_parsing_whatever_the_header_says(
    owner, conversation, speech_providers, web_client
):
    """The chunked-upload case: no trustworthy Content-Length, so the cap is
    enforced from the bytes actually received while parsing."""
    with (
        mock.patch("voice.views.max_request_bytes", return_value=10_000_000),
        mock.patch("voice.views.max_upload_bytes", return_value=4_000),
        mock.patch.object(TemporaryFileUploadHandler, "__init__") as init,
        mock.patch.object(InferenceGateway, "transcribe") as transcribe,
    ):
        response = _upload(web_client(owner), conversation.id, make_wav(seconds=1))
    assert response.status_code == 413
    assert response.json()["code"] == "voice.upload_too_large"
    init.assert_not_called()
    transcribe.assert_not_called()


def test_cap_is_below_djangos_in_memory_threshold(settings):
    from voice.services import max_request_bytes

    assert max_request_bytes() < 2_621_440  # FILE_UPLOAD_MAX_MEMORY_SIZE default
    assert getattr(settings, "FILE_UPLOAD_MAX_MEMORY_SIZE", 2_621_440) >= max_request_bytes()


# --- D3: WAV validation ---


def _truncated_header():
    return make_wav()[:30]


def _declared_size_lies():
    data = bytearray(make_wav())
    return bytes(data[:-100])  # data chunk shorter than declared


@pytest.mark.parametrize(
    "audio",
    [
        pytest.param(make_wav(channels=2), id="stereo"),
        pytest.param(make_wav(rate=44_100), id="44.1kHz"),
        pytest.param(b"ID3\x03\x00\x00\x00" + b"\xff\xfb" * 500, id="mp3"),
        pytest.param(_truncated_header(), id="truncated-header"),
        pytest.param(_declared_size_lies(), id="size-mismatch"),
        pytest.param(make_wav()[:44], id="no-frames"),
    ],
)
def test_unsupported_audio_is_415_before_any_model_call(
    owner, conversation, speech_providers, web_client, audio
):
    with mock.patch.object(FakeAdapter, "transcribe") as transcribe:
        response = _upload(web_client(owner), conversation.id, audio)
    assert response.status_code == 415
    assert response.json()["code"] == "voice.unsupported_audio"
    transcribe.assert_not_called()
    assert not ModelUsage.objects.exists()


# --- error mapping ---


def test_no_allowed_provider_is_503_and_never_reaches_a_disallowed_one(
    owner, conversation, make_provider, web_client
):
    # The fail-closed path uses a separate, deliberately disallowed stub --
    # never the seeded fakes (brief D4, plan-review H1).
    make_provider("cloud-stt-stub", ModelProvider.Capability.STT, allowed=False)
    with mock.patch.object(FakeAdapter, "transcribe") as transcribe:
        response = _upload(web_client(owner), conversation.id, make_wav())
    assert response.status_code == 503
    assert response.json()["code"] == "voice.no_local_provider"
    transcribe.assert_not_called()
    usage = ModelUsage.objects.get()
    assert usage.error_type == "no_provider"
    assert usage.routing_reason == "personal_data_required"


def test_tts_no_allowed_provider_is_503(owner, conversation, make_provider, web_client):
    make_provider("cloud-tts-stub", ModelProvider.Capability.TTS, allowed=False)
    response = _speak(web_client(owner), conversation.id)
    assert response.status_code == 503
    assert response.json()["code"] == "voice.no_local_provider"


def test_stt_gateway_failure_is_502(owner, conversation, speech_providers, web_client):
    with mock.patch.object(
        FakeAdapter, "transcribe", side_effect=AdapterError("provider_error", retryable=False)
    ):
        response = _upload(web_client(owner), conversation.id, make_wav())
    assert response.status_code == 502
    assert response.json()["code"] == "voice.stt_failed"


def test_tts_gateway_failure_is_502(owner, conversation, speech_providers, web_client):
    with mock.patch.object(
        FakeAdapter, "synthesize", side_effect=AdapterError("timeout", retryable=False)
    ):
        response = _speak(web_client(owner), conversation.id)
    assert response.status_code == 502
    assert response.json()["code"] == "voice.tts_failed"


def test_tts_output_that_is_not_a_wav_is_502(owner, conversation, speech_providers, web_client):
    from ai_core.adapters.base import SynthesizeAdapterResult

    with mock.patch.object(
        FakeAdapter,
        "synthesize",
        return_value=SynthesizeAdapterResult(audio=b"not a wav", content_type="audio/wav"),
    ):
        response = _speak(web_client(owner), conversation.id)
    assert response.status_code == 502
    assert response.json()["code"] == "voice.tts_failed"


@pytest.mark.parametrize("endpoint", ["stt", "tts"])
def test_budget_exceeded_is_429(owner, conversation, speech_providers, web_client, endpoint):
    target = "transcribe" if endpoint == "stt" else "synthesize"
    with mock.patch.object(
        InferenceGateway, target, side_effect=BudgetExceeded("audio", "monthly")
    ):
        if endpoint == "stt":
            response = _upload(web_client(owner), conversation.id, make_wav())
        else:
            response = _speak(web_client(owner), conversation.id)
    assert response.status_code == 429
    assert response.json()["code"] == "voice.budget_exceeded"


def test_real_session_budget_refuses_before_provider_selection(
    owner, conversation, make_provider, web_client
):
    """A3 end to end: a priced STT row and a spent session window -> 429
    with one `budget_exceeded` row, no adapter call."""
    priced = make_provider("priced-stt", ModelProvider.Capability.STT)
    priced.price_per_audio_second = Decimal("1")
    priced.save()
    AIBudget.objects.create(
        name="audio-test", purposes=["voice.stt"], session_limit_usd=Decimal("0.5")
    )
    with mock.patch.object(FakeAdapter, "transcribe") as transcribe:
        response = _upload(web_client(owner), conversation.id, make_wav(seconds=1))
    assert response.status_code == 429
    assert response.json()["code"] == "voice.budget_exceeded"
    transcribe.assert_not_called()
    usage = ModelUsage.objects.get()
    assert usage.error_type == "budget_exceeded"
    assert usage.context_report["budget_session"] == f"voice:{conversation.id}"


# --- speech request validation ---


def test_speech_text_over_the_cap_is_400(owner, conversation, speech_providers, web_client):
    response = _speak(web_client(owner), conversation.id, text="a" * 401)
    assert response.status_code == 400
    assert not ModelUsage.objects.exists()


@pytest.mark.parametrize("text", ["", "   "])
def test_speech_blank_text_is_400(owner, conversation, speech_providers, web_client, text):
    assert _speak(web_client(owner), conversation.id, text=text).status_code == 400


def test_speech_unknown_language_is_400(owner, conversation, speech_providers, web_client):
    response = _speak(web_client(owner), conversation.id, body={"language": "xx"})
    assert response.status_code == 400


def test_speech_language_is_passed_to_the_gateway(
    owner, conversation, speech_providers, web_client
):
    response = _speak(web_client(owner), conversation.id, body={"language": "en"})
    assert response.status_code == 200
    assert ModelUsage.objects.get(purpose="voice.tts").context_report["language"] == "en"


def test_speech_rejects_a_non_json_body(owner, conversation, speech_providers, web_client):
    response = web_client(owner).post(
        SPEECH, {"conversation": str(conversation.id), "text": "x"}, format="multipart"
    )
    assert response.status_code == 415


# --- D9: throttles ---


@pytest.mark.parametrize("endpoint", ["stt", "tts"])
def test_voice_endpoints_are_throttled(
    owner, conversation, speech_providers, web_client, monkeypatch, endpoint
):
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {"voice_stt": "2/min", "voice_tts": "2/min"}
    )
    client = web_client(owner)
    statuses = []
    for _ in range(3):
        if endpoint == "stt":
            statuses.append(_upload(client, conversation.id, make_wav()).status_code)
        else:
            statuses.append(_speak(client, conversation.id).status_code)
    assert statuses == [200, 200, 429]


def test_default_voice_rates():
    from django.conf import settings

    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["voice_stt"] == "20/min"
    assert rates["voice_tts"] == "120/min"


class _DownCache:
    def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    set = get


@pytest.mark.parametrize("endpoint", ["stt", "tts"])
def test_throttle_backend_down_fails_closed_with_503(
    owner, conversation, speech_providers, web_client, monkeypatch, endpoint
):
    monkeypatch.setattr(ScopedRateThrottle, "cache", _DownCache())
    with (
        mock.patch.object(InferenceGateway, "transcribe") as transcribe,
        mock.patch.object(InferenceGateway, "synthesize") as synthesize,
    ):
        if endpoint == "stt":
            response = _upload(web_client(owner), conversation.id, make_wav())
        else:
            response = _speak(web_client(owner), conversation.id)
    assert response.status_code == 503
    assert response.json()["code"] == "throttle_backend_unavailable"
    transcribe.assert_not_called()
    synthesize.assert_not_called()


# --- the transcript is never stored or logged by the voice app ---


def _all_rows_text():
    dump = []
    for model in apps.get_models():
        for row in model._default_manager.all().values():
            dump.append(json.dumps(row, default=str, ensure_ascii=False))
    return "\n".join(dump)


def test_transcript_is_never_written_to_a_row_or_a_log(
    owner, conversation, speech_providers, web_client, caplog
):
    caplog.set_level(logging.DEBUG)
    trace = str(uuid.uuid4())
    response = _upload(web_client(owner), conversation.id, make_wav(), HTTP_X_FRIDAY_TRACE=trace)
    assert response.status_code == 200
    assert response.json()["text"] == FAKE_TRANSCRIPT
    assert FAKE_TRANSCRIPT not in _all_rows_text()
    assert FAKE_TRANSCRIPT not in caplog.text
    # ...and the trace row exists, timings only.
    assert VoiceTurnTrace.objects.filter(trace_id=trace).exists()


def test_speech_text_is_never_written_to_a_row_or_a_log(
    owner, conversation, speech_providers, web_client, caplog
):
    caplog.set_level(logging.DEBUG)
    sentence = "Kanarya-7 gizli cümle."
    response = _speak(
        web_client(owner), conversation.id, text=sentence, HTTP_X_FRIDAY_TRACE=str(uuid.uuid4())
    )
    assert response.status_code == 200
    assert sentence not in _all_rows_text()
    assert sentence not in caplog.text
