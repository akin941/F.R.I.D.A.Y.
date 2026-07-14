"""Mockable Telegram Bot API HTTP wrapper (MVP 9 §4) -- mirrors
`ai_core.FakeAdapter`'s role for LLM calls: tests substitute a fake
transport (monkeypatch `_post`), never make a real outbound call. The real
Bot API token is a deploy-time secret that must never be required for the
test suite to pass, and CI must never make outbound calls to a third party.
"""

import json
import urllib.request

from django.conf import settings

# Telegram's own documented per-message character limit for sendMessage.
TELEGRAM_MESSAGE_LIMIT = 4096


def _api_url(method):
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


def _post(method, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(_api_url(method), data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def send_message(chat_id, text):
    """Splits `text` into sequential `sendMessage` calls (same `chat_id`)
    if it exceeds Telegram's 4096-character limit, rather than sending one
    oversized call Telegram would simply reject -- the one place this
    chunking logic lives, so every Telegram reply (chat, link
    confirmation, error messages) goes through it uniformly."""
    if not text:
        text = "(empty reply)"  # Telegram rejects a genuinely empty message
    for start in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[start : start + TELEGRAM_MESSAGE_LIMIT]
        _post("sendMessage", {"chat_id": chat_id, "text": chunk})


def set_webhook(webhook_url, secret_token):
    return _post("setWebhook", {"url": webhook_url, "secret_token": secret_token})
