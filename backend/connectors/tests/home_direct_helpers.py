"""Shared set-up for the Phase 13b direct-media tests (branch 1b): a bound
Home Assistant (the fake behind the real bridge, pin check run through the
real service), the owner's salon TV flagged direct, and the arguments the
deterministic matcher would produce."""

import pytest

from connectors import home_direct
from connectors.manifest import get_manifest
from connectors.models import HomeDirectCall
from connectors.tests.fake_home_assistant import allow, salon_tv_state
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB

TV = "media_player.salon_tv"
MEDIA_SERVICES = sorted(home_direct.DIRECT_MEDIA_SERVICES)
ALL_TV_SERVICES = [
    "turn_on",
    "turn_off",
    "select_source",
    *MEDIA_SERVICES,
]

DATA = {
    "volume_set": {"volume_level": 0.4},
    "volume_mute": {"is_volume_muted": True},
}

# origin -> (channel, input_mode) the classifier admits it with.
ORIGINS = {
    "chat": (CHANNEL_WEB, "text"),
    "voice": (CHANNEL_WEB, "voice"),
    "node": (CHANNEL_NODE_VOICE, "voice"),
}


def call(service, entity_id=TV, domain="media_player", data=None):
    return {
        "domain": domain,
        "service": service,
        "entity_id": entity_id,
        "data": dict(DATA.get(service, {}) if data is None else data),
    }


def flagged(workspace, entity_id=TV, services=None, **kwargs):
    entity = allow(
        workspace, entity_id, ALL_TV_SERVICES if services is None else services, **kwargs
    )
    entity.direct = True
    entity.save()
    return entity


def classify(owner, workspace, arguments=None, origin="chat", **overrides):
    channel, input_mode = ORIGINS.get(origin, (CHANNEL_WEB, "text"))
    kwargs = {
        "workspace": workspace,
        "actor": owner,
        "arguments": call("media_pause") if arguments is None else arguments,
        "origin": origin,
        "channel": channel,
        "input_mode": input_mode,
    }
    kwargs.update(overrides)
    return home_direct.classify(**kwargs)


def make_call(workspace, owner, **fields):
    """The tests' factory: branch 1b has no `HomeDirectCall` writer (the
    executor arrives in branch 1c)."""
    values = {
        "entity_id": TV,
        "service": "media_pause",
        "origin": HomeDirectCall.Origin.CHAT,
        "requested_by": owner,
        "status": HomeDirectCall.Status.SUCCEEDED,
    }
    values.update(fields)
    return HomeDirectCall.objects.create(workspace=workspace, **values)


@pytest.fixture
def bound_ha(ha, enable, workspace):
    ha.states[TV] = salon_tv_state()
    binding = enable(get_manifest("home_assistant"), workspace)
    ha.requests.clear()
    return binding


@pytest.fixture
def direct_tv(bound_ha, workspace):
    return flagged(workspace, label="TV")
