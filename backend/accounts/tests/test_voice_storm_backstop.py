"""Phase 11 branch 5 (brief D12): the server's storm backstop.

- `NodeVoiceStormGuard`: the 9th transcription of a node device inside a
  sliding 60 s window (VOICE_LIVE_STORM_MAX_STT_PER_MINUTE = 8) engages
  `VoiceLiveMute` (audited) and answers 423 `voice.live_muted`; the window
  slides; the counter is per DEVICE, so a rotated token does not reset it;
- while muted, `NodeVoiceTokenAuthentication` answers 423 on every route
  the token reaches -- the three voice endpoints, live sessions and the
  chat turn on `assistant.ConversationMessageListView` -- while the policy
  read answers 200 with `storm_muted`; a `cli` PAT is never affected;
- release only on screen: `DELETE /api/v1/auth/nodes/{device_id}/voice-mute`
  by the owner's web session (audited); a node voice token, a PAT, the
  node secret or a non-owner member is refused; another user's device id
  is a 404 and its mute stays engaged; after release the next request
  succeeds.

The cache is LocMem in tests (Redis in production); the clock of the
window is `accounts.voice_live._now_seconds`, patched.
"""

import pytest
from django.core.cache import cache

from accounts import voice_live
from accounts.models import PersonalAccessToken, VoiceLiveMute
from accounts.tests.node_voice_helpers import (
    CHAT_URL,
    MUTE_URL,
    POLICY_URL,
    SESSIONS_URL,
    TRANSCRIPTIONS_URL,
    FakeRedis,
    bearer,
    make_owner,
    make_pat,
    mint_token,
    other_user,
    pair_node,
)
from assistant.services import create_conversation
from audit.models import AuditLog
from core import revocation
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

QUERY = {"model_sha256": "a" * 64, "threshold": "0.5000", "refractory_ms": 2000}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, settings):
    monkeypatch.setattr(revocation, "_client", lambda: FakeRedis())
    settings.VOICE_LIVE_STORM_MAX_STT_PER_MINUTE = 8
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def clock(monkeypatch):
    state = {"now": 1_800_000_000}
    monkeypatch.setattr(voice_live, "_now_seconds", lambda: state["now"])
    return state


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def node(owner):
    return pair_node(owner)


@pytest.fixture
def voice(node):
    return bearer(mint_token(node[1]))


def transcribe(client):
    # No conversation: the view answers 400/404 AFTER the guards ran, which
    # is all the counter needs.
    return client.post(TRANSCRIPTIONS_URL, {}, format="multipart")


def storm(client, count=9):
    return [transcribe(client).status_code for _ in range(count)]


def test_the_ninth_transcription_in_a_minute_mutes_the_device(clock, node, voice, personal):
    device, _ = node
    codes = storm(voice)
    assert 423 not in codes[:8]
    assert codes[8] == 423
    mute = VoiceLiveMute.objects.get(node_device=device)
    assert mute.released_at is None and mute.workspace == personal
    assert mute.reason == VoiceLiveMute.Reason.STT_STORM
    audit = AuditLog.objects.get(action="accounts.voice_live_mute.engaged")
    assert audit.resource_id == str(mute.pk)
    assert audit.metadata["node_device_id"] == str(device.pk)
    response = transcribe(voice)
    assert response.status_code == 423 and response.json()["code"] == "voice.live_muted"
    assert VoiceLiveMute.objects.count() == 1  # one engaged row, not one per request
    assert AuditLog.objects.filter(action="accounts.voice_live_mute.engaged").count() == 1


def test_the_window_slides(clock, voice):
    assert 423 not in storm(voice, 8)
    clock["now"] += 61
    assert 423 not in storm(voice, 8)
    clock["now"] += 30
    assert 423 not in storm(voice, 0)
    assert transcribe(voice).status_code == 423  # 8 inside the last 60 s + this one


def test_the_counter_is_per_device_so_a_rotated_token_does_not_reset_it(clock, node):
    _, secret = node
    first = bearer(mint_token(secret))
    assert 423 not in storm(first, 5)
    second = bearer(mint_token(secret))  # rotation: the first is in its grace
    assert 423 not in storm(second, 3)
    assert transcribe(second).status_code == 423


def test_another_device_has_its_own_counter(clock, owner, voice):
    assert 423 not in storm(voice, 8)
    _, other_secret = pair_node(owner, name="Mac mini")
    other = bearer(mint_token(other_secret))
    assert 423 not in storm(other, 8)


def test_the_web_channel_is_never_counted(clock, owner, web_client):
    client = web_client(owner)
    assert 423 not in storm(client, 20)
    assert not VoiceLiveMute.objects.exists()


def test_a_refused_wake_trigger_is_not_counted(clock, node):
    wake = bearer(mint_token(node[1]), trigger="wake")
    assert {code for code in storm(wake, 12)} == {403}
    assert not VoiceLiveMute.objects.exists()


@pytest.fixture
def muted(clock, voice, node):
    storm(voice)
    return VoiceLiveMute.objects.get(node_device=node[0])


def test_while_muted_every_node_voice_route_is_423(muted, owner, personal, voice):
    live = create_conversation(workspace=personal, actor=owner, channel="node_voice")
    responses = {
        "chat-get": voice.get(CHAT_URL.format(live.id)),
        "chat-post": voice.post(
            CHAT_URL.format(live.id), {"content": "merhaba", "input_mode": "voice"}, format="json"
        ),
        "stt": transcribe(voice),
        "tts": voice.post(
            "/api/v1/voice/speech", {"conversation": str(live.id), "text": "Merhaba."}
        ),
        "receipt": voice.get("/api/v1/voice/receipt", {"conversation": str(live.id)}),
        "sessions": voice.post(SESSIONS_URL, {}, format="json"),
    }
    for name, response in responses.items():
        assert response.status_code == 423, name
        assert response.json()["code"] == "voice.live_muted", name
    policy = voice.get(POLICY_URL, QUERY)
    assert policy.status_code == 200
    assert "storm_muted" in policy.json()["reasons"]


def test_a_cli_pat_is_never_affected_by_the_mute(muted, owner, personal):
    live = create_conversation(workspace=personal, actor=owner, channel="web")
    cli = bearer(make_pat(owner, personal, PersonalAccessToken.Kind.CLI)[0])
    assert cli.get(CHAT_URL.format(live.id)).status_code == 200


def test_the_owner_releases_the_mute_on_screen(muted, node, voice, owner, web_client):
    device, _ = node
    response = web_client(owner).delete(MUTE_URL.format(device.pk))
    assert response.status_code == 200
    assert response.json() == {"device_id": str(device.pk), "released": True}
    muted.refresh_from_db()
    assert muted.released_at is not None and muted.released_by == owner
    audit = AuditLog.objects.get(action="accounts.voice_live_mute.released")
    assert audit.actor == owner and audit.resource_id == str(muted.pk)
    # The window still holds the storm's requests; the next minute is clean.
    cache.clear()
    assert transcribe(voice).status_code != 423
    assert voice.get(POLICY_URL, QUERY).json()["reasons"] != ["storm_muted"]
    # Releasing again is a no-op, not an error.
    again = web_client(owner).delete(MUTE_URL.format(device.pk))
    assert again.status_code == 200 and again.json()["released"] is False


def test_a_new_storm_after_release_mutes_again(muted, node, voice, owner, web_client, clock):
    web_client(owner).delete(MUTE_URL.format(node[0].pk))
    clock["now"] += 120
    assert storm(voice)[-1] == 423
    assert VoiceLiveMute.objects.filter(node_device=node[0]).count() == 2


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        ("node-voice", 401),
        ("cli", 401),
        ("hook", 401),
        ("node-secret", 401),
        ("non-owner-web", 403),
        ("anon", 401),
    ],
)
def test_only_the_owners_web_session_releases(muted, node, owner, personal, web_client,
                                              principal, expected):  # fmt: skip
    device, secret = node
    if principal == "node-voice":
        client = bearer(mint_token(secret))
    elif principal in ("cli", "hook"):
        client = bearer(make_pat(owner, personal, principal)[0])
    elif principal == "node-secret":
        client = bearer(secret)
    elif principal == "non-owner-web":
        member, _ = other_user("member@test.dev")
        WorkspaceMember.objects.get_or_create(workspace=personal, user=member)
        client = web_client(member)
    else:
        client = bearer("")
        client.credentials()
    assert client.delete(MUTE_URL.format(device.pk)).status_code == expected
    muted.refresh_from_db()
    assert muted.released_at is None


def test_another_owners_device_id_is_a_404_and_its_mute_stays(clock, owner, web_client):
    intruder, device = other_user()
    mute = VoiceLiveMute.objects.create(
        workspace=Workspace.objects.get(owner=intruder, type="personal"), node_device=device
    )
    response = web_client(owner).delete(MUTE_URL.format(device.pk))
    assert response.status_code == 404
    mute.refresh_from_db()
    assert mute.released_at is None


def test_a_firm_members_request_never_releases(muted, node, owner, web_client):
    """A firm workspace's member (not the owner) cannot release the owner's
    mute: refused before any lookup, so the answer confirms nothing."""
    member, _ = other_user("firm@test.dev")
    firm = Workspace.objects.create(name="Firm", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=member)
    response = web_client(member).delete(MUTE_URL.format(node[0].pk))
    assert response.status_code in (403, 404)
    muted.refresh_from_db()
    assert muted.released_at is None


def test_a_browser_device_id_is_a_404(owner, web_client):
    from accounts.models import TrustedDevice

    browser = TrustedDevice.objects.create(
        user=owner, name="Browser", secret_hash="f" * 64, enrolled_via="pin"
    )
    assert web_client(owner).delete(MUTE_URL.format(browser.pk)).status_code == 404


def test_at_most_one_engaged_mute_per_device(muted, node):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        VoiceLiveMute.objects.create(workspace=muted.workspace, node_device=node[0])
