"""`GET /api/v1/voice/receipt` (Phase 6 branch 3, brief A7): the
"bu ne kadar tuttu?" voice route's ModelUsage receipt. A read only -- role x
endpoint against the five Phase 4 principals, workspace isolation, numbers
without any content, and no model call or write of its own."""

import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from ai_core.models import ModelProvider, ModelUsage
from assistant.models import Conversation
from voice.tests.conftest import SPEECH, TRANSCRIPTIONS, make_wav

pytestmark = pytest.mark.django_db

RECEIPT = "/api/v1/voice/receipt"
PRINCIPALS = [
    ("owner-web", 200),
    ("owner-pat-cli", 403),
    ("owner-pat-openai", 403),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]
_CONTENT_KEYS = {"text", "audio", "transcript", "input", "messages", "content", "prompt"}


def _usage(workspace, purpose, trace_id="", *, cost="0", tokens=(0, 0), seconds=None, **extra):
    report = {"input_mode": "voice"}
    if trace_id:
        report["trace_id"] = trace_id
    if seconds is not None:
        report["audio_seconds"] = seconds
    return ModelUsage.objects.create(
        workspace=workspace,
        purpose=purpose,
        success=extra.pop("success", True),
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        estimated_cost=Decimal(cost),
        context_report=report,
        **extra,
    )


def _get(client, conversation_id, trace=None):
    params = {"conversation": str(conversation_id)}
    if trace:
        params["trace"] = trace
    return client.get(RECEIPT, params)


@pytest.mark.parametrize(("principal", "expected"), PRINCIPALS)
def test_receipt_role_matrix(principal_client, conversation, principal, expected):
    before = ModelUsage.objects.count()
    response = _get(principal_client(principal), conversation.id)
    assert response.status_code == expected
    # A read: never a gateway call, so never a ModelUsage row of its own.
    assert ModelUsage.objects.count() == before
    if expected == 403 and principal.startswith("owner-pat"):
        assert response.json()["code"] in {"channel_forbidden", "token_kind_forbidden"}


def test_receipt_sums_the_turn_by_trace_id(owner, workspace, conversation, web_client):
    trace = str(uuid.uuid4())
    _usage(workspace, "router.intent", trace, tokens=(40, 5), cost="0.000100")
    _usage(workspace, "chat", trace, tokens=(300, 120), cost="0.002000")
    _usage(workspace, "voice.stt", trace, seconds=2.5)
    _usage(workspace, "voice.tts", trace, seconds=1.25)
    _usage(workspace, "voice.tts", trace, seconds=0.75, success=False)
    # Another turn of the same workspace is not part of this receipt.
    _usage(workspace, "chat", str(uuid.uuid4()), tokens=(999, 999), cost="1.000000")

    response = _get(web_client(owner), conversation.id, trace)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    turn = body["turn"]
    assert turn["trace_id"] == trace
    assert turn["calls"] == 5
    assert turn["failed_calls"] == 1
    assert turn["input_tokens"] == 340
    assert turn["output_tokens"] == 125
    assert turn["audio_seconds"] == 4.5
    assert turn["estimated_cost"] == "0.002100"
    assert turn["purposes"] == {"router.intent": 1, "chat": 1, "voice.stt": 1, "voice.tts": 2}
    assert body["currency"] == "USD"
    assert body["today"]["calls"] == 6
    assert body["today"]["estimated_cost"] == "1.002100"


def test_receipt_holds_numbers_only(owner, workspace, conversation, web_client):
    trace = str(uuid.uuid4())
    _usage(workspace, "chat", trace, tokens=(1, 1))
    body = _get(web_client(owner), conversation.id, trace).json()

    def keys(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                yield key
                yield from keys(inner)

    assert not _CONTENT_KEYS & set(keys(body))


def test_receipt_without_trace_has_no_turn(owner, workspace, conversation, web_client):
    _usage(workspace, "chat", tokens=(10, 10), cost="0.5")
    body = _get(web_client(owner), conversation.id).json()
    assert body["turn"] is None
    assert body["today"]["calls"] == 1
    assert body["today"]["estimated_cost"] == "0.500000"


def test_unknown_trace_has_no_turn(owner, workspace, conversation, web_client):
    body = _get(web_client(owner), conversation.id, str(uuid.uuid4())).json()
    assert body["turn"] is None


def test_malformed_trace_is_400(owner, conversation, web_client):
    assert _get(web_client(owner), conversation.id, "not-a-uuid").status_code == 400


def test_another_workspaces_conversation_is_404(owner, web_client):
    from accounts.services import register_user
    from workspaces.models import Workspace

    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    foreign = Conversation.objects.create(
        workspace=Workspace.objects.get(owner=stranger), created_by=stranger
    )
    assert _get(web_client(owner), foreign.id).status_code == 404


def test_another_workspaces_usage_never_counts(owner, workspace, conversation, web_client):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel="web")
    trace = str(uuid.uuid4())
    # The same trace id in another workspace (correlation id, never a key
    # into someone else's rows) and the firm's own spend stay out.
    _usage(firm, "chat", trace, tokens=(5, 5), cost="3.0")
    _usage(workspace, "chat", trace, tokens=(1, 1), cost="0.1")
    body = _get(web_client(owner), conversation.id, trace).json()
    assert body["turn"]["calls"] == 1
    assert body["turn"]["estimated_cost"] == "0.100000"
    assert body["today"]["estimated_cost"] == "0.100000"


def test_yesterdays_usage_is_not_today(owner, workspace, conversation, web_client):
    row = _usage(workspace, "chat", cost="2.0")
    ModelUsage.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=2)
    )
    body = _get(web_client(owner), conversation.id).json()
    assert body["today"] == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0.000000",
    }


def test_local_only_flag(owner, workspace, conversation, web_client):
    provider = ModelProvider.objects.create(
        name="paid", provider_type=ModelProvider.ProviderType.FAKE, model_id="p"
    )
    trace = str(uuid.uuid4())
    _usage(workspace, "voice.stt", trace, seconds=1.0)
    assert _get(web_client(owner), conversation.id, trace).json()["turn"]["local_only"] is True
    _usage(workspace, "chat", trace, cost="0.01", provider=provider)
    assert _get(web_client(owner), conversation.id, trace).json()["turn"]["local_only"] is False


def test_a_real_spoken_turn_is_receipted_end_to_end(
    owner, conversation, speech_providers, chat_setup, web_client
):
    """Transcription, message and speech requests carrying one trace id all
    show up in the receipt of that turn (A5 trace -> A7 receipt)."""
    client = web_client(owner)
    trace = str(uuid.uuid4())
    headers = {"HTTP_X_FRIDAY_TRACE": trace}
    from django.core.files.uploadedfile import SimpleUploadedFile

    assert (
        client.post(
            TRANSCRIPTIONS,
            {
                "conversation": str(conversation.id),
                "audio": SimpleUploadedFile("u.wav", make_wav(seconds=1), "audio/wav"),
            },
            format="multipart",
            **headers,
        ).status_code
        == 200
    )
    response = client.post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "Merhaba", "input_mode": "voice"},
        format="json",
        **headers,
    )
    assert response.status_code == 200
    b"".join(response.streaming_content)
    assert (
        client.post(
            SPEECH,
            {"conversation": str(conversation.id), "text": "Merhaba."},
            format="json",
            **headers,
        ).status_code
        == 200
    )
    turn = _get(client, conversation.id, trace).json()["turn"]
    assert turn["purposes"]["voice.stt"] == 1
    assert turn["purposes"]["voice.tts"] == 1
    assert turn["purposes"]["chat"] == 1
    assert turn["audio_seconds"] >= 1.0
