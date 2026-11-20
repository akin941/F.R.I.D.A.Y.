"""Phase 8A branch 3: the Claude Code hook-event ingest (brief D6) and the
`CodingSession` read model + `GET /memory/sessions` (D12).

No real Claude Code, Keychain or network: events are posted as
`friday-hook` would post them, with real `hook` PATs.
"""

import json
import secrets
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from core.channels import CHANNEL_WEB
from core.throttling import ScopedRateThrottle
from memory.ingestion import hooks
from memory.ingestion import services as ingestion_services
from memory.ingestion.tasks import purge_expired_episodes_task
from memory.models import CodingSession, Episode, IngestionCounter, WorkspaceMappingRule
from memory.tests.ingestion_helpers import PASSWORD, add_rule
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

INGEST = "/api/v1/memory/ingest/hook-events"
SESSIONS = "/api/v1/memory/sessions"
PATH = WorkspaceMappingRule.RuleType.PATH_PREFIX
SID = "0b9f6a3e-2c7d-4f51-9a8e-1d2c3b4a5f60"
FAKE_KEY = "sk-ant-api03-" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0LmNbVcXz" * 2


def _token(user, workspace, kind=PersonalAccessToken.Kind.HOOK, **extra):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = PersonalAccessToken.objects.create(
        workspace=workspace, user=user, name=kind, kind=kind, token_hash=hash_token(raw), **extra
    )
    return raw, token


def _bearer(raw):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


@pytest.fixture
def hook_client(owner, personal):
    raw, _ = _token(owner, personal)
    return _bearer(raw)


@pytest.fixture
def mapped(hooks, personal, firm):
    """The owner's code tree -> personal; the firm's tree -> firm."""
    add_rule(hooks, PATH, "/Users/o/code", personal)
    add_rule(hooks, PATH, "/Users/o/code/acme", firm)
    return hooks


def _event(name, *, cwd="/Users/o/code/friday", session_id=SID, **extra):
    return {"hook_event_name": name, "session_id": session_id, "cwd": cwd, **extra}


def _post(client, *events):
    return client.post(INGEST, {"events": list(events)}, format="json")


def _all_text_in_db():
    """Every text/json column of every memory_* and audit table, as one
    string (the 'secrets never reach the DB' scan)."""
    chunks = []
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names():
            if not table.startswith(("memory_", "audit_", "accounts_")):
                continue
            cursor.execute(f'SELECT * FROM "{table}"')  # noqa: S608 -- table names from introspection
            for row in cursor.fetchall():
                chunks.append(json.dumps(row, default=str))
    return "\n".join(chunks)


# --- authentication: only a hook token -------------------------------------


@pytest.mark.parametrize("kind", ["cli", "openai"])
def test_other_pat_kinds_are_refused(owner, personal, mapped, kind):
    raw, _ = _token(owner, personal, kind=kind)
    response = _post(_bearer(raw), _event("SessionStart"))
    assert response.status_code == 403
    assert response.json()["code"] == "token_kind_forbidden"
    assert not Episode.all_objects.exists()


def test_web_session_is_refused(owner, mapped, web_client):
    response = _post(web_client(owner), _event("SessionStart"))
    # The view's only authenticator is the hook PAT: a web session (or the
    # forced principal here) never satisfies IsHookChannel.
    assert response.status_code in (401, 403)
    assert not Episode.all_objects.exists()


def test_anonymous_is_refused(mapped):
    response = _post(APIClient(), _event("SessionStart"))
    assert response.status_code == 401
    assert not Episode.all_objects.exists()


@pytest.mark.parametrize("state", ["revoked", "expired"])
def test_revoked_or_expired_hook_tokens_are_refused(owner, personal, mapped, state):
    now = timezone.now()
    extra = (
        {"revoked_at": now} if state == "revoked" else {"expires_at": now - timedelta(minutes=1)}
    )
    raw, _ = _token(owner, personal, **extra)
    response = _post(_bearer(raw), _event("SessionStart"))
    assert response.status_code == 401
    assert not Episode.all_objects.exists()


def test_a_non_owner_hook_token_stores_nothing(mapped):
    # A hook token can only be minted by the owner; one created behind the
    # API's back for another user is still useless: no source of theirs.
    member = register_user(email="member@test.dev", password=PASSWORD)
    raw, _ = _token(member, Workspace.objects.get(owner=member))
    response = _post(_bearer(raw), _event("SessionStart"))
    assert response.status_code == 202
    assert response.json()["stored"] == 0
    assert response.json()["dropped_paused"] == 1
    assert not Episode.all_objects.exists()


# --- the endpoint: append-only, counts only ----------------------------------


def test_events_are_stored_and_answered_with_counts_only(hook_client, mapped):
    response = _post(hook_client, _event("SessionStart", source="startup"))
    assert response.status_code == 202
    assert response.json() == {
        "received": 1,
        "stored": 1,
        "duplicate": 0,
        "dropped_unmapped": 0,
        "dropped_paused": 0,
        "ignored": 0,
        "invalid": 0,
    }
    episode = Episode.objects.get()
    assert episode.kind == "hook.session_start"
    assert episode.owner_text == ""  # hook events carry no owner text in 8A
    assert episode.metadata == {
        "event": "SessionStart",
        "session_id": SID,
        "project": "friday",
        "start_source": "startup",
    }


def test_the_response_never_echoes_the_payload(hook_client, mapped):
    response = _post(
        hook_client, _event("Notification", message="Claude needs your permission to use Bash")
    )
    body = response.content.decode()
    assert "permission" not in body
    assert SID not in body
    assert "/Users/o" not in body


def test_get_is_not_allowed_on_the_ingest_endpoint(hook_client, mapped):
    _post(hook_client, _event("SessionStart"))
    response = hook_client.get(INGEST)
    assert response.status_code == 405
    assert SID not in response.content.decode()


def test_unknown_event_names_and_fields_are_dropped(hook_client, mapped):
    response = _post(
        hook_client,
        _event("UserPromptSubmit", prompt="my secret plan"),
        _event("PreToolUse", tool_input={"command": "ls"}),
        _event(
            "Stop",
            transcript_path="/Users/o/.claude/projects/x.jsonl",
            prompt="never stored",
            stop_hook_active=False,
        ),
    )
    counts = response.json()
    assert counts["ignored"] == 2
    assert counts["stored"] == 1
    episode = Episode.objects.get()
    stored = json.dumps([episode.metadata, episode.context_text, episode.owner_text])
    assert "transcript" not in stored
    assert "never stored" not in stored
    assert "secret plan" not in _all_text_in_db()


@pytest.mark.parametrize(
    "bad",
    [
        {"hook_event_name": "Stop", "cwd": "/Users/o/code/x"},  # no session id
        _event("Stop", session_id="has spaces"),
        _event("Stop", cwd="relative/path"),
        _event("Stop", cwd=7),
        _event("Stop", occurred_at="yesterday"),
        _event("Stop", occurred_at="2026-11-19T09:00:00"),  # naive
        _event("Stop", event_id="x"),
        _event("Notification", notification_type="Not A Token"),
        "not an object",
    ],
)
def test_schema_failures_are_counted_invalid(hook_client, mapped, bad):
    response = _post(hook_client, bad)
    assert response.status_code == 202
    assert response.json()["invalid"] == 1
    assert not Episode.all_objects.exists()


def test_an_old_event_is_invalid_and_a_future_one_is_clamped(hook_client, mapped):
    now = timezone.now()
    old = (now - timedelta(days=2)).isoformat()
    future = (now + timedelta(days=1)).isoformat()
    response = _post(
        hook_client,
        _event("Stop", occurred_at=old, event_id="a" * 16),
        _event("Stop", occurred_at=future, event_id="b" * 16),
    )
    assert response.json()["invalid"] == 1
    assert Episode.objects.get().occurred_at <= timezone.now()


def test_a_body_that_is_not_an_events_object_is_400(hook_client, mapped):
    assert hook_client.post(INGEST, [1, 2], format="json").status_code == 400
    assert hook_client.post(INGEST, {"event": []}, format="json").status_code == 400


def test_more_than_fifty_events_is_413(hook_client, mapped):
    events = [_event("Stop", event_id=f"event{i:04d}x") for i in range(51)]
    response = _post(hook_client, *events)
    assert response.status_code == 413
    assert not Episode.all_objects.exists()


def test_a_body_over_64_kb_is_413(hook_client, mapped):
    response = _post(hook_client, _event("Notification", message="x" * (65 * 1024)))
    assert response.status_code == 413
    assert not Episode.all_objects.exists()


def test_fifty_events_are_accepted(hook_client, mapped):
    events = [_event("Stop", event_id=f"event{i:04d}x") for i in range(50)]
    response = _post(hook_client, *events)
    assert response.status_code == 202
    assert response.json()["stored"] == 50


def test_a_replayed_event_is_a_duplicate(hook_client, mapped):
    event = _event("Stop", event_id="replayed-event-1")
    assert _post(hook_client, event).json()["stored"] == 1
    assert _post(hook_client, event).json()["duplicate"] == 1
    assert Episode.objects.count() == 1
    assert CodingSession.objects.get().event_count == 1


def test_the_endpoint_is_throttled(hook_client, mapped, monkeypatch):
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"hook_ingest": "2/min"})
    statuses = [_post(hook_client, _event("Stop")).status_code for _ in range(3)]
    assert statuses == [202, 202, 429]


class _DownCache:
    def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    set = get


def test_throttle_backend_down_fails_closed_with_503(hook_client, mapped, monkeypatch):
    monkeypatch.setattr(ScopedRateThrottle, "cache", _DownCache())
    response = _post(hook_client, _event("SessionStart"))
    assert response.status_code == 503
    assert response.json()["code"] == "throttle_backend_unavailable"
    assert not Episode.all_objects.exists()


def test_default_hook_ingest_rate():
    from django.conf import settings

    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["hook_ingest"] == "240/min"


# --- scrubbing and mapping ---------------------------------------------------


def test_a_secret_in_a_notification_never_reaches_the_db(hook_client, mapped):
    message = f"Bash wants to run: export ANTHROPIC_API_KEY={FAKE_KEY} && deploy"
    response = _post(hook_client, _event("Notification", message=message))
    assert response.json()["stored"] == 1
    assert FAKE_KEY not in _all_text_in_db()
    assert FAKE_KEY[:20] not in _all_text_in_db()
    episode = Episode.objects.get()
    assert "[SECRET:" in episode.context_text
    assert len(episode.context_text) <= CodingSession.MAX_NOTIFICATION_CHARS
    session = CodingSession.objects.get()
    assert "[SECRET:" in session.last_notification
    counter = IngestionCounter.objects.get(source=mapped)
    assert sum(counter.scrubber_hits.values()) >= 1


def test_pii_in_a_notification_is_scrubbed(hook_client, mapped):
    _post(hook_client, _event("Notification", message="mail ayse@example.com.tr now"))
    assert "ayse@example.com.tr" not in _all_text_in_db()


def test_a_long_notification_is_cut_after_scrubbing(hook_client, mapped):
    message = "a" * 150 + " " + FAKE_KEY + " " + "b" * 400
    _post(hook_client, _event("Notification", message=message))
    episode = Episode.objects.get()
    assert len(episode.context_text) == CodingSession.MAX_NOTIFICATION_CHARS
    assert FAKE_KEY[:12] not in episode.context_text


def test_an_unmapped_cwd_stores_nothing(hook_client, mapped):
    response = _post(hook_client, _event("SessionStart", cwd="/tmp/elsewhere"))
    assert response.json()["dropped_unmapped"] == 1
    assert not Episode.all_objects.exists()
    assert not CodingSession.objects.exists()
    counter = IngestionCounter.objects.get(source=mapped)
    assert counter.dropped_unmapped == 1


def test_a_disabled_source_stores_nothing(hook_client, mapped):
    mapped.enabled = False
    mapped.save()
    response = _post(hook_client, _event("SessionStart"))
    assert response.json()["dropped_paused"] == 1
    assert not Episode.all_objects.exists()
    assert not CodingSession.objects.exists()


def test_a_firm_cwd_lands_only_in_the_firm_workspace(hook_client, mapped, personal, firm):
    """Brief follow-up (D6 vs D4): the token is bound to the personal
    workspace, yet only the mapping rule decides -- a firm path lands in the
    firm workspace and nowhere else."""
    _post(hook_client, _event("SessionStart", cwd="/Users/o/code/acme/api"))
    episode = Episode.objects.get()
    assert episode.workspace == firm
    session = CodingSession.objects.get()
    assert session.workspace == firm
    assert session.project == "api"
    assert not Episode.objects.filter(workspace=personal).exists()
    assert not CodingSession.objects.filter(workspace=personal).exists()


def test_without_a_firm_rule_firm_paths_never_default_to_personal(hook_client, hooks, firm):
    add_rule(hooks, PATH, "/Users/o/code/acme", firm, enabled=False)
    response = _post(hook_client, _event("SessionStart", cwd="/Users/o/code/acme/api"))
    assert response.json()["dropped_unmapped"] == 1
    assert not Episode.all_objects.exists()


def test_a_rule_to_a_workspace_the_owner_no_longer_owns_drops(hook_client, mapped, owner, firm):
    WorkspaceMember.objects.filter(workspace=firm, user=owner).update(
        role=WorkspaceMember.Role.MEMBER
    )
    response = _post(hook_client, _event("SessionStart", cwd="/Users/o/code/acme/api"))
    assert response.json()["dropped_unmapped"] == 1
    assert not Episode.all_objects.exists()


def test_a_session_moving_between_workspaces_gets_one_row_each(hook_client, mapped, firm):
    _post(hook_client, _event("SessionStart"))
    _post(
        hook_client,
        _event("Notification", cwd="/Users/o/code/acme/api", message="Acme permission"),
    )
    rows = {s.workspace_id: s for s in CodingSession.objects.all()}
    assert len(rows) == 2
    personal_row = next(s for w, s in rows.items() if w != firm.id)
    assert personal_row.last_notification == ""
    assert rows[firm.id].last_notification == "Acme permission"


# --- CodingSession status transitions ----------------------------------------


def _status():
    return CodingSession.objects.get().status


def test_status_transitions(hook_client, mapped):
    _post(hook_client, _event("SessionStart", source="startup"))
    assert _status() == "running"
    _post(
        hook_client,
        _event(
            "Notification",
            notification_type="permission_prompt",
            message="Claude needs your permission to use Bash",
        ),
    )
    assert _status() == "waiting_for_owner"
    _post(hook_client, _event("Stop"))
    assert _status() == "running"
    _post(hook_client, _event("Notification", notification_type="idle_prompt"))
    assert _status() == "waiting_for_owner"
    _post(hook_client, _event("SessionEnd", reason="logout"))
    session = CodingSession.objects.get()
    assert session.status == "stopped"
    assert session.event_count == 5
    assert session.last_event == "SessionEnd"
    assert session.last_notification == ""  # the idle prompt had no message
    # Resuming the same session id starts it again.
    _post(hook_client, _event("SessionStart", source="resume"))
    assert _status() == "running"


def test_an_informational_notification_keeps_the_status(hook_client, mapped):
    _post(hook_client, _event("SessionStart"))
    _post(hook_client, _event("Notification", notification_type="auth_success", message="ok"))
    assert _status() == "running"
    assert CodingSession.objects.get().last_notification == "ok"


def test_an_out_of_order_event_never_overwrites_newer_state(hook_client, mapped):
    now = timezone.now()
    _post(hook_client, _event("SessionEnd", occurred_at=now.isoformat(), event_id="end-event-1"))
    earlier = (now - timedelta(minutes=5)).isoformat()
    _post(hook_client, _event("SessionStart", occurred_at=earlier, event_id="start-event-1"))
    session = CodingSession.objects.get()
    assert session.status == "stopped"
    assert session.event_count == 2
    assert session.started_at.isoformat() == earlier


@pytest.mark.parametrize(
    "event, current, expected",
    [
        ("SessionStart", None, "running"),
        ("SessionStart", "stopped", "running"),
        ("Notification", "running", "waiting_for_owner"),
        ("Stop", "waiting_for_owner", "running"),
        ("SessionEnd", "running", "stopped"),
    ],
)
def test_next_status(event, current, expected):
    parsed = hooks.parse_event(_event(event))
    assert hooks.next_status(current, parsed) == expected


# --- GET /memory/sessions -----------------------------------------------------


def _session_client(principal, owner, personal, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal in ("cli", "openai", "hook"):
        raw, _ = _token(owner, personal, kind=principal)
        return _bearer(raw)
    if principal == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


@pytest.mark.parametrize(
    "principal, expected",
    [
        ("owner-web", 200),
        ("cli", 401),
        ("openai", 401),
        ("hook", 401),
        ("non-owner", 403),
        ("agent", 403),
        ("anonymous", 401),
    ],
)
def test_sessions_role_matrix(
    owner, personal, hook_client, mapped, web_client, principal, expected
):
    _post(hook_client, _event("SessionStart"))
    client = _session_client(principal, owner, personal, web_client)
    response = client.get(SESSIONS)
    assert response.status_code == expected
    if expected != 200:
        assert SID not in response.content.decode()


def test_sessions_lists_the_owner_sessions_with_workspace(
    owner, hook_client, mapped, personal, firm, web_client
):
    _post(hook_client, _event("SessionStart"))
    _post(
        hook_client, _event("SessionStart", session_id="firm-session-1", cwd="/Users/o/code/acme")
    )
    rows = web_client(owner).get(SESSIONS).json()
    assert {r["workspace"]["id"] for r in rows} == {str(personal.id), str(firm.id)}
    only_firm = web_client(owner).get(SESSIONS, {"workspace": str(firm.id)}).json()
    assert [r["session_id"] for r in only_firm] == ["firm-session-1"]
    assert only_firm[0]["project"] == "acme"
    assert only_firm[0]["status"] == "running"


def test_another_workspace_sessions_are_never_listed(owner, hook_client, mapped, web_client):
    _post(hook_client, _event("SessionStart"))
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    stranger_ws = Workspace.objects.get(owner=stranger)
    # The owner of the install asking for a workspace they are not in: 404.
    response = web_client(owner).get(SESSIONS, {"workspace": str(stranger_ws.id)})
    assert response.status_code == 404
    # A session row in the stranger's workspace (built directly) is never
    # in the owner's list.
    CodingSession.objects.create(
        workspace=stranger_ws,
        source=mapped,
        session_id="stranger-session",
        started_at=timezone.now(),
        last_event_at=timezone.now(),
    )
    ids = [r["session_id"] for r in web_client(owner).get(SESSIONS).json()]
    assert ids == [SID]


def test_a_firm_member_never_sees_the_owner_personal_sessions(
    owner, hook_client, mapped, firm, web_client
):
    _post(hook_client, _event("SessionStart"))
    _post(
        hook_client, _event("SessionStart", session_id="firm-session-1", cwd="/Users/o/code/acme")
    )
    member = register_user(email="member@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    visible = CodingSession.objects.for_actor(member)
    assert [s.session_id for s in visible] == ["firm-session-1"]
    # ...and the API itself stays owner-only.
    assert web_client(member).get(SESSIONS).status_code == 403


def test_sessions_with_no_event_for_24h_are_stale(owner, hook_client, mapped, web_client):
    _post(hook_client, _event("SessionStart"))
    _post(hook_client, _event("SessionStart", session_id="ended-session"))
    _post(hook_client, _event("SessionEnd", session_id="ended-session"))
    CodingSession.objects.update(last_event_at=timezone.now() - timedelta(hours=25))
    rows = {r["session_id"]: r for r in web_client(owner).get(SESSIONS).json()}
    assert rows[SID]["status"] == "stale"
    assert rows[SID]["stored_status"] == "running"
    assert rows["ended-session"]["status"] == "stopped"


def test_an_empty_list_is_fine(owner, personal, web_client):
    assert web_client(owner).get(SESSIONS).json() == []


def test_a_forgotten_source_hides_its_sessions_until_restored(
    owner, hook_client, mapped, web_client
):
    _post(hook_client, _event("SessionStart"))
    ingestion_services.forget_source(mapped, actor=owner, channel=CHANNEL_WEB)
    assert web_client(owner).get(SESSIONS).json() == []
    # Forgetting pauses the source: new events are dropped.
    assert _post(hook_client, _event("Stop")).json()["dropped_paused"] == 1
    mapped.refresh_from_db()
    ingestion_services.restore_source(mapped, actor=owner, channel=CHANNEL_WEB)
    assert [r["session_id"] for r in web_client(owner).get(SESSIONS).json()] == [SID]


def test_the_ttl_beat_deletes_idle_sessions(hook_client, mapped, personal, firm):
    _post(hook_client, _event("SessionStart"))
    _post(
        hook_client, _event("SessionStart", session_id="firm-session-1", cwd="/Users/o/code/acme")
    )
    now = timezone.now()
    CodingSession.objects.filter(workspace=firm).update(last_event_at=now - timedelta(days=15))
    CodingSession.objects.filter(workspace=personal).update(last_event_at=now - timedelta(days=15))
    purge_expired_episodes_task()
    # 15 days idle: past the firm TTL (14), inside the personal TTL (30).
    assert list(CodingSession.objects.values_list("workspace_id", flat=True)) == [personal.id]
