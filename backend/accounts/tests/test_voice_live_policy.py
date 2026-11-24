"""Phase 11 branch 5 (brief D11): the always-on arming policy and the
false-accept gate, server side.

- the verdict on the LATEST row per `(model_sha256, kind)`: every reason
  code, the FAR / FRR budget boundaries, `hours < 24`, the setting off, a
  later failing row closing a gate an earlier passing one opened (and a
  later passing one reopening it), threshold and refractory mismatches;
- `GET /api/v1/auth/nodes/voice-policy` with the real verdict (all three
  query parameters required) and its role matrix;
- `manage.py voice_wake_eval record|list`: append-only, audited, refuses
  impossible numbers;
- `GET /api/v1/auth/nodes/voice-live` (web, owner): the panel's data, never
  another user's devices, mutes or rows; role matrix;
- ADR-001: rows are read through `for_actor()` in the owner's personal
  workspace only.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts import voice_live
from accounts.models import PersonalAccessToken, VoiceLiveMute, VoiceWakeEvaluation
from accounts.tests.node_voice_helpers import (
    POLICY_URL,
    VOICE_LIVE_URL,
    FakeRedis,
    bearer,
    make_owner,
    make_pat,
    mint_token,
    other_user,
    pair_node,
)
from audit.models import AuditLog
from core import revocation
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

SHA = "a" * 64
OTHER_SHA = "b" * 64
KIND = VoiceWakeEvaluation.Kind


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(revocation, "_client", lambda: FakeRedis())


@pytest.fixture
def enabled(settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    settings.VOICE_LIVE_MAX_FAR_PER_HOUR = "0.2"
    settings.VOICE_LIVE_MAX_FRR = "0.10"
    return settings


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


_tick = iter(range(1, 1_000_000))


def household(owner, *, sha=SHA, hours="24", accepts=4, threshold="0.5", refractory=2000):
    return voice_live.record_evaluation(
        owner=owner,
        model_sha256=sha,
        kind=KIND.HOUSEHOLD,
        threshold=threshold,
        refractory_ms=refractory,
        hours=hours,
        false_accepts=accepts,
        recorded_by=owner,
    )


def positives(owner, *, sha=SHA, count=50, rejects=5, threshold="0.5", refractory=2000):
    return voice_live.record_evaluation(
        owner=owner,
        model_sha256=sha,
        kind=KIND.POSITIVES,
        threshold=threshold,
        refractory_ms=refractory,
        positives=count,
        false_rejects=rejects,
        recorded_by=owner,
    )


def verdict(owner, *, sha=SHA, threshold="0.5", refractory=2000, device_id=None):
    workspace_id = voice_live.personal_workspace_id(owner)
    return voice_live.policy_verdict(
        owner, workspace_id, sha, Decimal(threshold), refractory, device_id=device_id
    )


def passing(owner, **kwargs):
    household(owner, **kwargs)
    positives(owner, **kwargs)


# --- the verdict ------------------------------------------------------------


def test_a_passing_pair_with_the_setting_on_is_permitted(owner, enabled):
    passing(owner)
    result = verdict(owner)
    assert result.permitted is True and result.reasons == []
    assert result.household_id and result.positives_id


def test_the_setting_off_refuses_even_a_passing_pair(owner, settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = False
    passing(owner)
    result = verdict(owner)
    assert result.permitted is False and result.reasons == ["disabled"]


def test_no_rows_at_all(owner, enabled):
    assert verdict(owner).reasons == ["no_household_eval", "no_positive_eval"]


@pytest.mark.parametrize(
    ("accepts", "hours", "passes"),
    [(4, "20", True), (5, "25", True), (21, "100", False), (1, "4.99", False)],
)
def test_far_at_the_budget_boundary(owner, enabled, accepts, hours, passes):
    # 4 / 20 = 0.2 passes; 5 / 25 = 0.2 passes; 21 / 100 = 0.21 fails
    # (hours < 24 rows are filtered first: use >= 24 h where it matters).
    hours_value = Decimal(hours)
    if hours_value < 24:
        hours_value *= 5
        accepts *= 5
    household(owner, hours=str(hours_value), accepts=accepts)
    positives(owner)
    assert verdict(owner).permitted is passes
    if not passes:
        assert verdict(owner).reasons == ["far_above_budget"]


def test_far_exactly_0_2_passes_and_0_21_fails(owner, enabled):
    household(owner, hours="100", accepts=20)  # 0.2
    positives(owner)
    assert verdict(owner).permitted is True
    household(owner, hours="100", accepts=21)  # 0.21, newer
    assert verdict(owner).reasons == ["far_above_budget"]


def test_less_than_24_hours_of_household_audio_is_no_evaluation(owner, enabled):
    household(owner, hours="23.999", accepts=0)
    positives(owner)
    assert verdict(owner).reasons == ["no_household_eval"]


def test_frr_at_the_budget_boundary(owner, enabled):
    household(owner)
    positives(owner, count=100, rejects=10)  # 0.10 passes
    assert verdict(owner).permitted is True
    positives(owner, count=100, rejects=11)  # 0.11 fails
    assert verdict(owner).reasons == ["frr_above_budget"]


def test_a_later_failing_household_row_closes_the_gate_and_a_later_pass_reopens(owner, enabled):
    passing(owner)
    assert verdict(owner).permitted is True
    failing = household(owner, hours="24", accepts=24)  # 1.0 / h
    result = verdict(owner)
    assert result.reasons == ["far_above_budget"] and result.household_id == str(failing.pk)
    household(owner, hours="48", accepts=2)
    assert verdict(owner).permitted is True


def test_a_later_failing_positives_row_closes_the_gate(owner, enabled):
    passing(owner)
    positives(owner, count=50, rejects=25)
    assert verdict(owner).reasons == ["frr_above_budget"]
    positives(owner, count=50, rejects=0)
    assert verdict(owner).permitted is True


def test_the_latest_row_wins_on_recorded_at_then_id(owner, enabled, personal):
    passing(owner)
    # An older-dated failing row recorded afterwards does not supersede.
    VoiceWakeEvaluation.objects.create(
        workspace=personal,
        model_sha256=SHA,
        kind=KIND.HOUSEHOLD,
        hours=Decimal("24"),
        false_accepts=100,
        far_per_hour=Decimal("4.1667"),
        threshold=Decimal("0.5"),
        refractory_ms=2000,
        recorded_at=timezone.now() - timedelta(days=3),
    )
    assert verdict(owner).permitted is True


def test_another_models_rows_are_a_model_mismatch(owner, enabled):
    passing(owner, sha=OTHER_SHA)
    result = verdict(owner, sha=SHA)
    assert result.reasons == ["model_mismatch"]
    assert verdict(owner, sha=OTHER_SHA).permitted is True


@pytest.mark.parametrize("requested", ["0.4", "0.6", "0.5001"])
def test_a_request_at_another_threshold_is_a_threshold_mismatch(owner, enabled, requested):
    passing(owner)
    assert verdict(owner, threshold=requested).reasons == ["threshold_mismatch"]


def test_the_two_latest_rows_at_different_thresholds_mismatch(owner, enabled):
    household(owner, threshold="0.5")
    positives(owner, threshold="0.6")
    assert verdict(owner, threshold="0.5").reasons == ["threshold_mismatch"]
    assert verdict(owner, threshold="0.6").reasons == ["threshold_mismatch"]


def test_a_refractory_mismatch(owner, enabled):
    passing(owner)
    assert verdict(owner, refractory=1500).reasons == ["threshold_mismatch"]
    household(owner, refractory=1500)
    assert verdict(owner, refractory=2000).reasons == ["threshold_mismatch"]


def test_a_storm_muted_device_reads_storm_muted(owner, enabled, node, personal):
    passing(owner)
    device, _ = node
    VoiceLiveMute.objects.create(workspace=personal, node_device=device)
    assert verdict(owner, device_id=device.pk).reasons == ["storm_muted"]


def test_the_negative_set_kind_is_recorded_but_not_judged(owner, enabled):
    voice_live.record_evaluation(
        owner=owner,
        model_sha256=SHA,
        kind=KIND.NEGATIVE_SET,
        threshold="0.5",
        refractory_ms=2000,
        hours="10",
        false_accepts=50,
    )
    passing(owner)
    assert verdict(owner).permitted is True


def test_any_model_permitted_needs_the_setting_and_a_passing_pair(owner, settings):
    workspace_id = voice_live.personal_workspace_id(owner)
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    assert voice_live.any_model_permitted(owner, workspace_id) is False
    household(owner, sha=OTHER_SHA)
    assert voice_live.any_model_permitted(owner, workspace_id) is False
    passing(owner)
    assert voice_live.any_model_permitted(owner, workspace_id) is True
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = False
    assert voice_live.any_model_permitted(owner, workspace_id) is False


def test_the_verdict_never_reads_another_users_rows(owner, enabled):
    intruder, _ = other_user()
    passing(intruder)
    assert verdict(owner).reasons == ["no_household_eval", "no_positive_eval"]
    # Even a crafted call with the other workspace id finds nothing: the
    # rows are read through `for_actor()`.
    other_ws = voice_live.personal_workspace_id(intruder)
    result = voice_live.policy_verdict(owner, other_ws, SHA, Decimal("0.5"), 2000)
    assert result.reasons == ["no_household_eval", "no_positive_eval"]


# --- append-only ---------------------------------------------------------------


def test_rows_are_append_only_and_audited(owner):
    row = household(owner)
    audit = AuditLog.objects.get(action="accounts.voice_wake_eval.recorded")
    assert audit.resource_id == str(row.pk)
    assert audit.metadata["model_sha256"] == SHA and audit.metadata["far_per_hour"] == "0.1667"
    row.hours = Decimal("100")
    with pytest.raises(TypeError):
        row.save()
    with pytest.raises(TypeError):
        row.delete()
    with pytest.raises(TypeError):
        VoiceWakeEvaluation.objects.all().update(hours=1)
    with pytest.raises(TypeError):
        VoiceWakeEvaluation.objects.all().delete()


# --- the command ------------------------------------------------------------------


def _record(*args):
    call_command("voice_wake_eval", "record", "--model-sha256", SHA, *args)


def test_the_command_records_household_and_positives(owner, enabled):
    _record("--kind", "household", "--threshold", "0.5", "--refractory-ms", "2000",
            "--hours", "24", "--false-accepts", "4", "--far-per-hour", "0.1667")  # fmt: skip
    _record("--kind", "positives", "--threshold", "0.5", "--refractory-ms", "2000",
            "--positives", "60", "--false-rejects", "3")  # fmt: skip
    assert VoiceWakeEvaluation.objects.count() == 2
    row = VoiceWakeEvaluation.objects.get(kind=KIND.POSITIVES)
    assert row.frr == Decimal("0.0500") and row.recorded_by == owner
    assert verdict(owner).permitted is True
    assert AuditLog.objects.filter(action="accounts.voice_wake_eval.recorded").count() == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--kind", "household", "--threshold", "0.5", "--refractory-ms", "2000",
         "--hours", "-1", "--false-accepts", "0"],
        ["--kind", "household", "--threshold", "0.5", "--refractory-ms", "2000",
         "--hours", "24", "--false-accepts", "-3"],
        ["--kind", "household", "--threshold", "0.5", "--refractory-ms", "2000",
         "--hours", "24", "--false-accepts", "4", "--far-per-hour", "0.1"],
        ["--kind", "household", "--threshold", "0.5", "--refractory-ms", "2000",
         "--hours", "0", "--false-accepts", "0"],
        ["--kind", "household", "--threshold", "0.5", "--refractory-ms", "2000",
         "--hours", "24", "--positives", "3"],
        ["--kind", "positives", "--threshold", "0.5", "--refractory-ms", "2000",
         "--positives", "10", "--false-rejects", "11"],
        ["--kind", "positives", "--threshold", "0.5", "--refractory-ms", "2000",
         "--positives", "10", "--false-rejects", "1", "--frr", "0.5"],
        ["--kind", "positives", "--threshold", "0.5", "--refractory-ms", "2000",
         "--positives", "0"],
        ["--kind", "positives", "--threshold", "1.5", "--refractory-ms", "2000",
         "--positives", "10"],
        ["--kind", "positives", "--threshold", "0.12345", "--refractory-ms", "2000",
         "--positives", "10"],
        ["--kind", "positives", "--threshold", "nan", "--refractory-ms", "2000",
         "--positives", "10"],
        ["--kind", "positives", "--threshold", "0.5", "--refractory-ms", "-1",
         "--positives", "10"],
    ],
)  # fmt: skip
def test_the_command_refuses_impossible_numbers(owner, args):
    with pytest.raises(CommandError):
        _record(*args)
    assert VoiceWakeEvaluation.objects.count() == 0
    assert not AuditLog.objects.filter(action="accounts.voice_wake_eval.recorded").exists()


def test_the_command_refuses_a_bad_model_hash(owner):
    with pytest.raises(CommandError):
        call_command(
            "voice_wake_eval", "record", "--model-sha256", "XYZ", "--kind", "positives",
            "--threshold", "0.5", "--refractory-ms", "2000", "--positives", "1",
        )  # fmt: skip
    assert VoiceWakeEvaluation.objects.count() == 0


def test_the_command_needs_an_owner(db):
    with pytest.raises(CommandError):
        call_command("voice_wake_eval", "list")


def test_the_command_lists_the_latest_rows(owner, enabled, capsys):
    passing(owner)
    call_command("voice_wake_eval", "list")
    out = capsys.readouterr().out
    assert SHA in out and "permitted" in out


# --- the policy endpoint -----------------------------------------------------------


QUERY = {"model_sha256": SHA, "threshold": "0.5000", "refractory_ms": 2000}


def test_the_policy_endpoint_carries_the_real_verdict(owner, enabled, voice):
    response = voice.get(POLICY_URL, QUERY)
    assert response.json()["reasons"] == ["no_household_eval", "no_positive_eval"]
    passing(owner)
    body = voice.get(POLICY_URL, QUERY).json()
    assert body["always_on_permitted"] is True and body["reasons"] == []
    assert body["household_eval_id"] and body["positives_eval_id"]
    body = voice.get(POLICY_URL, QUERY | {"threshold": "0.4"}).json()
    assert body == {**body, "always_on_permitted": False, "reasons": ["threshold_mismatch"]}


@pytest.mark.parametrize("missing", ["model_sha256", "threshold", "refractory_ms"])
def test_a_missing_policy_parameter_is_a_400(voice, missing):
    query = {key: value for key, value in QUERY.items() if key != missing}
    assert voice.get(POLICY_URL, query).status_code == 400


def test_the_policy_endpoint_answers_storm_muted_instead_of_423(owner, enabled, node, voice):
    passing(owner)
    device, _ = node
    VoiceLiveMute.objects.create(
        workspace=Workspace.objects.get(owner=owner, type="personal"), node_device=device
    )
    response = voice.get(POLICY_URL, QUERY)
    assert response.status_code == 200
    assert response.json()["reasons"] == ["storm_muted"]


# --- the voice-live panel ------------------------------------------------------------


def test_voice_live_lists_the_owners_nodes_mutes_and_models(owner, enabled, node, web_client):
    device, _ = node
    passing(owner)
    household(owner, sha=OTHER_SHA, accepts=100)
    personal = Workspace.objects.get(owner=owner, type="personal")
    VoiceLiveMute.objects.create(workspace=personal, node_device=device)
    response = web_client(owner).get(VOICE_LIVE_URL)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["always_on_enabled"] is True and body["any_model_permitted"] is True
    assert body["max_far_per_hour"] == "0.2" and body["max_frr"] == "0.10"
    (entry,) = body["nodes"]
    assert entry["device_id"] == str(device.pk) and entry["mute"]["reason"] == "stt_storm"
    by_sha = {m["model_sha256"]: m for m in body["models"]}
    assert by_sha[SHA]["permitted"] is True and by_sha[SHA]["threshold"] == "0.5000"
    assert by_sha[SHA]["household"]["far_per_hour"] == "0.1667"
    assert by_sha[OTHER_SHA]["reasons"] == ["far_above_budget", "no_positive_eval"]


def test_voice_live_never_shows_another_users_data(owner, enabled, web_client):
    intruder, device = other_user()
    passing(intruder)
    VoiceLiveMute.objects.create(
        workspace=Workspace.objects.get(owner=intruder, type="personal"), node_device=device
    )
    body = web_client(owner).get(VOICE_LIVE_URL).json()
    assert body["nodes"] == [] and body["models"] == []
    assert body["any_model_permitted"] is False


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        ("owner-web", 200),
        ("non-owner-web", 403),
        ("cli", 401),
        ("node-voice", 401),
        ("node-secret", 401),
        ("anon", 401),
    ],
)
def test_voice_live_role_matrix(owner, personal, node, web_client, principal, expected):
    if principal == "owner-web":
        client = web_client(owner)
    elif principal == "non-owner-web":
        member, _ = other_user("member@test.dev")
        WorkspaceMember.objects.get_or_create(workspace=personal, user=member)
        client = web_client(member)
    elif principal == "cli":
        client = bearer(make_pat(owner, personal, PersonalAccessToken.Kind.CLI)[0])
    elif principal == "node-voice":
        client = bearer(mint_token(node[1]))
    elif principal == "node-secret":
        client = bearer(node[1])
    else:
        client = bearer("")
        client.credentials()
    assert client.get(VOICE_LIVE_URL).status_code == expected


def test_the_verdict_function_is_the_one_the_view_uses(owner, enabled, voice, monkeypatch):
    calls = []
    original = voice_live.judge

    def spy(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(voice_live, "judge", spy)
    voice.get(POLICY_URL, QUERY)
    assert calls == [SHA]
