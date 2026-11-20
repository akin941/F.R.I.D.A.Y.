"""Phase 8A branch 4 "done when": with all four sources enabled, one day of
fixture activity -- commits in a personal and a firm repo, a harness job, a
hook event and calendar events -- produces scrubbed episodes in the right
workspaces, and the audit counters match. Local fixture git repos, the fake
Google bridge, mocked runner calls; no network."""

from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from connectors import ingestion as calendar_ingestion
from connectors.bridges import google
from connectors.models import ConnectorBinding
from connectors.tests import google_fake
from connectors.tests.google_fake import FakeGoogle
from core.channels import CHANNEL_WEB
from harness import ingestion as harness_ingestion
from harness.models import HarnessJob, HarnessRepo
from harness.tests.test_commit_ingestion import FixtureRepo
from memory.ingestion.ports import record_episode
from memory.ingestion.services import ensure_default_sources
from memory.models import Episode, IngestionCounter, IngestionSource, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule
from workspaces.models import Workspace
from workspaces.services import create_firm_workspace

pytestmark = pytest.mark.django_db

OWNER_EMAIL = "owner@test.dev"
SECRET = "AKIA" + "IOSFODNN7EXAMPLE"
Rule = WorkspaceMappingRule.RuleType


@pytest.fixture
def world(tmp_path, settings, monkeypatch):
    settings.MEMORY_OWNER_GIT_EMAILS = [OWNER_EMAIL]
    owner = register_user(email=OWNER_EMAIL, password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    firm = create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)
    ensure_default_sources(workspace=personal, created_by=owner)
    sources = {s.kind: s for s in IngestionSource.objects.filter(workspace=personal)}
    IngestionSource.objects.filter(workspace=personal).update(enabled=True)

    personal_git = FixtureRepo(tmp_path / "personal")
    firm_git = FixtureRepo(tmp_path / "firm")
    friday = HarnessRepo.objects.create(
        workspace=personal, name="friday", git_url=personal_git.url, enabled=True
    )
    acme = HarnessRepo.objects.create(
        workspace=firm, name="acme", git_url=firm_git.url, enabled=True, sensitive=True
    )
    for kind in (IngestionSource.Kind.GIT, IngestionSource.Kind.HARNESS):
        add_rule(sources[kind], Rule.REPO, str(friday.id), personal)
        add_rule(sources[kind], Rule.REPO, str(acme.id), firm)
    add_rule(sources["claude_code_hooks"], Rule.PATH_PREFIX, "~/code/acme", firm)

    binding = ConnectorBinding.objects.create(
        workspace=personal,
        connector="google",
        enabled=True,
        last_health={"tools": {"calendar_agenda": "ok"}},
    )
    add_rule(sources["calendar"], Rule.CALENDAR_BINDING, str(binding.id), personal)
    fake = FakeGoogle(
        events=[
            {
                "id": "ev1",
                "summary": "Haftalık plan",
                "location": "Ev",
                "start": {"dateTime": "2026-11-19T09:00:00+03:00"},
                "end": {"dateTime": "2026-11-19T09:30:00+03:00"},
                "organizer": {"self": True},
            }
        ]
    )
    monkeypatch.setenv(google.ENV_CLIENT_ID, google_fake.CLIENT_ID)
    monkeypatch.setenv(google.ENV_CLIENT_SECRET, google_fake.CLIENT_SECRET_CANARY)
    monkeypatch.setenv(google.ENV_REFRESH_TOKEN, google_fake.REFRESH_TOKEN_CANARY)
    monkeypatch.setattr(google, "_transport", fake.transport())
    google.clear_token_cache()
    yield {
        "owner": owner,
        "personal": personal,
        "firm": firm,
        "sources": sources,
        "personal_git": personal_git,
        "firm_git": firm_git,
        "friday": friday,
        "acme": acme,
    }
    google.clear_token_cache()


def _stored(source):
    row = IngestionCounter.objects.filter(source=source, day=timezone.localdate()).first()
    return row.stored if row else 0


def test_one_fixture_day_lands_scrubbed_in_the_right_workspaces(world):
    w = world
    w["personal_git"].commit(f"Tune the scrubber, key was {SECRET}", ago=timedelta(hours=5))
    w["personal_git"].commit(
        "feat: agent work\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
        ago=timedelta(hours=4),
    )
    w["firm_git"].commit("Acme invoice export", ago=timedelta(hours=3))

    job = HarnessJob.objects.create(
        workspace=w["personal"],
        repo=w["friday"],
        goal="Neden embedding kuyruğu takılıyor?",
        created_by=w["owner"],
        status=HarnessJob.Status.FAILED,
    )
    harness_ingestion.record_discovery_outcome(job)
    record_episode(
        source="claude_code_hooks",
        kind="hook.notification",
        external_id="hook:s1:1",
        occurred_at=timezone.now(),
        mapping_key="path:~/code/acme/api",
        context_text="Claude needs your permission",
    )
    assert harness_ingestion.ingest_repo_commits()["results"] == {"stored": 3}
    assert calendar_ingestion.ingest_calendar_metadata()["results"] == {"stored": 1}

    personal, firm, sources = w["personal"], w["firm"], w["sources"]
    by_kind = {}
    for episode in Episode.objects.all():
        by_kind.setdefault(episode.kind, []).append(episode)
    commits = {e.metadata["repo"]: e for e in by_kind["git.commit"] if e.owner_text}
    assert commits["friday"].workspace_id == personal.id
    assert "[SECRET:" in commits["friday"].owner_text
    assert commits["acme"].workspace_id == firm.id
    assert commits["acme"].sensitivity == "private"
    assert len(by_kind["git.commit"]) == 3  # the agent commit kept metadata only
    assert by_kind["harness.discovery"][0].workspace_id == personal.id
    assert by_kind["hook.notification"][0].workspace_id == firm.id
    assert by_kind["calendar.event"][0].workspace_id == personal.id
    assert by_kind["calendar.event"][0].owner_text == "Haftalık plan"

    # Counters match the rows, per source.
    for kind, expected in (("git", 3), ("harness", 1), ("claude_code_hooks", 1), ("calendar", 1)):
        assert _stored(sources[kind]) == expected
        assert Episode.objects.filter(source=sources[kind]).count() == expected

    # No raw secret anywhere in the episode table.
    with connection.cursor() as cursor:
        cursor.execute("SELECT owner_text, context_text, metadata::text FROM memory_episode")
        assert SECRET not in str(cursor.fetchall())
    # Firm data never in the personal workspace.
    assert not Episode.objects.filter(workspace=personal, metadata__repo="acme").exists()


def test_a_paused_day_ingests_nothing(world):
    IngestionSource.objects.filter(workspace=world["personal"]).update(paused_at=timezone.now())
    world["personal_git"].commit("x")
    with mock.patch("harness.runner.commit_log", side_effect=AssertionError("cloned")):
        harness_ingestion.ingest_repo_commits()
    calendar_ingestion.ingest_calendar_metadata()
    assert not Episode.all_objects.exists()
