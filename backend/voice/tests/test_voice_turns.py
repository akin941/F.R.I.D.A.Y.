"""`Message.input_mode` (Phase 6 D9) and the ADR-015 capability cap on
`input_mode="voice"` turns (A6): a voice turn reads and proposes only --
nothing it produces executes without an on-screen act. Lives in
`voice/tests` because it drives whole turns into `tasks`/`approvals`/
`tools`, which `assistant`'s own tests may not import (contracts)."""

import dataclasses
import json
from unittest import mock

import pytest

from accounts.models import PersonalAccessToken
from approvals.models import ApprovalRequest
from assistant.models import Message
from assistant.services import (
    INTENT_EFFECTS,
    VOICE_ALLOWED_EFFECTS,
    apply_voice_capability_cap,
)
from tasks.models import Task
from tools import registry
from voice.tests.conftest import pat_client

pytestmark = pytest.mark.django_db


def _post(client, conversation, content, **body):
    response = client.post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": content, **body},
        format="json",
    )
    assert response.status_code == 200, response.content
    events = []
    for chunk in b"".join(response.streaming_content).split(b"\n\n"):
        if chunk.startswith(b"data: "):
            events.append(json.loads(chunk[len(b"data: ") :]))
    return events


# --- input_mode ---


def test_web_voice_turn_stamps_the_user_message_only(owner, conversation, chat_setup, web_client):
    client = web_client(owner)
    _post(client, conversation, "Merhaba", input_mode="voice")
    user = Message.objects.get(role=Message.Role.USER)
    reply = Message.objects.get(role=Message.Role.ASSISTANT)
    assert user.input_mode == Message.InputMode.VOICE
    assert reply.input_mode == Message.InputMode.TEXT
    listed = client.get(f"/api/v1/ai/conversations/{conversation.id}/messages").json()
    assert [m["input_mode"] for m in listed] == ["voice", "text"]


def test_input_mode_defaults_to_text(owner, conversation, chat_setup, web_client):
    _post(web_client(owner), conversation, "Merhaba")
    assert Message.objects.get(role=Message.Role.USER).input_mode == Message.InputMode.TEXT


def test_input_mode_is_forced_text_for_a_pat_turn(owner, workspace, conversation, chat_setup):
    client = pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
    _post(client, conversation, "Merhaba", input_mode="voice")
    assert Message.objects.get(role=Message.Role.USER).input_mode == Message.InputMode.TEXT


def test_unknown_input_mode_is_400(owner, conversation, chat_setup, web_client):
    response = web_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "x", "input_mode": "telepathy"},
        format="json",
    )
    assert response.status_code == 400


# --- the cap, as a unit ---


def test_every_router_intent_has_a_declared_effect():
    """A new intent must decide what it does on a voice turn when it is
    added; one that executes is absent from VOICE_ALLOWED_EFFECTS."""
    assert set(INTENT_EFFECTS) == set(Message.Intent.values)
    assert VOICE_ALLOWED_EFFECTS == {"read", "proposal"}


def test_an_executing_or_unknown_intent_degrades_to_chat_for_voice_only(monkeypatch):
    monkeypatch.setitem(INTENT_EFFECTS, "auto_execute", "execute")
    classification = {"intent": "auto_execute", "intent_payload": {"x": 1}}
    capped = apply_voice_capability_cap(classification, input_mode="voice")
    assert capped == {"intent": "chat", "intent_payload": {}}
    assert apply_voice_capability_cap({"intent": "never_heard"}, input_mode="voice")["intent"] == (
        "chat"
    )
    # A typed turn is untouched: the cap narrows voice, adds no trust to text.
    assert apply_voice_capability_cap(classification, input_mode="text") is classification


def test_a_connector_read_must_name_a_read_tool_on_a_voice_turn():
    gated = {"intent": "connector_read", "intent_payload": {"tool_name": "vault_export"}}
    assert apply_voice_capability_cap(gated, input_mode="voice")["intent"] == "chat"


def test_proposals_pass_the_cap():
    draft = {"intent": "task_draft", "intent_payload": {"title": "x", "due_date": None}}
    assert apply_voice_capability_cap(draft, input_mode="voice") is draft


# --- end to end: a voice turn never reaches an execute path ---


def test_voice_task_draft_turn_yields_only_a_draft(owner, conversation, chat_setup, web_client):
    events = _post(web_client(owner), conversation, "remind me to call mom", input_mode="voice")
    drafts = [e for e in events if e["type"] == "draft"]
    assert len(drafts) == 1 and drafts[0]["kind"] == "task"
    assert events[-1]["type"] == "done"
    assert not Task.objects.exists()


@pytest.fixture
def instrumented_registry(monkeypatch):
    """Every registered gated ToolSpec with its execute hooks replaced by
    recorders -- a voice turn must never call them."""
    calls = []
    for name, spec in list(registry._REGISTRY.items()):

        def create_execution(*args, _name=name, **kwargs):
            calls.append(("create_execution", _name))

        def dispatch(*args, _name=name, **kwargs):
            calls.append(("dispatch", _name))

        monkeypatch.setitem(
            registry._REGISTRY,
            name,
            dataclasses.replace(spec, create_execution=create_execution, dispatch=dispatch),
        )
    return calls


def test_registry_scan_no_proposable_tool_executes_from_a_voice_turn(
    owner, conversation, chat_setup, web_client, instrumented_registry
):
    names = registry.list_tool_names()
    assert names, "the registry scan needs at least one proposable tool"
    client = web_client(owner)
    for name in names:
        with mock.patch(
            "assistant.services.parse_intent",
            return_value={"intent": "tool_request", "tool_name": name},
        ):
            events = _post(client, conversation, f"run {name}", input_mode="voice")
        drafts = [e for e in events if e["type"] == "draft"]
        if name == "node_action":
            # Phase 10 D13: a node action draft needs an argument step over
            # the owner's paired nodes; with none there is nothing to
            # propose and the turn is plain chat. The node action voice
            # regressions are tools/tests/test_node_action_trust.py.
            assert drafts == [], name
            continue
        if registry.get_tool(name).draft_arguments is not None:
            # Phase 12 D12: a personal tool's argument step needs a
            # personal-data provider; this setup has none, so the turn is
            # plain chat (the drafted path: connectors/tests/test_n8n_writes.py).
            assert drafts == [], name
            continue
        assert [d["kind"] for d in drafts] == ["tool"], name
        assert drafts[0]["tool_name"] == name
    assert instrumented_registry == []
    assert not ApprovalRequest.objects.exists()


def test_voice_turn_naming_a_gated_tool_as_a_read_is_plain_chat(
    owner, conversation, chat_setup, web_client, instrumented_registry
):
    with (
        mock.patch(
            "assistant.services.parse_intent",
            return_value={"intent": "connector_read", "tool_name": "vault_export"},
        ),
        mock.patch("assistant.views.prepare_connector_read") as prepare,
    ):
        events = _post(web_client(owner), conversation, "export", input_mode="voice")
    prepare.assert_not_called()
    assert not [e for e in events if e["type"] == "draft"]
    assert events[-1]["type"] == "done"
    assert Message.objects.get(role=Message.Role.USER).intent == Message.Intent.CHAT
    assert instrumented_registry == []
