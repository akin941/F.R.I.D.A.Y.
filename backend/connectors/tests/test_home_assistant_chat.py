"""An `ha_states` read through chat (brief D5/D8): personal-class data routes
only to personal-data providers, marks the conversation private and
`personal_data` before the consuming completion, and never happens without
an allowed provider. The real chat view, router plumbing and bridge; HA is
an `httpx.MockTransport`."""

import json

import pytest

from assistant.models import Conversation
from audit.models import AuditLog
from connectors.manifest import get_manifest
from connectors.tests.fake_home_assistant import ha  # noqa: F401
from connectors.tests.test_chat_reads import (  # noqa: F401
    _post,
    ai,
    calls,
    cloud_provider,
    local_provider,
    route,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    return settings


@pytest.fixture
def conversation(workspace, owner):
    return Conversation.objects.create(workspace=workspace, created_by=owner)


def test_a_states_read_is_personal_data_routed(
    ha,  # noqa: F811
    enable,
    workspace,
    owner,
    local_provider,  # noqa: F811
    calls,  # noqa: F811
    route,  # noqa: F811
    conversation,
    web_client,
):
    enable(get_manifest("home_assistant"), workspace)
    route("ha_states", {"entity_id": "light.salon"})

    events = _post(web_client(owner), conversation, content="salonun ışığı açık mı?")

    assert events[-1]["type"] == "done"
    assert {c["purpose"]: c["personal_data"] for c in calls} == {
        "router.intent": False,
        "router.connector_args": True,
        "chat": True,
    }
    chat = next(c for c in calls if c["purpose"] == "chat")
    prompt = "\n".join(m["content"] for m in json.loads(chat["messages"]))
    assert '<connector_result source="home_assistant"' in prompt
    assert "light.salon" in chat["messages"]
    assert "PICTURE-TOKEN" not in chat["messages"]
    conversation.refresh_from_db()
    assert (conversation.private, conversation.personal_data) == (True, True)
    assert AuditLog.objects.get(action="connector.read").metadata["outcome"] == "ok"


def test_no_personal_data_provider_means_no_read(
    ha,  # noqa: F811
    enable,
    workspace,
    owner,
    cloud_provider,  # noqa: F811
    calls,  # noqa: F811
    route,  # noqa: F811
    conversation,
    web_client,
):
    enable(get_manifest("home_assistant"), workspace)
    requests_after_enable = len(ha.requests)
    route("ha_states", {})

    events = _post(web_client(owner), conversation)

    assert events == [{"type": "error", "error_type": "connector_local_model_required"}]
    assert len(ha.requests) == requests_after_enable
    conversation.refresh_from_db()
    assert (conversation.private, conversation.personal_data) == (False, False)
