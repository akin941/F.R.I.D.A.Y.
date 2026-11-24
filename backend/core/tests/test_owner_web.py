"""Phase 12 D3: the shared owner-over-the-web check, over every principal."""

from types import SimpleNamespace

import pytest

from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    CHANNEL_SYSTEM,
    CHANNEL_TELEGRAM,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
)
from core.owner_web import OwnerWebRequired, require_owner_web


def user(**overrides):
    values = {"pk": 1, "is_authenticated": True, "is_active": True, "is_owner": True}
    values.update(overrides)
    return SimpleNamespace(**values)


OWNER = user()
WORKSPACE = SimpleNamespace(owner_id=1)


def test_the_owner_over_the_web_passes():
    require_owner_web(actor=OWNER, workspace=WORKSPACE, channel=CHANNEL_WEB)


@pytest.mark.parametrize(
    "channel",
    [
        CHANNEL_CLI,
        CHANNEL_OPENAI,
        CHANNEL_HOOK,
        CHANNEL_NODE,
        CHANNEL_NODE_VOICE,
        CHANNEL_TELEGRAM,
        CHANNEL_SYSTEM,
        CHANNEL_UNKNOWN,
        "",
        None,
    ],
)
def test_every_other_channel_is_refused(channel):
    with pytest.raises(OwnerWebRequired) as excinfo:
        require_owner_web(actor=OWNER, workspace=WORKSPACE, channel=channel)
    assert excinfo.value.code == "channel_forbidden"


@pytest.mark.parametrize(
    "actor",
    [
        None,
        user(is_authenticated=False),
        user(is_agent=True),
        user(is_active=False),
        user(is_owner=False),
        user(pk=2),  # the owner flag, but not this workspace's owner
        SimpleNamespace(is_authenticated=True),  # attribute-less principal
    ],
    ids=["none", "anonymous", "agent", "inactive", "member", "other-owner", "bare"],
)
def test_every_non_owner_principal_is_refused(actor):
    with pytest.raises(OwnerWebRequired) as excinfo:
        require_owner_web(actor=actor, workspace=WORKSPACE, channel=CHANNEL_WEB)
    assert excinfo.value.code == "owner_required"


def test_a_missing_workspace_is_refused():
    with pytest.raises(OwnerWebRequired):
        require_owner_web(actor=OWNER, workspace=None, channel=CHANNEL_WEB)
    with pytest.raises(OwnerWebRequired):
        require_owner_web(actor=OWNER, workspace=SimpleNamespace(owner_id=None), channel="web")


def test_the_owner_web_check_is_a_permission_error():
    assert issubclass(OwnerWebRequired, PermissionError)
