# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined
"""Phase 13b D5 (ADR-018 decision 2, branch 1b): the per-entity `direct`
flag. `HomeEntity.save()` refuses it outside the class on every write
path; the owner-web service sets it (audited `ha.entity_direct_changed`,
separately from `ha.entity_changed`) and clears it when the last eligible
service is unticked; a model's `"direct": true` in `ha_call_service`
arguments is a validation error, never a flag; and without a registered
executor a flagged TV still gets the Phase 13 draft card."""

import pytest
from django.core.exceptions import ValidationError

from approvals.models import ApprovalRequest
from assistant.models import Message
from audit.models import AuditLog
from connectors import home_direct, home_entities
from connectors.bridges import home_assistant as bridge
from connectors.models import HomeDirectCall, HomeEntity
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from connectors.tests.home_direct_helpers import (
    TV,
    bound_ha,  # noqa: F401
    call,
)
from connectors.tests.n8n_chat_fixtures import (  # noqa: F401 -- fixtures
    calls,
    chat_turn,
    conversation,
    n8n_chat,
    route,
)
from core.channels import CHANNEL_WEB
from tools import home_ports

pytestmark = pytest.mark.django_db

BASE = "/api/v1/connectors/home-assistant/entities"
APPROVALS = "/api/v1/approvals/requests"


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    return settings


def _create(owner, workspace, entity_id=TV, services=None, **kwargs):
    return home_entities.create_entity(
        actor=owner,
        workspace=workspace,
        channel=CHANNEL_WEB,
        entity_id=entity_id,
        label=kwargs.pop("label", "TV"),
        services=services or ["turn_on", "media_pause", "volume_set"],
        **kwargs,
    )


def _direct_audits():
    return list(AuditLog.objects.filter(action="ha.entity_direct_changed").order_by("created_at"))


# --- the model refusal (every write path) -------------------------------------------


@pytest.mark.parametrize(
    ("entity_id", "services"),
    [
        ("light.salon", ["turn_on"]),
        ("switch.priz", ["turn_on", "turn_off"]),
        ("scene.film_gecesi", ["turn_on"]),
        ("climate.salon", ["set_temperature"]),
        (TV, ["turn_on", "turn_off"]),
        (TV, ["select_source"]),
    ],
)
def test_the_model_refuses_direct_outside_the_class(workspace, entity_id, services):
    entity = HomeEntity(
        workspace=workspace, entity_id=entity_id, label="x", key="x", services=services, direct=True
    )
    with pytest.raises(ValidationError) as excinfo:
        entity.save()
    assert excinfo.value.code == home_direct.DIRECT_NOT_ALLOWED
    assert not HomeEntity.objects.exists()
    with pytest.raises(ValidationError):
        HomeEntity.objects.create(
            workspace=workspace,
            entity_id=entity_id,
            label="x",
            key="x",
            services=services,
            direct=True,
        )


@pytest.mark.parametrize("service", sorted(home_direct.DIRECT_MEDIA_SERVICES))
def test_one_eligible_service_is_enough(workspace, service):
    entity = HomeEntity.objects.create(
        workspace=workspace,
        entity_id=TV,
        label="x",
        key="x",
        services=["turn_on", service],
        direct=True,
    )
    assert entity.direct is True
    assert entity.direct_services == [service]


def test_the_flag_defaults_off(workspace, owner):
    assert allow(workspace, TV).direct is False
    assert _create(owner, workspace, "media_player.yatak").direct is False
    assert _direct_audits() == []


def test_the_eligibility_helper():
    assert home_direct.direct_services(TV, ["turn_on", "volume_up", "media_pause"]) == [
        "media_pause",
        "volume_up",
    ]
    assert home_direct.direct_services("light.salon", ["turn_on"]) == []
    assert home_direct.direct_services("speaker.media_pause", ["media_pause"]) == []
    assert home_direct.direct_services(TV, None) == []
    assert not home_direct.direct_eligible("not an id", ["media_pause"])


# --- the owner-web service ------------------------------------------------------------


def test_create_with_direct_is_audited_separately(owner, workspace):
    entity = _create(owner, workspace, direct=True)
    assert entity.direct is True
    (audit,) = _direct_audits()
    assert audit.metadata == {
        "home_entity_id": str(entity.pk),
        "entity_id": TV,
        "domain": "media_player",
        "direct": True,
        "direct_services": ["media_pause", "volume_set"],
    }
    allowed = AuditLog.objects.get(action="ha.entity_allowed")
    assert allowed.metadata["direct"] is True


def test_create_refuses_direct_outside_the_class(owner, workspace):
    with pytest.raises(home_entities.HomeEntityDirectNotAllowed) as excinfo:
        _create(owner, workspace, "light.salon", ["turn_on"], direct=True)
    assert excinfo.value.code == "ha_entity.direct_not_allowed"
    with pytest.raises(home_entities.HomeEntityDirectNotAllowed):
        _create(owner, workspace, services=["turn_on", "select_source"], direct=True)
    with pytest.raises(home_entities.HomeEntityInvalid):
        _create(owner, workspace, direct="true")
    assert not HomeEntity.objects.exists()
    assert _direct_audits() == []


def test_toggling_direct_is_its_own_audit(owner, workspace):
    entity = _create(owner, workspace)
    home_entities.update_entity(actor=owner, entity=entity, channel=CHANNEL_WEB, direct=True)
    home_entities.update_entity(actor=owner, entity=entity, channel=CHANNEL_WEB, direct=True)
    home_entities.update_entity(actor=owner, entity=entity, channel=CHANNEL_WEB, direct=False)
    assert [a.metadata["direct"] for a in _direct_audits()] == [True, False]
    # A pure flag change is not also an `ha.entity_changed`.
    assert not AuditLog.objects.filter(action="ha.entity_changed").exists()


def test_unticking_the_last_eligible_service_clears_direct(owner, workspace):
    entity = _create(owner, workspace, direct=True)
    entity = home_entities.update_entity(
        actor=owner, entity=entity, channel=CHANNEL_WEB, services=["turn_on", "volume_set"]
    )
    assert entity.direct is True  # volume_set is still eligible
    entity = home_entities.update_entity(
        actor=owner, entity=entity, channel=CHANNEL_WEB, services=["turn_on"]
    )
    assert entity.direct is False
    assert HomeEntity.objects.get().direct is False
    audits = _direct_audits()
    assert [a.metadata["direct"] for a in audits] == [True, False]
    assert audits[-1].metadata["direct_services"] == []
    changed = AuditLog.objects.filter(action="ha.entity_changed").order_by("created_at")
    assert sorted(changed.last().metadata["fields"]) == ["direct", "services"]


def test_direct_and_a_narrowing_in_one_write_is_refused(owner, workspace):
    entity = _create(owner, workspace)
    with pytest.raises(home_entities.HomeEntityDirectNotAllowed):
        home_entities.update_entity(
            actor=owner, entity=entity, channel=CHANNEL_WEB, services=["turn_on"], direct=True
        )
    entity.refresh_from_db()
    assert (entity.direct, entity.services) == (False, ["media_pause", "turn_on", "volume_set"])


@pytest.mark.parametrize("channel", ["cli", "openai", "hook", "node", "node_voice", "system"])
def test_the_service_refuses_any_channel_but_web(owner, workspace, channel):
    entity = _create(owner, workspace)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        home_entities.update_entity(actor=owner, entity=entity, channel=channel, direct=True)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        home_entities.create_entity(
            actor=owner,
            workspace=workspace,
            channel=channel,
            entity_id="media_player.yatak",
            label="Yatak",
            services=["media_pause"],
            direct=True,
        )
    assert HomeEntity.objects.get().direct is False


def test_an_agent_principal_cannot_set_the_flag(owner, workspace):
    from accounts.models import User

    entity = _create(owner, workspace)
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        home_entities.update_entity(actor=agent, entity=entity, channel=CHANNEL_WEB, direct=True)
    assert HomeEntity.objects.get().direct is False


# --- the API's strict field ------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", 1, "1", None, "yes"])
def test_the_api_flag_is_a_strict_boolean(owner, workspace, web_client, value):
    client = web_client(owner)
    entity = _create(owner, workspace)
    response = client.patch(f"{BASE}/{entity.pk}", {"direct": value}, format="json")
    assert response.status_code == 400
    created = client.post(
        BASE,
        {
            "workspace": str(workspace.pk),
            "entity_id": "media_player.yatak",
            "label": "Yatak",
            "services": ["media_pause"],
            "direct": value,
        },
        format="json",
    )
    assert created.status_code == 400
    assert HomeEntity.objects.get().direct is False


def test_the_api_sets_and_shows_the_flag(owner, workspace, web_client):
    client = web_client(owner)
    entity = _create(owner, workspace)
    response = client.patch(f"{BASE}/{entity.pk}", {"direct": True}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["direct"] is True
    assert response.json()["direct_services"] == ["media_pause", "volume_set"]
    light = client.post(
        BASE,
        {
            "workspace": str(workspace.pk),
            "entity_id": "light.salon",
            "label": "Tavan",
            "services": ["turn_on"],
            "direct": True,
        },
        format="json",
    )
    assert light.status_code == 400 and light.json()["code"] == "ha_entity.direct_not_allowed"
    listed = client.get(BASE, {"workspace": str(workspace.pk)}).json()["results"]
    assert [(row["entity_id"], row["direct"]) for row in listed] == [(TV, True)]


# --- a model's "direct": true is never a flag ------------------------------------------


def test_a_model_draft_carrying_direct_is_a_serializer_refusal():
    arguments = {**call("media_pause"), "direct": True}
    serializer = bridge.CallServiceArguments(data=arguments)
    assert not serializer.is_valid()
    nested = {**call("media_pause"), "data": {"direct": True}}
    assert not bridge.CallServiceArguments(data=nested).is_valid()


def test_a_request_carrying_direct_is_refused_and_flags_nothing(
    bound_ha,
    ha,
    owner,
    workspace,
    web_client,
):
    entity = allow(workspace, TV)
    response = web_client(owner).post(
        APPROVALS,
        {
            "workspace": str(workspace.pk),
            "tool_name": "ha_call_service",
            "payload": {"arguments": {**call("media_pause"), "direct": True}},
        },
        format="json",
    )
    assert response.status_code == 400
    assert not ApprovalRequest.objects.exists()
    entity.refresh_from_db()
    assert entity.direct is False
    assert ha.posts == []


# --- the ports at boot; without an executor no turn is direct ------------------------------


def test_the_boot_registers_the_classifier_and_the_executor():
    assert home_ports.direct_classifier() is home_direct.classify
    assert home_ports.direct_executor() is home_direct.execute


@pytest.fixture
def no_executor():
    executor = home_ports.direct_executor()
    home_ports.unregister_direct_executor()
    yield
    if executor is not None:
        home_ports.register_direct_executor(executor)


@pytest.mark.parametrize("input_mode", ["voice", "text"])
def test_without_an_executor_a_flagged_tv_still_gets_the_card(
    no_executor,
    bound_ha,
    ha,
    owner,
    workspace,
    web_client,
    conversation,
    n8n_chat,
    calls,
    input_mode,
):
    """The branch 1b guarantee kept as a port property: with no executor
    registered, a would-be direct call is the Phase 13 card with the
    resolver's arguments, nothing is sent and no model is asked."""
    entity = allow(workspace, TV, label="TV")
    entity.direct = True
    entity.save()
    pause = call("media_pause")
    body = {"input_mode": "voice"} if input_mode == "voice" else {}
    events = chat_turn(web_client(owner), conversation, "TV'yi duraklat", **body)
    drafts = [e for e in events if e["type"] == "draft"]
    assert drafts and drafts[0]["payload"] == {"arguments": pause}
    assert drafts[0]["direct_reason"] == "not_available"
    assert calls == []
    user = Message.objects.get(conversation=conversation, role="user")
    assert user.intent == "tool_request"
    assert ha.requests == []
    assert not HomeDirectCall.objects.exists()
    assert not ApprovalRequest.objects.exists()
