"""Phase 11 branch 1 (brief D3): the node voice credential.

- the mint (`POST /api/v1/auth/nodes/voice-token`): node secret only, every
  other principal refused, revoked node / kill switch refused, the raw
  token returned once and never audited or logged, one current token per
  device with the 30 s rotation grace (manual clock), the throttle;
- the generic mint refuses `node_voice` (400, never a row, never a 500);
  list and revoke on `/auth/tokens` handle device-bound tokens;
- authentication: expiry, revoke, device revoke / kill switch /
  `revoke_all_devices` after the mint, a device revoked behind the token's
  back, an inactive owner -> refused on the next request; a voice token on
  any other PAT surface is 403 `token_kind_forbidden`;
- `accepted_kinds` defaults keep the existing authenticators unchanged;
- the voice-policy read (closed by default; the gate itself is
  test_voice_live_policy.py);
- the check constraint (the boot-time TTL cap is in
  config/tests/test_settings.py).

No Redis is touched: `core.revocation._client` is a recorder.
"""

import json
import logging
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.utils import timezone
from rest_framework.throttling import ScopedRateThrottle

from accounts import devices
from accounts.authentication import (
    FacadeTokenAuthentication,
    HookTokenAuthentication,
    NodeVoicePolicyAuthentication,
    NodeVoiceTokenAuthentication,
    PersonalAccessTokenAuthentication,
    hash_token,
)
from accounts.models import NodeKillSwitch, PersonalAccessToken, TrustedDevice
from accounts.tests.node_voice_helpers import (
    MINT_URL,
    POLICY_URL,
    FakeRedis,
    bearer,
    make_owner,
    make_pat,
    mint,
    mint_token,
    pair_node,
)
from audit.models import AuditLog
from core import revocation
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

KIND = PersonalAccessToken.Kind


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(revocation, "_client", lambda: fake)
    return fake


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def node(owner):
    return pair_node(owner)


POLICY_QUERY = {"model_sha256": "a" * 64, "threshold": "0.5", "refractory_ms": 2000}


def policy(token):
    return bearer(token).get(POLICY_URL, POLICY_QUERY)


# --- the mint ---------------------------------------------------------------


def test_the_node_secret_mints_a_device_bound_short_lived_voice_token(owner, personal, node):
    device, secret = node
    before = timezone.now()
    response = mint(secret)
    assert response.status_code == 201, response.content
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {"token", "token_id", "expires_at", "previous_valid_until"}
    assert body["token"].startswith("jpat_") and body["previous_valid_until"] is None
    token = PersonalAccessToken.objects.get(pk=body["token_id"])
    assert token.kind == KIND.NODE_VOICE
    assert token.node_device == device and token.user == owner
    assert token.workspace == personal
    assert token.token_hash == hash_token(body["token"])
    ttl = (token.expires_at - before).total_seconds()
    assert 899 <= ttl <= 905
    # The child's first call works with it.
    assert policy(body["token"]).status_code == 200


def test_the_raw_token_is_never_audited_or_logged(node, caplog):
    _, secret = node
    caplog.set_level(logging.DEBUG)
    raw = mint_token(secret)
    assert policy(raw).status_code == 200
    row = AuditLog.objects.get(action="node_voice_token.minted")
    token = PersonalAccessToken.objects.get(kind=KIND.NODE_VOICE)
    assert row.metadata["device_id"] == str(token.node_device_id)
    assert row.metadata["token_id"] == str(token.pk)
    for audit in AuditLog.objects.all():
        assert raw not in json.dumps(audit.metadata)
        assert raw[5:] not in json.dumps(audit.metadata)
    assert raw not in caplog.text


@pytest.mark.parametrize("principal", ["web", "cli", "openai", "hook", "voice", "anonymous"])
def test_only_the_node_secret_can_mint(owner, personal, node, web_client, principal):
    _, secret = node
    voice = mint_token(secret)
    before = PersonalAccessToken.objects.count()
    if principal == "web":
        client = web_client(owner)
    elif principal == "voice":
        client = bearer(voice)
    elif principal == "anonymous":
        client = bearer("")
        client.credentials()
    else:
        client = bearer(make_pat(owner, personal, principal)[0])
    response = client.post(MINT_URL)
    assert response.status_code in (401, 403), (principal, response.status_code)
    assert PersonalAccessToken.objects.count() == before + (
        1 if principal in ("cli", "openai", "hook") else 0
    )
    assert PersonalAccessToken.objects.filter(kind=KIND.NODE_VOICE).count() == 1


def test_a_revoked_node_cannot_mint(owner, node):
    device, secret = node
    devices.revoke_device(device=device, actor=owner)
    assert mint(secret).status_code == 401
    assert not PersonalAccessToken.objects.filter(kind=KIND.NODE_VOICE).exists()


def test_an_engaged_kill_switch_refuses_the_mint(owner, node):
    _, secret = node
    devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    assert mint(secret).status_code == 401
    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    assert mint(secret).status_code == 201


def test_the_mint_is_throttled(node, monkeypatch):
    _, secret = node
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {**ScopedRateThrottle.THROTTLE_RATES, "node_voice_mint": "2/hour"},
    )
    assert mint(secret).status_code == 201
    assert mint(secret).status_code == 201
    assert mint(secret).status_code == 429


def test_the_throttle_scopes_are_configured(settings):
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["node_voice_mint"] == "30/hour"
    assert rates["node_voice_session"] == "60/hour"


# --- rotation: one current token, the 30 s grace -----------------------------


def test_a_second_mint_shortens_the_first_to_the_rotation_grace(node, frozen_now):
    _, secret = node
    t0 = frozen_now(timezone.now().replace(microsecond=0))
    first = mint_token(secret)
    t1 = frozen_now(t0 + timedelta(seconds=600))
    response = mint(secret)
    assert response.status_code == 201
    second = response.json()["token"]
    grace_until = t1 + timedelta(seconds=30)
    assert response.json()["previous_valid_until"] == grace_until.isoformat().replace("+00:00", "Z")
    old = PersonalAccessToken.objects.get(token_hash=hash_token(first))
    assert old.expires_at == grace_until and old.revoked_at is None
    frozen_now(t1 + timedelta(seconds=29))
    assert policy(first).status_code == 200
    assert policy(second).status_code == 200
    frozen_now(t1 + timedelta(seconds=31))
    assert policy(first).status_code == 401
    assert policy(second).status_code == 200


def test_the_grace_never_extends_an_older_expiry(node, frozen_now, settings):
    _, secret = node
    t0 = frozen_now(timezone.now().replace(microsecond=0))
    first = mint_token(secret)
    # The first token's expiry is already inside the grace window: untouched.
    frozen_now(t0 + timedelta(seconds=890))
    mint_token(secret)
    old = PersonalAccessToken.objects.get(token_hash=hash_token(first))
    assert old.expires_at == t0 + timedelta(seconds=900)


def test_each_device_has_its_own_current_token(owner, node):
    _, secret = node
    _, other_secret = pair_node(owner, name="Mac mini")
    first = mint_token(secret)
    mint_token(other_secret)
    old = PersonalAccessToken.objects.get(token_hash=hash_token(first))
    assert old.expires_at > timezone.now() + timedelta(seconds=800)


# --- the generic mint and the token list -------------------------------------


def test_the_generic_mint_refuses_node_voice_with_a_400(owner, personal, web_client):
    response = web_client(owner).post(
        "/api/v1/auth/tokens",
        {"workspace": str(personal.pk), "name": "x", "kind": "node_voice"},
        format="json",
    )
    assert response.status_code == 400
    # DRF's `invalid_choice` (the project's error envelope flattens the
    # code to `invalid`): a field error, never a device-less row or a 500.
    assert "is not a valid choice" in response.json()["kind"][0]
    assert not PersonalAccessToken.objects.exists()


@pytest.mark.parametrize("kind", ["cli", "openai", "hook", "node_voice"])
def test_the_generic_mint_refuses_low_trust_callers_without_a_500(owner, personal, node, kind):
    _, secret = node
    raw = mint_token(secret) if kind == "node_voice" else make_pat(owner, personal, kind)[0]
    response = bearer(raw).post(
        "/api/v1/auth/tokens",
        {"workspace": str(personal.pk), "name": "x", "kind": "node_voice"},
        format="json",
    )
    assert response.status_code in (401, 403)


def test_the_token_list_shows_voice_tokens_with_their_device(owner, node, web_client):
    device, secret = node
    mint_token(secret)
    rows = web_client(owner).get("/api/v1/auth/tokens").json()
    (row,) = [row for row in rows if row["kind"] == "node_voice"]
    assert row["node_device"] == str(device.pk)
    assert "token" not in row and "token_hash" not in row


def test_revoking_a_voice_token_on_the_web_is_immediate(owner, node, web_client):
    _, secret = node
    raw = mint_token(secret)
    token = PersonalAccessToken.objects.get(kind=KIND.NODE_VOICE)
    response = web_client(owner).post(f"/api/v1/auth/tokens/{token.pk}/revoke")
    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
    assert AuditLog.objects.filter(action="accounts.pat.revoked").exists()
    assert policy(raw).status_code == 401


def test_the_dev_pat_command_cannot_mint_a_voice_token(owner):
    with pytest.raises(CommandError):
        call_command("create_dev_pat", owner.email, "x", kind="node_voice")


# --- authentication ----------------------------------------------------------


def test_an_expired_voice_token_is_refused(node, frozen_now):
    _, secret = node
    t0 = frozen_now(timezone.now())
    raw = mint_token(secret)
    frozen_now(t0 + timedelta(seconds=901))
    assert policy(raw).status_code == 401


def test_revoking_the_node_after_the_mint_kills_the_token(owner, node, fake_redis):
    device, secret = node
    raw = mint_token(secret)
    devices.revoke_device(device=device, actor=owner)
    assert PersonalAccessToken.objects.get(kind=KIND.NODE_VOICE).revoked_at is not None
    assert policy(raw).status_code == 401


def test_a_device_revoked_behind_the_tokens_back_is_rechecked_per_request(node):
    device, secret = node
    raw = mint_token(secret)
    # The row itself is not revoked: only the per-request device check
    # stands between the token and the endpoint.
    TrustedDevice.objects.filter(pk=device.pk).update(revoked_at=timezone.now())
    assert PersonalAccessToken.objects.get(kind=KIND.NODE_VOICE).revoked_at is None
    assert policy(raw).status_code == 401
    assert AuditLog.objects.filter(
        action="accounts.pat.auth_failed", metadata__reason="node_revoked"
    ).exists()


def test_the_kill_switch_after_the_mint_kills_the_token(owner, node):
    _, secret = node
    raw = mint_token(secret)
    devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    assert PersonalAccessToken.objects.get(kind=KIND.NODE_VOICE).revoked_at is not None
    assert policy(raw).status_code == 401
    # Releasing the switch does not resurrect it: the host mints a new one.
    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    assert policy(raw).status_code == 401
    assert policy(mint_token(secret)).status_code == 200


def test_the_kill_switch_alone_refuses_an_unrevoked_token(owner, node):
    _, secret = node
    raw = mint_token(secret)
    NodeKillSwitch.objects.create(user=owner, engaged_at=timezone.now())
    assert policy(raw).status_code == 401


def test_revoke_all_devices_kills_the_token(owner, node):
    _, secret = node
    raw = mint_token(secret)
    devices.revoke_all_devices(user=owner)
    assert PersonalAccessToken.objects.get(kind=KIND.NODE_VOICE).revoked_at is not None
    assert policy(raw).status_code == 401


def test_an_inactive_owner_is_refused(owner, node):
    _, secret = node
    raw = mint_token(secret)
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    assert policy(raw).status_code == 401


def test_revoking_a_node_leaves_other_tokens_alone(owner, personal, node):
    device, secret = node
    cli_raw, cli = make_pat(owner, personal, KIND.CLI)
    _, other_secret = pair_node(owner, name="Mac mini")
    other = mint_token(other_secret)
    mint_token(secret)
    devices.revoke_device(device=device, actor=owner)
    cli.refresh_from_db()
    assert cli.revoked_at is None
    assert policy(other).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/ai/conversations",
        "/api/v1/approvals/requests",
        "/api/v1/agents",
    ],
)
def test_a_voice_token_on_another_pat_surface_is_token_kind_forbidden(node, url):
    _, secret = node
    response = bearer(mint_token(secret)).get(url)
    assert response.status_code == 403, (url, response.status_code)
    assert response.json()["code"] == "token_kind_forbidden"


# --- accepted_kinds ------------------------------------------------------------


def test_accepted_kinds_default_to_the_single_kind():
    assert PersonalAccessTokenAuthentication().get_accepted_kinds() == {KIND.CLI}
    assert FacadeTokenAuthentication().get_accepted_kinds() == {KIND.OPENAI}
    assert HookTokenAuthentication().get_accepted_kinds() == {KIND.HOOK}
    assert NodeVoiceTokenAuthentication().get_accepted_kinds() == {KIND.CLI, KIND.NODE_VOICE}
    assert NodeVoicePolicyAuthentication().get_accepted_kinds() == {KIND.NODE_VOICE}


# --- the voice-policy read -------------------------------------------------------


def test_the_policy_is_closed_by_default(node):
    _, secret = node
    response = bearer(mint_token(secret)).get(POLICY_URL, POLICY_QUERY)
    assert response.status_code == 200
    body = response.json()
    assert body["always_on_permitted"] is False
    assert body["reasons"] == ["disabled", "no_household_eval", "no_positive_eval"]
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [{"model_sha256": "xyz"}, {"threshold": "1.5"}, {"refractory_ms": "-1"}],
)
def test_a_malformed_policy_query_is_a_400(node, query):
    _, secret = node
    assert bearer(mint_token(secret)).get(POLICY_URL, POLICY_QUERY | query).status_code == 400


@pytest.mark.parametrize(
    ("principal", "expected"),
    [("cli", 403), ("openai", 403), ("hook", 403), ("node", 401), ("web", 403), ("anon", 401)],
)
def test_only_the_voice_token_reads_the_policy(
    owner, personal, node, web_client, principal, expected
):
    _, secret = node
    if principal == "web":
        client = web_client(owner)
    elif principal == "node":
        client = bearer(secret)
    elif principal == "anon":
        client = bearer("")
        client.credentials()
    else:
        client = bearer(make_pat(owner, personal, principal)[0])
    response = client.get(POLICY_URL)
    assert response.status_code == expected
    if principal in ("cli", "openai", "hook"):
        assert response.json()["code"] == "token_kind_forbidden"


# --- model invariants ------------------------------------------------------------


def test_a_voice_token_needs_its_device(owner, personal, node):
    device, _ = node
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonalAccessToken.objects.create(
            workspace=personal, user=owner, name="x", kind=KIND.NODE_VOICE, token_hash="a" * 64
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonalAccessToken.objects.create(
            workspace=personal,
            user=owner,
            name="x",
            kind=KIND.CLI,
            token_hash="b" * 64,
            node_device=device,
        )


def test_a_node_with_voice_tokens_cannot_be_deleted_alone(node):
    device, secret = node
    mint_token(secret)
    with pytest.raises(RestrictedError), transaction.atomic():
        device.delete()
    assert PersonalAccessToken.objects.filter(kind=KIND.NODE_VOICE).count() == 1
