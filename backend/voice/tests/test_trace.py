"""The per-turn trace (Phase 6 A5): `X-Friday-Trace` on the voice and
message requests, recorded in every ModelUsage row of the turn, and the
timings-only `VoiceTurnTrace` row with its 30-day prune."""

import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from accounts.tests.node_voice_helpers import bearer, mint_token, pair_node
from ai_core.models import ModelUsage
from assistant.models import Conversation
from assistant.services import create_conversation
from core.tracing import request_trace_id
from voice.models import STAGES, VoiceTurnTrace
from voice.services import record_trace
from voice.tasks import prune_voice_turn_traces, prune_voice_turn_traces_task
from voice.tests.conftest import SPEECH, TRANSCRIPTIONS, make_wav
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def _upload(client, conversation, **extra):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        TRANSCRIPTIONS,
        {
            "conversation": str(conversation.id),
            "audio": SimpleUploadedFile("u.wav", make_wav(), content_type="audio/wav"),
        },
        format="multipart",
        **extra,
    )


def _speak(client, conversation, body=None, **extra):
    payload = {"conversation": str(conversation.id), "text": "Tamam."}
    payload.update(body or {})
    return client.post(SPEECH, payload, format="json", **extra)


# --- the model holds no text or audio ---


def test_trace_model_fields_are_an_allow_list():
    """A release-gate regression (brief test strategy): text or audio in
    `VoiceTurnTrace`. Any new column must be added here deliberately."""
    names = {field.name for field in VoiceTurnTrace._meta.get_fields()}
    assert names == {
        "id",
        "workspace",
        "trace_id",
        "conversation",
        "input_mode",
        "source",
        "created_at",
        "updated_at",
        *STAGES,
    }
    for name in STAGES:
        field = VoiceTurnTrace._meta.get_field(name)
        assert field.get_internal_type() == "PositiveIntegerField"
    # No free-text or binary column at all (input_mode is a short choice).
    for field in VoiceTurnTrace._meta.concrete_fields:
        assert field.get_internal_type() not in {"TextField", "BinaryField", "JSONField"}
        if field.get_internal_type() == "CharField":
            assert field.name in {"input_mode", "source"} and field.choices
    source_choices = VoiceTurnTrace._meta.get_field("source").choices
    assert {value for value, _ in source_choices} == {"web", "node"}


def test_trace_rows_are_workspace_scoped_through_for_actor(owner, workspace, conversation):
    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    foreign_ws = Workspace.objects.get(owner=stranger)
    foreign_conv = Conversation.objects.create(workspace=foreign_ws, created_by=stranger)
    mine = record_trace(
        conversation=conversation, trace_id=str(uuid.uuid4()), server_timings={"stt_ms": 5}
    )
    theirs = record_trace(
        conversation=foreign_conv, trace_id=str(uuid.uuid4()), server_timings={"stt_ms": 7}
    )
    assert list(VoiceTurnTrace.objects.for_actor(owner)) == [mine]
    assert list(VoiceTurnTrace.objects.for_actor(stranger)) == [theirs]


# --- header parsing ---


@pytest.mark.parametrize(
    "raw",
    ["", "not-a-uuid", "x" * 200, "'; DROP TABLE", "123", "Kanarya metni"],
)
def test_malformed_trace_header_is_ignored(raw):
    request = mock.Mock(META={"HTTP_X_FRIDAY_TRACE": raw})
    assert request_trace_id(request) == ""


def test_trace_header_is_canonicalised():
    value = uuid.uuid4()
    request = mock.Mock(META={"HTTP_X_FRIDAY_TRACE": str(value).upper()})
    assert request_trace_id(request) == str(value)


# --- end to end ---


def test_a_voice_turn_records_one_trace_id_on_every_usage_row(
    owner, conversation, speech_providers, chat_setup, web_client
):
    trace = str(uuid.uuid4())
    client = web_client(owner)
    header = {"HTTP_X_FRIDAY_TRACE": trace}
    assert _upload(client, conversation, **header).status_code == 200
    response = client.post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "Merhaba", "input_mode": "voice"},
        format="json",
        **header,
    )
    assert response.status_code == 200
    b"".join(response.streaming_content)
    assert (
        _speak(
            client,
            conversation,
            body={"timings": {"end_of_turn_ms": 310, "playback_start_ms": 40, "total_ms": 1400}},
            **header,
        ).status_code
        == 200
    )
    turn_purposes = {"voice.stt", "router.intent", "router.task_draft", "chat", "voice.tts"}
    rows = list(ModelUsage.objects.filter(purpose__in=turn_purposes))
    purposes = {row.purpose for row in rows}
    assert {"voice.stt", "router.intent", "chat", "voice.tts"} <= purposes
    for row in rows:
        assert row.context_report.get("trace_id") == trace, row.purpose
        assert row.context_report.get("input_mode") == "voice", row.purpose

    trace_row = VoiceTurnTrace.objects.get(trace_id=trace)
    assert trace_row.conversation_id == conversation.id
    assert trace_row.workspace_id == conversation.workspace_id
    assert trace_row.stt_ms is not None and trace_row.tts_first_ms is not None
    assert trace_row.end_of_turn_ms == 310
    assert trace_row.playback_start_ms == 40
    assert trace_row.total_ms == 1400


def test_malformed_trace_header_is_not_stored_anywhere(
    owner, conversation, speech_providers, web_client
):
    client = web_client(owner)
    bad = "Kanarya-not-a-uuid"
    assert _upload(client, conversation, HTTP_X_FRIDAY_TRACE=bad).status_code == 200
    assert _speak(client, conversation, HTTP_X_FRIDAY_TRACE=bad).status_code == 200
    assert not VoiceTurnTrace.objects.exists()
    for row in ModelUsage.objects.all():
        assert "trace_id" not in row.context_report
        assert bad not in str(row.context_report)


def test_no_header_means_no_trace_row(owner, conversation, speech_providers, web_client):
    assert _upload(web_client(owner), conversation).status_code == 200
    assert not VoiceTurnTrace.objects.exists()


def test_server_stage_is_written_once_and_client_stages_overwrite(conversation):
    trace = str(uuid.uuid4())
    record_trace(conversation=conversation, trace_id=trace, server_timings={"tts_first_ms": 90})
    record_trace(
        conversation=conversation,
        trace_id=trace,
        server_timings={"tts_first_ms": 400},
        client_timings={"total_ms": 1000},
    )
    record_trace(conversation=conversation, trace_id=trace, client_timings={"total_ms": 1200})
    row = VoiceTurnTrace.objects.get(trace_id=trace)
    assert row.tts_first_ms == 90
    assert row.total_ms == 1200


def test_garbage_timings_are_dropped(conversation):
    trace = str(uuid.uuid4())
    assert (
        record_trace(
            conversation=conversation,
            trace_id=trace,
            client_timings={"total_ms": -1, "end_of_turn_ms": "12", "transcript": 5, "x": True},
        )
        is None
    )
    assert not VoiceTurnTrace.objects.exists()


def test_out_of_range_client_timing_is_a_400(owner, conversation, speech_providers, web_client):
    response = _speak(
        web_client(owner),
        conversation,
        body={"timings": {"total_ms": 10**9}},
        HTTP_X_FRIDAY_TRACE=str(uuid.uuid4()),
    )
    assert response.status_code == 400


def test_a_trace_id_of_another_conversation_is_left_alone(owner, workspace, conversation):
    other = Conversation.objects.create(workspace=workspace, created_by=owner)
    trace = str(uuid.uuid4())
    record_trace(conversation=conversation, trace_id=trace, server_timings={"stt_ms": 5})
    assert record_trace(conversation=other, trace_id=trace, client_timings={"total_ms": 9}) is None
    row = VoiceTurnTrace.objects.get(trace_id=trace)
    assert row.conversation_id == conversation.id and row.total_ms is None


# --- prune beat ---


def test_prune_deletes_rows_older_than_retention(conversation, settings):
    settings.VOICE_TRACE_RETENTION_DAYS = 30
    old = record_trace(
        conversation=conversation, trace_id=str(uuid.uuid4()), server_timings={"stt_ms": 1}
    )
    fresh = record_trace(
        conversation=conversation, trace_id=str(uuid.uuid4()), server_timings={"stt_ms": 2}
    )
    VoiceTurnTrace.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
    assert prune_voice_turn_traces() == 1
    assert list(VoiceTurnTrace.objects.values_list("pk", flat=True)) == [fresh.pk]
    assert prune_voice_turn_traces_task() == 0


# --- Phase 11 D14: `source` from the channel, the live stages ---


def test_live_stages_are_recorded_from_the_timings_body(
    owner, conversation, speech_providers, web_client
):
    trace = str(uuid.uuid4())
    response = _speak(
        web_client(owner),
        conversation,
        body={"timings": {"wake_to_listen_ms": 45, "interrupted_at_ms": 800, "total_ms": 1500}},
        HTTP_X_FRIDAY_TRACE=trace,
    )
    assert response.status_code == 200
    row = VoiceTurnTrace.objects.get(trace_id=trace)
    assert (row.wake_to_listen_ms, row.interrupted_at_ms, row.total_ms) == (45, 800, 1500)


def test_a_web_request_claiming_source_node_records_web(
    owner, conversation, speech_providers, web_client
):
    trace = str(uuid.uuid4())
    response = _speak(
        web_client(owner),
        conversation,
        body={"timings": {"source": "node", "total_ms": 900}},
        HTTP_X_FRIDAY_TRACE=trace,
    )
    assert response.status_code == 200
    row = VoiceTurnTrace.objects.get(trace_id=trace)
    assert row.source == "web" and row.total_ms == 900


def test_a_node_voice_request_claiming_source_web_records_node(owner, workspace, speech_providers):
    _, secret = pair_node(owner)
    client = bearer(mint_token(secret))
    live = create_conversation(workspace=workspace, actor=owner, channel="node_voice")
    trace = str(uuid.uuid4())
    assert _upload(client, live, HTTP_X_FRIDAY_TRACE=trace).status_code == 200
    response = _speak(
        client,
        live,
        body={"timings": {"source": "web", "wake_to_listen_ms": 30, "total_ms": 1100}},
        HTTP_X_FRIDAY_TRACE=trace,
    )
    assert response.status_code == 200
    row = VoiceTurnTrace.objects.get(trace_id=trace)
    assert row.source == "node"
    assert row.stt_ms is not None and row.tts_first_ms is not None
    assert (row.wake_to_listen_ms, row.total_ms) == (30, 1100)


def test_record_trace_refuses_an_unknown_source(conversation):
    row = record_trace(
        conversation=conversation,
        trace_id=str(uuid.uuid4()),
        server_timings={"stt_ms": 3},
        source="mars",
    )
    assert row.source == "web"
