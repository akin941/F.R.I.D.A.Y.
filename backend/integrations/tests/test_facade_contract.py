"""Golden-file wire contract of the OpenAI-compatible facade (Phase 4
branch 6): chunk order, the usage chunk, `[DONE]`, a mid-stream error, the
non-streaming completion and the model list -- on the FakeAdapter only.

Volatile values are normalized before comparing: `id` -> "chatcmpl-<id>"
(its real shape is asserted separately), `created` -> 0, token counts ->
"<int>" (their arithmetic is asserted separately). Regenerate the files
after an intentional wire change with `UPDATE_GOLDEN=1 uv run pytest
integrations/tests/test_facade_contract.py` and review the diff."""

import json
import os
import re
import uuid
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from ai_core.adapters.base import AdapterError
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Message
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

GOLDEN_DIR = Path(__file__).parent / "golden"
COMPLETIONS = "/api/v1/openai/chat/completions"
_ID = re.compile(r"^chatcmpl-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture(autouse=True)
def setup_ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.FACADE_RATE = "1000/min"
    ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )
    for name, purpose in [("jarvis_system", "chat"), ("jarvis_router_intent", "router.intent")]:
        template = PromptTemplate.objects.create(name=name, purpose=purpose)
        version = PromptVersion.objects.create(template=template, version=1, content="x")
        template.active_version = version
        template.save(update_fields=["active_version"])


@pytest.fixture
def client():
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    raw = TOKEN_PREFIX + uuid.uuid4().hex
    PersonalAccessToken.objects.create(
        user=owner,
        workspace=Workspace.objects.get(owner=owner),
        name="open webui",
        kind=PersonalAccessToken.Kind.OPENAI,
        token_hash=hash_token(raw),
    )
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return api


def _normalize(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "id" and isinstance(item, str) and item.startswith("chatcmpl-"):
                assert _ID.match(item), item
                out[key] = "chatcmpl-<id>"
            elif key == "created" and isinstance(item, int):
                out[key] = 0
            elif key in {"prompt_tokens", "completion_tokens", "total_tokens"}:
                assert isinstance(item, int)
                out[key] = "<int>"
            else:
                out[key] = _normalize(item)
        if {"prompt_tokens", "completion_tokens", "total_tokens"} <= value.keys():
            assert value["total_tokens"] == value["prompt_tokens"] + value["completion_tokens"]
        return out
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _frames(response):
    """The raw SSE payloads: parsed JSON objects, and the final "[DONE]"."""
    assert response["Content-Type"] == "text/event-stream"
    body = b"".join(response.streaming_content).decode()
    assert body.endswith("data: [DONE]\n\n")
    frames = []
    for frame in body.split("\n\n"):
        if not frame:
            continue
        assert frame.startswith("data: "), frame
        payload = frame[len("data: ") :]
        frames.append(payload if payload == "[DONE]" else json.loads(payload))
    return frames


def _sse_lines(frames):
    return [_normalize(frame) for frame in frames]


def _check_golden(name, actual):
    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    assert actual == json.loads(path.read_text())


def _stream(client, **extra):
    return client.post(
        COMPLETIONS,
        {"model": "jarvis", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        | extra,
        format="json",
    )


def test_stream_contract(client):
    response = _stream(client)
    assert response.status_code == 200
    lines = _sse_lines(_frames(response))
    _check_golden("stream", lines)
    # One id for the whole stream, and it names the persisted reply.
    reply = Message.objects.get(role=Message.Role.ASSISTANT)
    assert reply.status == Message.Status.COMPLETE
    content = "".join(
        line["choices"][0]["delta"].get("content", "") for line in lines if line != "[DONE]"
    )
    assert content == reply.content


def test_stream_ids_are_the_reply_id(client):
    frames = _frames(_stream(client))
    ids = {frame["id"] for frame in frames[:-1]}
    reply = Message.objects.get(role=Message.Role.ASSISTANT)
    assert ids == {f"chatcmpl-{reply.id}"}


def test_stream_with_usage_contract(client):
    frames = _frames(_stream(client, stream_options={"include_usage": True}))
    _check_golden("stream_usage", _sse_lines(frames))
    usage_frame = frames[-2]
    assert usage_frame["choices"] == []
    chat = ModelUsage.objects.get(purpose="chat")
    assert usage_frame["usage"]["prompt_tokens"] == chat.input_tokens
    assert usage_frame["usage"]["completion_tokens"] == chat.output_tokens


def test_mid_stream_error_contract(client, monkeypatch):
    def failing_stream(self, messages, params, *, purpose=""):
        yield "Merhaba "
        raise AdapterError("provider_error", retryable=False)

    monkeypatch.setattr(FakeAdapter, "stream", failing_stream)
    response = _stream(client)
    assert response.status_code == 200
    _check_golden("stream_error", _sse_lines(_frames(response)))
    reply = Message.objects.get(role=Message.Role.ASSISTANT)
    assert reply.status == Message.Status.PARTIAL
    assert reply.content == "Merhaba "
    usage = ModelUsage.objects.get(purpose="chat")
    assert not usage.success
    assert usage.error_type == "provider_error"


def test_non_streaming_contract(client):
    response = client.post(
        COMPLETIONS,
        {"model": "jarvis", "messages": [{"role": "user", "content": "hi"}]},
        format="json",
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    _check_golden("completion", _normalize(response.json()))


def test_lite_stream_contract(client):
    response = client.post(
        COMPLETIONS,
        {
            "model": "jarvis-lite",
            "messages": [{"role": "user", "content": "title?"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        format="json",
    )
    _check_golden("stream_lite", _sse_lines(_frames(response)))
    assert not Message.objects.exists()


def test_models_contract(client):
    response = client.get("/api/v1/openai/models")
    _check_golden("models", response.json())
