"""Phase 8A closeout end to end (brief "7. closeout"): a fixture commit and
a hook event -> the nightly learning job (eager Celery, on-commit dispatch
executed) -> the morning card -> approve -> the approved memory is used in
the next chat answer.

Everything goes through the real surfaces: the owner enables sources and
writes mapping rules over the web API, the git producer runs its runner
primitive against a local fixture repo in `tmp_path` (`file://`, no
remote), the hook event is posted with a real `hook` PAT the way
`friday-hook` posts it, and the chat turn is the real SSE view with the
FakeAdapter behind the gateway. It lives in `ops` because it spans
`harness`, `memory` and `assistant` (ops is the leaf allowed to import all
three). No real LLM, Claude Code, Keychain, GitHub or git remote."""

import json
import secrets

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelUsage
from assistant.models import Conversation
from harness.models import HarnessRepo
from harness.tasks import ingest_repo_commits
from harness.tests.test_commit_ingestion import FixtureRepo
from memory.ingestion.tasks import run_learning_night_task
from memory.models import CodingSession, CoreBlockProposal, Episode, IngestionSource, Memory
from workspaces.models import Workspace
from workspaces.services import create_firm_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
OWNER_EMAIL = "owner@test.dev"
COMMIT_MESSAGE = "Prefer short Turkish commit subjects and keep the scrubber stdlib only"
FIRM_COMMIT = "Acme billing export moves to the nightly batch window"
SECRET = "ghp_" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8"
INGEST = "/api/v1/memory/ingest/hook-events"
SOURCES = "/api/v1/memory/ingestion/sources"
LEARNING = "/api/v1/memory/learning"
Kind = IngestionSource.Kind


@pytest.fixture(autouse=True)
def stack(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.MEMORY_OWNER_GIT_EMAILS = [OWNER_EMAIL]
    call_command("seed_ai_defaults", stdout=open("/dev/null", "w"))  # noqa: SIM115


@pytest.fixture
def owner():
    user = register_user(email=OWNER_EMAIL, password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    from core.channels import CHANNEL_WEB

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def calls(monkeypatch):
    captured = []
    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        captured.append((purpose, json.dumps(messages, ensure_ascii=False)))
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    return captured


def _hook_client(owner, personal):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=personal,
        user=owner,
        name="claude-code-hook",
        kind=PersonalAccessToken.Kind.HOOK,
        token_hash=hash_token(raw),
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _sse(response):
    assert response.status_code == 200, response.content
    events = []
    for chunk in b"".join(response.streaming_content).decode().split("\n\n"):
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))
    return events


def _setup_sources(web, personal, firm, repo, firm_repo):
    """The runbook's steps over the web API: seed, enable, map."""
    call_command("seed_ingestion_sources", stdout=open("/dev/null", "w"))  # noqa: SIM115
    rows = web.get(f"{SOURCES}?workspace={personal.id}").json()
    by_kind = {row["kind"]: row["id"] for row in rows}
    assert set(by_kind) == {Kind.GIT, Kind.HARNESS, Kind.CLAUDE_CODE_HOOKS, Kind.CALENDAR}
    for kind in (Kind.GIT, Kind.CLAUDE_CODE_HOOKS):
        response = web.patch(f"{SOURCES}/{by_kind[kind]}", {"enabled": True}, format="json")
        assert response.status_code == 200, response.content
    for pattern, rule_type, kind, target in (
        (str(repo.id), "repo", Kind.GIT, personal),
        (str(firm_repo.id), "repo", Kind.GIT, firm),
        ("/Users/o/code", "path_prefix", Kind.CLAUDE_CODE_HOOKS, personal),
    ):
        response = web.post(
            f"{SOURCES}/{by_kind[kind]}/rules",
            {"rule_type": rule_type, "pattern": pattern, "target_workspace": str(target.id)},
            format="json",
        )
        assert response.status_code == 201, response.content


def test_commit_and_hook_event_to_an_approved_memory_used_in_chat(
    owner, personal, firm, tmp_path, calls, web_client, django_capture_on_commit_callbacks
):
    web = web_client(owner)
    fixture = FixtureRepo(tmp_path / "friday")
    firm_fixture = FixtureRepo(tmp_path / "acme")
    repo = HarnessRepo.objects.create(
        workspace=personal, name="friday", git_url=fixture.url, default_branch="main", enabled=True
    )
    firm_repo = HarnessRepo.objects.create(
        workspace=firm, name="acme", git_url=firm_fixture.url, default_branch="main", enabled=True
    )
    _setup_sources(web, personal, firm, repo, firm_repo)

    # 1. A fixture commit by the owner (with a pasted token in its body), a
    #    firm commit, and an agent-written commit (metadata only).
    fixture.commit(f"{COMMIT_MESSAGE}\n\nused {SECRET} while testing")
    fixture.commit("Agent refactor\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    firm_fixture.commit(FIRM_COMMIT)
    result = ingest_repo_commits()
    assert result is None or isinstance(result, dict)
    commits = Episode.objects.filter(kind="git.commit")
    assert commits.count() == 3
    assert not any(SECRET in (e.owner_text + e.context_text) for e in commits)

    # 2. A hook event, posted the way `friday-hook` posts it.
    hook = _hook_client(owner, personal)
    response = hook.post(
        INGEST,
        {
            "events": [
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "0b9f6a3e-2c7d-4f51-9a8e-1d2c3b4a5f60",
                    "cwd": "/Users/o/code/friday",
                    "source": "startup",
                }
            ]
        },
        format="json",
    )
    assert response.status_code in (200, 202), response.content
    session = CodingSession.objects.get()
    assert session.workspace_id == personal.id and session.project == "friday"
    sessions = web.get("/api/v1/memory/sessions").json()
    assert "0b9f6a3e" in json.dumps(sessions)

    # 3. The nightly learning run, eager, with on-commit dispatch executed.
    with django_capture_on_commit_callbacks(execute=True):
        night = run_learning_night_task()
    assert night["jobs_planned"] >= 2  # personal and firm
    extraction_payloads = [m for p, m in calls if p == "memory.episode_extraction"]
    assert extraction_payloads
    assert all(SECRET not in m for m in extraction_payloads)
    assert all("Agent refactor" not in m.split("<untrusted_context>")[0] for m in extraction_payloads)
    # Firm text never reaches a personal-workspace payload, and vice versa.
    for payload in extraction_payloads:
        assert not (COMMIT_MESSAGE in payload and FIRM_COMMIT in payload)
    # Hook events carry no owner text: no proposal from them.
    learned = Memory.objects.filter(ingestion_source__isnull=False)
    assert not learned.filter(ingestion_source__kind=Kind.CLAUDE_CODE_HOOKS).exists()
    assert not learned.filter(status=Memory.Status.APPROVED).exists()  # proposals only
    assert not CoreBlockProposal.objects.filter(status="approved").exists()
    # Exactly one ModelUsage per learning call, no text in the report.
    usage = ModelUsage.objects.filter(purpose="memory.episode_extraction")
    assert usage.count() == len(extraction_payloads)
    assert all(COMMIT_MESSAGE not in json.dumps(u.context_report) for u in usage)

    # 4. The morning card: the owner's own words, in the right workspace.
    body = web.get(f"{LEARNING}/digest").json()
    groups = {g["workspace"]["id"]: g["items"] for g in body["workspaces"]}
    personal_items = groups[str(personal.id)]
    item = next(i for i in personal_items if COMMIT_MESSAGE[:40] in i["quote"])
    assert item["source_kind"] == Kind.GIT
    assert item["bulk_approvable"] is False
    assert all(FIRM_COMMIT not in i["quote"] for i in personal_items)
    assert any(FIRM_COMMIT in i["quote"] for i in groups[str(firm.id)])
    assert all("Agent refactor" not in i["quote"] for g in groups.values() for i in g)

    # 5. Approve it (a human click, web channel).
    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(f"{LEARNING}/proposals/{item['kind']}/{item['id']}/approve", {})
    assert response.status_code == 200, response.content
    memory = Memory.objects.get(id=item["id"])
    assert memory.status == Memory.Status.APPROVED
    assert memory.workspace_id == personal.id

    # 6. The next chat answer uses it: it is in the chat payload's fenced
    #    memory block and named in the chat call's context report.
    conversation = Conversation.objects.create(workspace=personal, created_by=owner)
    calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        events = _sse(
            web.post(
                f"/api/v1/ai/conversations/{conversation.id}/messages",
                {"content": "Commit mesajlarımı nasıl yazmayı seviyorum?"},
                format="json",
            )
        )
    assert events[-1]["type"] == "done"
    chat_messages = json.loads(next(m for p, m in calls if p == "chat"))
    chat_payload = "\n".join(str(m["content"]) for m in chat_messages)
    assert memory.content in chat_payload
    assert SECRET not in chat_payload
    assert FIRM_COMMIT not in chat_payload
    chat_usage = ModelUsage.objects.filter(purpose="chat").latest("created_at")
    assert str(memory.id) in chat_usage.context_report["memory_ids"]
