from unittest import mock

import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from assistant.models import Conversation
from integrations.models import LinkedTelegramChat, TelegramLinkCode
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def webhook_secret(settings):
    settings.TELEGRAM_WEBHOOK_SECRET = "test-secret"
    settings.TELEGRAM_BOT_TOKEN = "test-token"
    return settings


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


# --- link-code mint ---


def test_mint_link_code_returns_code_and_expiry(owner, workspace):
    response = auth_client(owner).post(
        "/api/v1/integrations/telegram/link-code", {"workspace": str(workspace.id)}, format="json"
    )
    assert response.status_code == 201
    assert response.data["code"]
    assert response.data["expires_at"]
    assert TelegramLinkCode.objects.filter(user=owner, workspace=workspace).exists()


def test_mint_link_code_in_inaccessible_workspace_is_404(outsider, workspace):
    response = auth_client(outsider).post(
        "/api/v1/integrations/telegram/link-code", {"workspace": str(workspace.id)}, format="json"
    )
    assert response.status_code == 404


def test_mint_link_code_requires_web_auth_not_pat(owner, workspace):
    """PersonalAccessTokenAuthentication is never allowlisted on this view
    -- minting a link code is itself a sensitive, human-decided action,
    same discipline as minting a PersonalAccessToken (accounts/views.py)."""
    from accounts.authentication import hash_token
    from accounts.models import PersonalAccessToken

    raw = "jpat_test-raw-token-0000000000000000"
    PersonalAccessToken.objects.create(
        user=owner, workspace=workspace, name="test", token_hash=hash_token(raw)
    )
    response = pat_client(raw).post(
        "/api/v1/integrations/telegram/link-code", {"workspace": str(workspace.id)}, format="json"
    )
    assert response.status_code == 401


# --- link-status / unlink ---


def test_link_status_reflects_unlinked_then_linked(owner, workspace):
    response = auth_client(owner).get(
        f"/api/v1/integrations/telegram/link-status?workspace={workspace.id}"
    )
    assert response.status_code == 200
    assert response.data == {"linked": False, "chat_id": None, "linked_at": None}

    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id="12345", conversation=conversation
    )
    response = auth_client(owner).get(
        f"/api/v1/integrations/telegram/link-status?workspace={workspace.id}"
    )
    assert response.status_code == 200
    assert response.data["linked"] is True
    assert response.data["chat_id"] == "12345"
    assert response.data["linked_at"] is not None


def test_unlink_removes_the_row_but_keeps_conversation_and_messages(owner, workspace):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id="12345", conversation=conversation
    )

    response = auth_client(owner).delete(
        f"/api/v1/integrations/telegram/link?workspace={workspace.id}"
    )
    assert response.status_code == 204
    assert not LinkedTelegramChat.objects.filter(user=owner, workspace=workspace).exists()
    assert Conversation.objects.filter(id=conversation.id).exists()


def test_relinking_after_unlink_creates_a_fresh_row(owner, workspace):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id="12345", conversation=conversation
    )
    auth_client(owner).delete(f"/api/v1/integrations/telegram/link?workspace={workspace.id}")

    new_conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id="67890", conversation=new_conversation
    )
    link = LinkedTelegramChat.objects.get(user=owner, workspace=workspace)
    assert link.chat_id == "67890"


def test_link_status_requires_workspace_param(owner):
    response = auth_client(owner).get("/api/v1/integrations/telegram/link-status")
    assert response.status_code == 400


def test_unlink_out_of_scope_workspace_is_404(outsider, workspace):
    response = auth_client(outsider).delete(
        f"/api/v1/integrations/telegram/link?workspace={workspace.id}"
    )
    assert response.status_code == 404


# --- webhook security ---


def test_webhook_missing_secret_header_is_403_and_does_nothing():
    with mock.patch("integrations.views.process_telegram_update.delay") as delay:
        response = APIClient().post(
            "/api/v1/integrations/telegram/webhook", {"update_id": 1}, format="json"
        )
    assert response.status_code == 403
    delay.assert_not_called()


def test_webhook_wrong_secret_header_is_403_and_does_nothing():
    with mock.patch("integrations.views.process_telegram_update.delay") as delay:
        response = APIClient().post(
            "/api/v1/integrations/telegram/webhook",
            {"update_id": 1},
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong",
        )
    assert response.status_code == 403
    delay.assert_not_called()


def test_webhook_unconfigured_secret_never_matches_empty_header(settings):
    settings.TELEGRAM_WEBHOOK_SECRET = ""
    with mock.patch("integrations.views.process_telegram_update.delay") as delay:
        response = APIClient().post(
            "/api/v1/integrations/telegram/webhook",
            {"update_id": 1},
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="",
        )
    assert response.status_code == 403
    delay.assert_not_called()


def test_webhook_correct_secret_enqueues_and_returns_200_without_running_relay_inline():
    payload = {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": "hi"}}
    with mock.patch("integrations.views.process_telegram_update.delay") as delay:
        response = APIClient().post(
            "/api/v1/integrations/telegram/webhook",
            payload,
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="test-secret",
        )
    assert response.status_code == 200
    delay.assert_called_once_with(payload)
