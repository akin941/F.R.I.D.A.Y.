"""Voice transport services (Phase 6 branch 2, ADR-011, brief D1-D5/D9, A3/A5).

Two edges around the unchanged chat turn:

- `transcribe_utterance`: a validated 16 kHz mono PCM16 WAV (bytes held in
  request memory only) -> one `voice.stt` gateway call -> the transcript,
  returned to the caller and nothing else. The transcript is never written
  to a row or a log by this app: the client sends it on through the
  ordinary messages endpoint, where it is a user message like typed text.
- `synthesize_sentence`: one reply sentence -> one `voice.tts` gateway call
  -> a checked `audio/wav`.

Every call goes through `ai_core`'s InferenceGateway (ADR-003): one
`ModelUsage` row each, workspace = the conversation's, `context_report`
carrying `input_mode` and the turn's trace id -- never audio, never text.
Audio calls are always personal data in the gateway itself (D5).

The per-session budget window (A3) is keyed on a SERVER-held value -- the
conversation id -- never on a request field, so a client cannot dodge the
session cap by sending a fresh session value per call (branch-1 review
follow-up).
"""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from ai_core.audio import InvalidAudio, normalize_tts_wav, parse_stt_input
from ai_core.exceptions import BudgetExceeded, GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from assistant.models import Message
from core.tracing import trace_report
from voice.models import CLIENT_STAGES, MAX_STAGE_MS, VoiceTurnTrace

logger = logging.getLogger(__name__)

PURPOSE_STT = "voice.stt"
PURPOSE_TTS = "voice.tts"
WAV_CONTENT_TYPE = "audio/wav"
# WAV header allowance on top of the PCM payload when sizing the upload cap
# (a canonical header is 44 bytes; leave room for a small LIST chunk).
_WAV_HEADER_ALLOWANCE = 1024
# Multipart framing (boundaries, part headers, the `conversation` field) on
# top of the file itself, for the pre-parse Content-Length check.
_MULTIPART_ALLOWANCE = 8 * 1024


class VoiceServiceError(Exception):
    """A voice request refused with a stable `code` (core/error_codes.py)
    and HTTP `status`. Messages are content-free: never audio, never text."""

    code = "voice.error"
    status = 400
    default_message = "Voice request failed."

    def __init__(self, message=""):
        super().__init__(message or self.default_message)


class UnsupportedAudio(VoiceServiceError):
    code = "voice.unsupported_audio"
    status = 415
    default_message = "Audio must be a 16 kHz mono 16-bit PCM WAV."


class UploadTooLarge(VoiceServiceError):
    code = "voice.upload_too_large"
    status = 413
    default_message = "Utterance exceeds the maximum length."


class NoLocalProvider(VoiceServiceError):
    code = "voice.no_local_provider"
    status = 503
    default_message = "No speech provider allowed to process personal data is available."


class SpeechToTextFailed(VoiceServiceError):
    code = "voice.stt_failed"
    status = 502
    default_message = "Transcription failed."


class TextToSpeechFailed(VoiceServiceError):
    code = "voice.tts_failed"
    status = 502
    default_message = "Speech synthesis failed."


class VoiceBudgetExceeded(VoiceServiceError):
    code = "voice.budget_exceeded"
    status = 429
    default_message = "The audio budget is exhausted."


def max_upload_bytes():
    """The utterance cap in bytes (D2/D3): `VOICE_MAX_UTTERANCE_SECONDS` of
    16 kHz mono PCM16 plus a header allowance -- 30 s = 960 KB, below
    Django's 2.5 MB in-memory threshold."""
    return settings.VOICE_MAX_UTTERANCE_SECONDS * 16_000 * 2 + _WAV_HEADER_ALLOWANCE


def max_request_bytes():
    """The whole multipart request's cap (pre-parse Content-Length check)."""
    return max_upload_bytes() + _MULTIPART_ALLOWANCE


def budget_session_for(conversation):
    """The A3 per-session budget key: server-held, derived from the
    conversation, never from the request."""
    return f"voice:{conversation.pk}"


def _report(trace_id):
    return {"input_mode": Message.InputMode.VOICE, **trace_report(trace_id)}


def _map_gateway_error(exc, failure_cls):
    if isinstance(exc, BudgetExceeded):
        return VoiceBudgetExceeded()
    if isinstance(exc, NoProviderAvailable):
        return NoLocalProvider()
    return failure_cls(f"{failure_cls.default_message} ({exc.error_type})")


def transcribe_utterance(*, conversation, actor, audio, trace_id=""):
    """D1/D2/D3: validate `audio` and transcribe it in the request thread.
    Returns `{"text", "audio_seconds", "language"}`. Raises a
    `VoiceServiceError` -- before any model call for bad audio."""
    try:
        info = parse_stt_input(audio, max_seconds=settings.VOICE_MAX_UTTERANCE_SECONDS)
    except InvalidAudio as exc:
        if exc.reason == "too_long":
            raise UploadTooLarge() from exc
        raise UnsupportedAudio() from exc
    try:
        result = InferenceGateway().transcribe(
            PURPOSE_STT,
            bytes(audio),
            content_type=WAV_CONTENT_TYPE,
            params={"language": settings.VOICE_STT_LANGUAGE},
            actor=actor,
            workspace=conversation.workspace,
            context_report=_report(trace_id),
            budget_session=budget_session_for(conversation),
        )
    except GatewayError as exc:
        raise _map_gateway_error(exc, SpeechToTextFailed) from exc
    if trace_id:
        record_trace(
            conversation=conversation,
            trace_id=trace_id,
            server_timings={"stt_ms": result.usage.latency_ms},
        )
    return {
        "text": result.text,
        # The duration of audio that really arrived (D3), not the server's.
        "audio_seconds": round(info.seconds, 3),
        "language": result.language or "",
    }


def synthesize_sentence(*, conversation, actor, text, language="", trace_id="", timings=None):
    """D1/D7/A2: one reply sentence -> checked WAV bytes. `language` picks
    the TTS route (provider chosen by the gateway among allowed rows, never
    by the client). `timings` are the client's stage timings for the trace
    (A5), reported with the turn's last speech request."""
    language = language or settings.VOICE_STT_LANGUAGE
    try:
        result = InferenceGateway().synthesize(
            PURPOSE_TTS,
            text,
            params={"language": language},
            actor=actor,
            workspace=conversation.workspace,
            context_report=_report(trace_id),
            budget_session=budget_session_for(conversation),
        )
    except GatewayError as exc:
        raise _map_gateway_error(exc, TextToSpeechFailed) from exc
    try:
        wav, _info = normalize_tts_wav(result.audio, max_seconds=settings.VOICE_TTS_MAX_SECONDS)
    except InvalidAudio as exc:
        raise TextToSpeechFailed(
            f"Speech synthesis returned invalid audio ({exc.reason})."
        ) from exc
    if trace_id:
        record_trace(
            conversation=conversation,
            trace_id=trace_id,
            server_timings={"tts_first_ms": result.usage.latency_ms},
            client_timings=timings,
        )
    return wav


def _clean_timings(timings, allowed):
    cleaned = {}
    for key, value in (timings or {}).items():
        if key not in allowed or isinstance(value, bool) or not isinstance(value, int):
            continue
        if 0 <= value <= MAX_STAGE_MS:
            cleaned[key] = value
    return cleaned


def record_trace(*, conversation, trace_id, server_timings=None, client_timings=None):
    """Upsert the turn's `VoiceTurnTrace` (A5): timings only. A server
    stage is written once (the first STT/TTS call of the turn); client
    stages are overwritten by the latest report. A trace id already used
    by another conversation of the workspace is left alone (correlation
    id, never a key into someone else's row). Never raises: a trace is
    observability, it must not fail the voice request."""
    server = _clean_timings(server_timings, {"stt_ms", "tts_first_ms"})
    client = _clean_timings(client_timings, set(CLIENT_STAGES))
    if not server and not client:
        return None
    try:
        with transaction.atomic():
            trace, _created = VoiceTurnTrace.objects.select_for_update().get_or_create(
                workspace=conversation.workspace,
                trace_id=trace_id,
                defaults={"conversation": conversation},
            )
            if trace.conversation_id != conversation.pk:
                return None
            changed = []
            for key, value in server.items():
                if getattr(trace, key) is None:
                    setattr(trace, key, value)
                    changed.append(key)
            for key, value in client.items():
                setattr(trace, key, value)
                changed.append(key)
            if changed:
                trace.save(update_fields=[*changed, "updated_at"])
            return trace
    except IntegrityError:
        logger.warning("voice trace upsert raced; timings dropped")
        return None
