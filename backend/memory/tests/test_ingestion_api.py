"""Phase 8A branch 1 owner APIs (brief D11): role x endpoint against the
five Phase 4 principals, another workspace's rows -> 404, and the
behaviour of each endpoint.

PATs: these views use default authentication only (no PAT allowlist, the
connectors-bindings precedent), so an owner `cli`/`openai` token is not
authenticated at all -> 401.
"""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.ingestion import services
from memory.ingestion.ports import record_episode
from memory.models import Episode, IngestionSource, ScrubTerm, WorkspaceMappingRule
from memory.tests.ingestion_helpers import PASSWORD, WHEN, add_rule
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

BASE = "/api/v1/memory/ingestion"
PATH = WorkspaceMappingRule.RuleType.PATH_PREFIX


def _pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal == "owner-pat-cli":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
    if principal == "owner-pat-openai":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.OPENAI)
    if principal == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


PRINCIPALS = [
    ("owner-web", "ok"),
    ("owner-pat-cli", 401),
    ("owner-pat-openai", 401),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.fixture
def setup(owner, personal, firm, hooks):
    rule = add_rule(hooks, PATH, "/Users/o/code", personal)
    term = ScrubTerm.objects.create(workspace=personal, term="Initech")
    record_episode(
        source=hooks,
        kind="hook.stop",
        external_id="e1",
        occurred_at=WHEN,
        mapping_key="path:/Users/o/code/x",
        owner_text="hello",
    )
    return {"rule": rule, "term": term}


def _endpoints(personal, firm, hooks, setup):
    """(method, url, body, success status)."""
    src = f"{BASE}/sources/{hooks.id}"
    return [
        ("get", f"{BASE}/sources?workspace={personal.id}", None, 200),
        ("get", src, None, 200),
        ("patch", src, {"daily_token_budget": 1000}, 200),
        ("post", f"{src}/forget", {}, 200),
        ("get", f"{src}/rules", None, 200),
        (
            "post",
            f"{src}/rules",
            {"rule_type": "path_prefix", "pattern": "/w", "target_workspace": str(firm.id)},
            201,
        ),
        ("patch", f"{BASE}/rules/{setup['rule'].id}", {"enabled": False}, 200),
        ("get", f"{BASE}/scrub-terms?workspace={personal.id}", None, 200),
        ("post", f"{BASE}/scrub-terms", {"workspace": str(personal.id), "term": "Hooli"}, 201),
        ("delete", f"{BASE}/scrub-terms/{setup['term'].id}", None, 204),
    ]


ENDPOINT_IDS = [
    "list-sources",
    "source-audit",
    "source-update",
    "source-forget",
    "list-rules",
    "create-rule",
    "update-rule",
    "list-terms",
    "create-term",
    "delete-term",
]


@pytest.mark.parametrize("index", range(len(ENDPOINT_IDS)), ids=ENDPOINT_IDS)
@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_role_matrix(owner, personal, firm, hooks, setup, web_client, principal, expected, index):
    method, url, body, success = _endpoints(personal, firm, hooks, setup)[index]
    client = _client(principal, owner, personal, web_client)
    response = getattr(client, method)(url, body, format="json")
    assert response.status_code == (success if expected == "ok" else expected)
    if expected != "ok":
        # Nothing changed: the source is still live and not forgotten.
        hooks.refresh_from_db()
        assert hooks.forgotten_at is None and hooks.daily_token_budget == 40000
        assert ScrubTerm.objects.filter(id=setup["term"].id).exists()


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_restore_role_matrix(owner, personal, hooks, setup, web_client, principal, expected):
    services.forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    url = f"{BASE}/sources/{hooks.id}/restore"
    client = _client(principal, owner, personal, web_client)
    response = client.post(url, {}, format="json")
    if expected == "ok":
        assert response.status_code == 200
        assert response.json()["episodes"] == 1
        assert Episode.objects.count() == 1
    else:
        assert response.status_code == expected
        assert Episode.objects.count() == 0


@pytest.fixture
def foreign(db):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    stranger.save()
    workspace = Workspace.objects.get(owner=stranger)
    services.ensure_default_sources(workspace=workspace, created_by=stranger)
    source = IngestionSource.objects.get(workspace=workspace, kind="git")
    rule = add_rule(source, PATH, "/s", workspace)
    term = ScrubTerm.objects.create(workspace=workspace, term="Stranger Co")
    return {"workspace": workspace, "source": source, "rule": rule, "term": term}


def test_another_workspaces_rows_are_404(owner, personal, web_client, foreign):
    client = web_client(owner)
    src = f"{BASE}/sources/{foreign['source'].id}"
    ws = foreign["workspace"].id
    assert client.get(f"{BASE}/sources?workspace={ws}").status_code == 404
    assert client.get(src).status_code == 404
    assert client.patch(src, {"enabled": True}, format="json").status_code == 404
    assert client.post(f"{src}/forget", {}, format="json").status_code == 404
    assert client.post(f"{src}/restore", {}, format="json").status_code == 404
    assert client.get(f"{src}/rules").status_code == 404
    assert (
        client.patch(
            f"{BASE}/rules/{foreign['rule'].id}", {"enabled": False}, format="json"
        ).status_code
        == 404
    )
    assert client.get(f"{BASE}/scrub-terms?workspace={ws}").status_code == 404
    assert (
        client.post(
            f"{BASE}/scrub-terms", {"workspace": str(ws), "term": "x1"}, format="json"
        ).status_code
        == 404
    )
    assert client.delete(f"{BASE}/scrub-terms/{foreign['term'].id}").status_code == 404
    foreign["source"].refresh_from_db()
    assert foreign["source"].enabled is False
    assert ScrubTerm.objects.filter(id=foreign["term"].id).exists()


def test_rule_target_in_another_workspace_is_404(owner, hooks, web_client, foreign):
    response = web_client(owner).post(
        f"{BASE}/sources/{hooks.id}/rules",
        {
            "rule_type": "path_prefix",
            "pattern": "/x",
            "target_workspace": str(foreign["workspace"].id),
        },
        format="json",
    )
    assert response.status_code == 404
    assert not WorkspaceMappingRule.objects.filter(pattern="/x").exists()


def test_list_and_audit_view(owner, personal, firm, hooks, setup, web_client):
    client = web_client(owner)
    rows = client.get(f"{BASE}/sources", {"workspace": str(personal.id)}).json()
    assert sorted(r["kind"] for r in rows) == sorted(IngestionSource.Kind.values)
    hooks_row = next(r for r in rows if r["kind"] == "claude_code_hooks")
    assert hooks_row["enabled"] is True and hooks_row["today"]["stored"] == 1
    # Firm workspace holds no sources (they live in the personal workspace).
    assert client.get(f"{BASE}/sources", {"workspace": str(firm.id)}).json() == []

    record_episode(
        source=hooks,
        kind="hook.stop",
        external_id="e2",
        occurred_at=WHEN,
        mapping_key="path:/nowhere",
    )
    body = client.get(f"{BASE}/sources/{hooks.id}").json()
    assert [e["owner_text"] for e in body["episodes"]] == ["hello"]
    assert body["episodes"][0]["workspace_name"] == personal.name
    assert body["unmapped_keys"][0]["short"] == "path:/nowhere"
    assert body["counters"][-1]["dropped_unmapped"] == 1
    assert body["forget_preview"] == {
        "episodes": 1,
        "memories": 0,
        "core_block_proposals": 0,
        "facts": 0,
    }
    assert body["acceptance"]["rate"] is None
    assert body["daily_token_budget"] == 40000 and body["tokens_spent_today"] == 0
    viewed = AuditLog.objects.filter(action="memory.ingestion.viewed")
    assert viewed.count() == 3  # two list reads + one audit view
    assert "hello" not in str(list(viewed.values_list("metadata", flat=True)))


def test_owner_flow_over_the_api(owner, personal, firm, hooks, setup, web_client):
    client = web_client(owner)
    src = f"{BASE}/sources/{hooks.id}"
    assert client.patch(src, {}, format="json").status_code == 400
    body = client.patch(src, {"paused": True}, format="json").json()
    assert body["paused"] is True
    body = client.patch(src, {"paused": False, "enabled": True}, format="json").json()
    assert body["paused"] is False

    created = client.post(
        f"{src}/rules",
        {
            "rule_type": "path_prefix",
            "pattern": "/Users/o/code/acme/",
            "target_workspace": str(firm.id),
        },
        format="json",
    )
    assert created.status_code == 201 and created.json()["pattern"] == "/Users/o/code/acme"
    bad = client.post(
        f"{src}/rules",
        {"rule_type": "path_prefix", "pattern": "relative", "target_workspace": str(firm.id)},
        format="json",
    )
    assert bad.status_code == 400 and bad.json()["code"] == "memory.invalid_transition"
    rules = client.get(f"{src}/rules").json()
    assert len(rules) == 2

    forgotten = client.post(f"{src}/forget", {}, format="json").json()
    assert forgotten["episodes"] == 1 and forgotten["source"]["restorable"] is True
    assert not Episode.objects.exists()
    restored = client.post(f"{src}/restore", {}, format="json").json()
    assert restored["episodes"] == 1 and restored["source"]["restorable"] is False
    assert client.post(f"{src}/restore", {}, format="json").status_code == 400
    assert client.post(f"{src}/unknown", {}, format="json").status_code == 404

    term = client.post(
        f"{BASE}/scrub-terms", {"workspace": str(firm.id), "term": "Globex"}, format="json"
    )
    assert term.status_code == 201
    dup = client.post(
        f"{BASE}/scrub-terms", {"workspace": str(firm.id), "term": "globex"}, format="json"
    )
    assert dup.status_code == 400
    listed = client.get(f"{BASE}/scrub-terms", {"workspace": str(firm.id)}).json()
    assert [t["term"] for t in listed] == ["Globex"]


def test_a_workspace_admin_who_is_not_owner_is_refused(owner, personal, hooks, setup, web_client):
    """The installation owner acting on a workspace where their role is not
    `owner` (defence in depth behind IsOwner)."""
    from workspaces.models import WorkspaceMember

    WorkspaceMember.objects.filter(workspace=personal, user=owner).update(
        role=WorkspaceMember.Role.ADMIN
    )
    client = web_client(owner)
    response = client.get(f"{BASE}/sources/{hooks.id}")
    assert response.status_code == 403
    assert response.json()["code"] == "memory.permission_denied"


# --- Phase 8B branch 7 (brief D12): vault_prefix rules over the API ----------


def test_vault_prefix_rule_types_are_checked_per_source(owner, personal, hooks, web_client):
    inbox = IngestionSource.objects.get(
        workspace=personal, kind=IngestionSource.Kind.OBSIDIAN_INBOX
    )
    client = web_client(owner)
    target = str(personal.id)
    on_hooks = client.post(
        f"{BASE}/sources/{hooks.id}/rules",
        {"rule_type": "vault_prefix", "pattern": "00_Inbox/work", "target_workspace": target},
        format="json",
    )
    assert on_hooks.status_code == 400
    path_on_inbox = client.post(
        f"{BASE}/sources/{inbox.id}/rules",
        {"rule_type": "path_prefix", "pattern": "/Users/o", "target_workspace": target},
        format="json",
    )
    assert path_on_inbox.status_code == 400
    for pattern in ("00_Inbox/../03_Workspaces", "00_Inbox/a/../../x", "/abs/00_Inbox"):
        escaping = client.post(
            f"{BASE}/sources/{inbox.id}/rules",
            {"rule_type": "vault_prefix", "pattern": pattern, "target_workspace": target},
            format="json",
        )
        assert escaping.status_code == 400
    created = client.post(
        f"{BASE}/sources/{inbox.id}/rules",
        {"rule_type": "vault_prefix", "pattern": "00_Inbox/work/", "target_workspace": target},
        format="json",
    )
    assert created.status_code == 201 and created.json()["pattern"] == "00_Inbox/work"
    assert WorkspaceMappingRule.objects.filter(source=inbox).count() == 1
    assert not WorkspaceMappingRule.objects.filter(source=hooks, rule_type="vault_prefix").exists()


# --- Phase 8B branch 8 (brief D13): the transcript sources over the API -------


def _transcript_endpoints(source, firm):
    src = f"{BASE}/sources/{source.id}"
    return [
        ("get", src, None, 200),
        ("patch", src, {"enabled": True}, 200),
        ("get", f"{src}/rules", None, 200),
        (
            "post",
            f"{src}/rules",
            {"rule_type": "path_prefix", "pattern": "/w", "target_workspace": str(firm.id)},
            400,  # the rules are the hooks source's: refused even for the owner
        ),
    ]


TRANSCRIPT_ENDPOINT_IDS = ["audit", "enable", "list-rules", "create-rule"]


@pytest.mark.parametrize("kind", ["claude_code_transcripts", "codex_transcripts"])
@pytest.mark.parametrize("index", range(4), ids=TRANSCRIPT_ENDPOINT_IDS)
@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_transcript_source_role_matrix(
    owner, personal, firm, sources, web_client, principal, expected, index, kind
):
    source = sources[kind]
    method, url, body, success = _transcript_endpoints(source, firm)[index]
    client = _client(principal, owner, personal, web_client)
    response = getattr(client, method)(url, body, format="json")
    assert response.status_code == (success if expected == "ok" else expected)
    source.refresh_from_db()
    assert not WorkspaceMappingRule.objects.filter(source=source).exists()
    if expected != "ok":
        assert source.enabled is False and source.enabled_at is None


def test_enabling_a_transcript_source_over_the_api_records_enabled_at(
    owner, personal, sources, web_client
):
    source = sources["claude_code_transcripts"]
    client = web_client(owner)
    response = client.patch(f"{BASE}/sources/{source.id}", {"enabled": True}, format="json")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True and body["enabled_at"] is not None
    assert body["rules_shared_with"] == "claude_code_hooks"
    services.bump_counter(source, "dropped_not_owner")
    audit = client.get(f"{BASE}/sources/{source.id}").json()
    assert audit["rules_shared_with"] == "claude_code_hooks"
    assert audit["counters"][0]["dropped_not_owner"] == 1
    assert audit["counters"][0]["dropped_foreign"] == 0
