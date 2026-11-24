"""Phase 11 branch 1 (brief D4, D5): the chat turn over the node voice
channel.

- `input_mode` is forced to `voice` (even when the body says `text`), so
  the ADR-015 cap applies (a gated tool named as a read degrades to chat);
- GET and POST reach only conversations opened via `node_voice`: another
  workspace's, a web-, CLI- or facade-opened one and a legacy row
  (`opened_via=""`) are 404 while the web session still reaches them;
- authenticator coexistence: a web session and a `cli` PAT keep working
  exactly as before, `openai` and `hook` tokens keep their 403;
- the serializers expose `channel: "node_voice"` and a read-only
  `opened_via`; `create_conversation(channel=...)` writes it once.
The whole node-action voice regression with the reference node is in
`tools/tests/test_node_action_node_voice.py`."""

from unittest import mock

import pytest

from accounts.models import PersonalAccessToken
from accounts.tests.node_voice_helpers import (
    CHAT_URL,
    bearer,
    make_owner,
    make_pat,
    mint_token,
    pair_node,
    sse_events,
)
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from assistant.services import create_conversation
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

KIND = PersonalAccessToken.Kind


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    return settings


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])


@pytest.fixture(autouse=True)
def chat_setup(db):
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", "Classify the message.")
    _prompt("friday_router_task_draft", "router.task_draft", "Extract the task.")


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def voice(owner):
    _, secret = pair_node(owner)
    return bearer(mint_token(secret))


@pytest.fixture
def live(owner, personal):
    return create_conversation(workspace=personal, actor=owner, channel="node_voice")


def post(client, conversation, content="Bugün ne var?", **body):
    return client.post(
        CHAT_URL.format(conversation.pk), {"content": content, **body}, format="json"
    )


def turn(client, conversation, content="Bugün ne var?", **body):
    response = post(client, conversation, content, **body)
    assert response.status_code == 200, response.content
    return sse_events(response)


# --- input_mode and the cap ----------------------------------------------------


@pytest.mark.parametrize("asserted", [None, "text", "voice"])
def test_input_mode_is_forced_to_voice(voice, live, asserted):
    body = {"input_mode": asserted} if asserted else {}
    events = turn(voice, live, **body)
    assert events[-1]["type"] == "done"
    user = Message.objects.get(conversation=live, role=Message.Role.USER)
    reply = Message.objects.get(conversation=live, role=Message.Role.ASSISTANT)
    assert user.channel == reply.channel == "node_voice"
    assert user.input_mode == Message.InputMode.VOICE
    usage = ModelUsage.objects.filter(purpose="chat").get()
    assert usage.context_report["input_mode"] == "voice"


def test_the_voice_cap_applies_on_the_channel(voice, live):
    with (
        mock.patch(
            "assistant.services.parse_intent",
            return_value={"intent": "connector_read", "tool_name": "vault_export"},
        ),
        mock.patch("assistant.views.prepare_connector_read") as prepare,
    ):
        events = turn(voice, live, "export", input_mode="text")
    prepare.assert_not_called()
    assert not [e for e in events if e["type"] == "draft"]
    assert Message.objects.get(role=Message.Role.USER).intent == Message.Intent.CHAT


def test_a_task_draft_is_a_draft_only(voice, live):
    with mock.patch(
        "assistant.services.parse_intent",
        return_value={"intent": "task_draft", "tool_name": None},
    ):
        events = turn(voice, live, "yarın annemi aramayı hatırlat")
    drafts = [e for e in events if e["type"] == "draft"]
    assert [d["kind"] for d in drafts] == ["task"]


# --- the opened_via rule (D4) -----------------------------------------------------


@pytest.mark.parametrize("opened_via", ["", "openai", "cli", "web"])
def test_a_personal_conversation_not_opened_live_is_404(
    owner, personal, voice, web_client, opened_via
):
    conversation = Conversation.objects.create(
        workspace=personal, created_by=owner, opened_via=opened_via
    )
    assert voice.get(CHAT_URL.format(conversation.pk)).status_code == 404
    assert post(voice, conversation).status_code == 404
    assert not Message.objects.exists()
    web = web_client(owner)
    assert web.get(CHAT_URL.format(conversation.pk)).status_code == 200
    assert post(web, conversation).status_code == 200


def test_another_workspaces_conversation_is_404(owner, voice):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    firm_live = create_conversation(workspace=firm, actor=owner, channel="node_voice")
    assert voice.get(CHAT_URL.format(firm_live.pk)).status_code == 404
    assert post(voice, firm_live).status_code == 404


def test_another_users_live_conversation_is_404(voice):
    from accounts.services import register_user

    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    theirs = create_conversation(
        workspace=Workspace.objects.get(owner=stranger), actor=stranger, channel="node_voice"
    )
    assert voice.get(CHAT_URL.format(theirs.pk)).status_code == 404
    assert post(voice, theirs).status_code == 404


def test_get_lists_a_live_conversation(voice, live):
    turn(voice, live)
    rows = voice.get(CHAT_URL.format(live.pk)).json()
    assert [row["channel"] for row in rows] == ["node_voice", "node_voice"]
    assert all(row["overlay_source_workspaces"] == {} for row in rows)


# --- authenticator coexistence (D3/D4) ---------------------------------------------


def test_a_web_session_and_a_cli_pat_still_reach_the_chat_turn(owner, personal, web_client):
    conversation = Conversation.objects.create(workspace=personal, created_by=owner)
    turn(web_client(owner), conversation, input_mode="voice")
    cli = bearer(make_pat(owner, personal, KIND.CLI)[0])
    assert cli.get(CHAT_URL.format(conversation.pk)).status_code == 200
    turn(cli, conversation, input_mode="voice")
    users = Message.objects.filter(role=Message.Role.USER).order_by("created_at")
    assert [(m.channel, m.input_mode) for m in users] == [("web", "voice"), ("cli", "text")]


@pytest.mark.parametrize("kind", [KIND.OPENAI, KIND.HOOK])
def test_openai_and_hook_tokens_are_still_refused(owner, personal, live, kind):
    client = bearer(make_pat(owner, personal, kind)[0])
    for response in (client.get(CHAT_URL.format(live.pk)), post(client, live)):
        assert response.status_code == 403
        assert response.json()["code"] == "token_kind_forbidden"


def test_only_the_chat_turn_swaps_the_authenticator():
    from accounts.authentication import (
        NodeVoiceTokenAuthentication,
        PersonalAccessTokenAuthentication,
    )
    from assistant import views

    assert views.ConversationMessageListView.authentication_classes[0] is (
        NodeVoiceTokenAuthentication
    )
    for name in ("ConversationListCreateView", "ConversationDetailView"):
        view = getattr(views, name)
        assert view.authentication_classes[0] is PersonalAccessTokenAuthentication


def test_a_voice_token_cannot_list_or_open_conversations(voice, live):
    assert voice.get("/api/v1/ai/conversations").status_code == 403
    assert voice.post("/api/v1/ai/conversations", {"title": "x"}, format="json").status_code == 403
    assert voice.get(f"/api/v1/ai/conversations/{live.pk}").status_code == 403


# --- opened_via --------------------------------------------------------------------


def test_create_conversation_writes_opened_via(owner, personal):
    assert create_conversation(workspace=personal, actor=owner).opened_via == ""
    facade = create_conversation(workspace=personal, actor=owner, channel="openai")
    assert facade.opened_via == "openai"


def test_opened_via_is_exposed_read_only(owner, personal, live, web_client):
    web = web_client(owner)
    url = f"/api/v1/ai/conversations/{live.pk}"
    assert web.get(url).json()["opened_via"] == "node_voice"
    response = web.patch(url, {"opened_via": "web", "title": "Yeni"}, format="json")
    assert response.status_code == 200
    live.refresh_from_db()
    assert live.opened_via == "node_voice" and live.title == "Yeni"
    listed = web.get("/api/v1/ai/conversations", {"workspace": str(personal.pk)}).json()
    assert {row["opened_via"] for row in listed} == {"node_voice"}
    created = web.post(
        "/api/v1/ai/conversations",
        {"workspace": str(personal.pk), "opened_via": "node_voice"},
        format="json",
    )
    assert created.status_code == 201 and created.json()["opened_via"] == ""
