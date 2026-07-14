"""integrations.telegram_client unit tests -- no real network call. `_post`
is mocked directly (mirrors ai_core.FakeAdapter's role for LLM calls); CI
must never make an outbound call to Telegram's real Bot API."""

from unittest import mock

from integrations import telegram_client


def test_send_message_under_the_limit_is_a_single_call():
    with mock.patch.object(telegram_client, "_post") as post:
        telegram_client.send_message("chat-1", "hello")

    post.assert_called_once_with("sendMessage", {"chat_id": "chat-1", "text": "hello"})


def test_send_message_splits_text_exceeding_the_limit():
    text = "x" * (telegram_client.TELEGRAM_MESSAGE_LIMIT + 10)
    with mock.patch.object(telegram_client, "_post") as post:
        telegram_client.send_message("chat-1", text)

    assert post.call_count == 2
    first_call, second_call = post.call_args_list
    assert len(first_call.args[1]["text"]) == telegram_client.TELEGRAM_MESSAGE_LIMIT
    assert len(second_call.args[1]["text"]) == 10
    assert first_call.args[1]["chat_id"] == "chat-1"
    assert second_call.args[1]["chat_id"] == "chat-1"


def test_send_message_never_sends_a_genuinely_empty_message():
    with mock.patch.object(telegram_client, "_post") as post:
        telegram_client.send_message("chat-1", "")

    post.assert_called_once()
    assert post.call_args.args[1]["text"]  # non-empty


def test_set_webhook_posts_url_and_secret():
    with mock.patch.object(telegram_client, "_post", return_value={"ok": True}) as post:
        result = telegram_client.set_webhook("https://x/webhook", "s3cr3t")

    post.assert_called_once_with(
        "setWebhook", {"url": "https://x/webhook", "secret_token": "s3cr3t"}
    )
    assert result == {"ok": True}


def test_post_calls_urlopen_with_a_finite_timeout(settings):
    """Proves the round-4 P2 fix: urlopen's own default timeout is
    unbounded, which would otherwise let a stalled Telegram API call hang
    the calling Celery worker indefinitely."""
    settings.TELEGRAM_BOT_TOKEN = "test-token"
    fake_response = mock.MagicMock()
    fake_response.read.return_value = b'{"ok": true}'
    fake_response.__enter__.return_value = fake_response
    with mock.patch.object(
        telegram_client.urllib.request, "urlopen", return_value=fake_response
    ) as urlopen:
        result = telegram_client._post("sendMessage", {"chat_id": "1", "text": "hi"})

    assert result == {"ok": True}
    assert urlopen.call_args.kwargs["timeout"] == telegram_client._REQUEST_TIMEOUT_SECONDS
