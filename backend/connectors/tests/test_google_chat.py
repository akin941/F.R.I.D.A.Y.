"""Branch 8 "done when": "bugün neler var, son gelen mail ne diyor?" is
answered through a local model -- a Google read reaches chat fenced, marks
the conversation `private` + `personal_data`, and every gateway call from
the argument extraction on carries `personal_data=True`; with only a cloud
provider nothing is read. The real chat view and router plumbing, the real
Google bridge against `httpx.MockTransport`."""

import pytest

from assistant.models import Conversation, Message
from audit.models import AuditLog
from connectors.bridges import google
from connectors.manifest import get_manifest
from connectors.tests import google_fake

# The chat-read fixtures (prompts, providers, gateway spy, router stubs),
# re-exported from the framework's chat-read tests.
from connectors.tests import test_chat_reads as chat_reads
from connectors.tests.google_fake import FakeGoogle

ai = chat_reads.ai
calls = chat_reads.calls
cloud_provider = chat_reads.cloud_provider
local_provider = chat_reads.local_provider
route = chat_reads.route
_post = chat_reads._post
_system_prompt = chat_reads._system_prompt

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake(monkeypatch):
    state = FakeGoogle(scopes=google.READ_SCOPES)
    monkeypatch.setenv(google.ENV_CLIENT_ID, google_fake.CLIENT_ID)
    monkeypatch.setenv(google.ENV_CLIENT_SECRET, google_fake.CLIENT_SECRET_CANARY)
    monkeypatch.setenv(google.ENV_REFRESH_TOKEN, google_fake.REFRESH_TOKEN_CANARY)
    monkeypatch.setattr(google, "_transport", state.transport())
    google.clear_token_cache()
    yield state
    google.clear_token_cache()


@pytest.fixture
def conversation(workspace, owner):
    return Conversation.objects.create(workspace=workspace, created_by=owner)


def test_todays_agenda_is_answered_through_the_local_model_only(
    fake, workspace, owner, enable, local_provider, calls, route, conversation, web_client
):
    enable(get_manifest("google"), workspace)
    route("calendar_agenda", {"days": 1})

    events = _post(web_client(owner), conversation, content="bugün neler var?")

    assert events[-1]["type"] == "done"
    by_purpose = {c["purpose"]: c["personal_data"] for c in calls}
    assert by_purpose == {"router.intent": False, "router.connector_args": True, "chat": True}
    chat_call = next(c for c in calls if c["purpose"] == "chat")
    system = _system_prompt(chat_call)
    assert '<connector_result source="google" tool="calendar_agenda">' in system
    assert "Ekip toplantısı" in system
    conversation.refresh_from_db()
    assert conversation.private is True and conversation.personal_data is True
    read = AuditLog.objects.get(action="connector.read")
    assert read.metadata["tool"] == "calendar_agenda" and read.metadata["outcome"] == "ok"
    reply = Message.objects.filter(conversation=conversation, role="assistant").last()
    assert reply.connector_reads[0]["data_class"] == "personal"

    # The next turn ("son gelen mail ne diyor?") reads the latest mail, and
    # every call -- router included -- already carries personal_data.
    route("mail_read", {})
    calls.clear()
    events = _post(web_client(owner), conversation, content="son gelen mail ne diyor?")
    assert events[-1]["type"] == "done"
    assert calls and all(c["personal_data"] for c in calls)
    assert "10:00" in _system_prompt(next(c for c in calls if c["purpose"] == "chat"))


def test_with_only_a_cloud_provider_nothing_is_read(
    fake, workspace, owner, enable, cloud_provider, calls, route, conversation, web_client
):
    enable(get_manifest("google"), workspace)
    route("mail_read", {})
    events = _post(web_client(owner), conversation, content="son gelen mail ne diyor?")
    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "connector_local_model_required"
    assert fake.api_requests() == []
    conversation.refresh_from_db()
    assert conversation.personal_data is False


def test_a_revoked_grant_between_beats_says_reconnect(
    fake, workspace, owner, enable, local_provider, calls, route, conversation, web_client
):
    enable(get_manifest("google"), workspace)
    fake.token_error = "invalid_grant"
    google.clear_token_cache()
    route("calendar_agenda", {})
    events = _post(web_client(owner), conversation, content="bugün neler var?")
    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "connector_reconnect_required"
