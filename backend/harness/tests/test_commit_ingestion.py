"""Phase 8A branch 4 (brief D5): the git-commit producer -- the runner's
`commit_log` primitive against a local fixture repo built in `tmp_path`
(`file://`, no remote), the owner-identity and agent-trailer rules, the
cursor, pause semantics and the runner-beat wiring. No real git remote."""

import os
import subprocess
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from harness import ingestion, runner
from harness.models import HarnessImplementationJob, HarnessRepo
from memory.ingestion.services import ensure_default_sources
from memory.models import Episode, IngestionCounter, IngestionSource, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
OWNER_EMAIL = "owner@test.dev"
REPO_RULE = WorkspaceMappingRule.RuleType.REPO
FAKE_KEY = "sk-" + "ant-api03-NEVERSTORED0123456789abcdefXYZ"
_GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


# --- fixture repo ---------------------------------------------------------------


class FixtureRepo:
    def __init__(self, path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.counter = 0
        self._git("init", "-q", "-b", "main")

    @property
    def url(self):
        return f"file://{self.path}"

    def _git(self, *args, env=None):
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
            env={**os.environ, **_GIT_ENV, **(env or {})},
            text=True,
        ).stdout.strip()

    def commit(self, message, *, email=OWNER_EMAIL, ago=timedelta(hours=3), lines=2):
        self.counter += 1
        (self.path / f"file{self.counter}.txt").write_text(
            "".join(f"secret-ish content {i}\n" for i in range(lines))
        )
        self._git("add", "-A")
        when = (timezone.now() - ago).isoformat()
        self._git(
            "-c",
            "user.name=Someone",
            "-c",
            f"user.email={email}",
            "commit",
            "-q",
            "-m",
            message,
            env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
        )
        return self._git("rev-parse", "HEAD")

    def merge(self, branch, message, *, ago=timedelta(hours=1)):
        when = (timezone.now() - ago).isoformat()
        self._git(
            "-c",
            "user.name=Someone",
            "-c",
            f"user.email={OWNER_EMAIL}",
            "merge",
            "-q",
            "--no-ff",
            "-m",
            message,
            branch,
            env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
        )
        return self._git("rev-parse", "HEAD")


@pytest.fixture
def fixture_repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return FixtureRepo(path)


# --- DB fixtures -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def owner_emails(settings):
    settings.MEMORY_OWNER_GIT_EMAILS = [OWNER_EMAIL]


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
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def git_source(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    source = IngestionSource.objects.get(workspace=personal, kind=IngestionSource.Kind.GIT)
    source.enabled = True
    source.save()
    return source


def make_repo(workspace, fixture_repo, *, sensitive=False, name="friday"):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name=name,
        git_url=fixture_repo.url,
        default_branch="main",
        enabled=True,
        sensitive=sensitive,
    )


@pytest.fixture
def repo(personal, fixture_repo, git_source):
    repo = make_repo(personal, fixture_repo)
    add_rule(git_source, REPO_RULE, str(repo.id), personal)
    return repo


def _episodes():
    return {e.metadata["sha"]: e for e in Episode.objects.filter(kind="git.commit")}


def _counter(source):
    return IngestionCounter.objects.get(source=source, day=timezone.localdate())


@pytest.fixture
def git_calls(monkeypatch):
    """Every subprocess argv the runner issues (the real git still runs)."""
    calls = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", spy)
    return calls


# --- the runner primitive --------------------------------------------------------


def test_commit_log_reads_messages_and_counts_never_diffs(fixture_repo, git_calls):
    first = fixture_repo.commit("first change\n\nwith a body line", lines=3)
    second = fixture_repo.commit("second change", ago=timedelta(hours=1), lines=1)

    commits = runner.commit_log(
        git_url=fixture_repo.url, branch="main", since=timezone.now() - timedelta(days=1)
    )

    assert [c["sha"] for c in commits] == [second, first]  # newest first
    assert commits[1]["message"] == "first change\n\nwith a body line"
    assert commits[1]["author_email"] == OWNER_EMAIL
    assert commits[1]["files_changed"] == 1
    assert commits[1]["insertions"] == 3
    assert commits[1]["deletions"] == 0
    assert commits[0]["parents"] == [first]
    # No diff, patch, file list or file content is ever requested.
    flat = [arg for argv in git_calls for arg in argv]
    for forbidden in ("-p", "-u", "--patch", "--stat", "--numstat", "--name-only", "show", "diff"):
        assert forbidden not in flat
    assert not any(a.startswith("--name-status") for a in flat)
    clone = next(argv for argv in git_calls if "clone" in argv)
    assert "--filter=blob:none" in clone
    assert "--no-checkout" in clone
    assert any(a.startswith("--shallow-since=") for a in clone)
    assert clone[clone.index("--") + 1] == fixture_repo.url
    logs = [argv for argv in git_calls if "log" in argv]
    assert len(logs) == 2
    assert any("--shortstat" in argv for argv in logs)
    # The temporary clone is gone.
    clone_dir = clone[-1]
    assert not os.path.exists(clone_dir)


def test_commit_log_empty_window_is_not_an_error(fixture_repo):
    fixture_repo.commit("old", ago=timedelta(days=10))
    assert (
        runner.commit_log(
            git_url=fixture_repo.url, branch="main", since=timezone.now() - timedelta(days=1)
        )
        == []
    )


def test_commit_log_unreachable_remote_raises_runner_error(tmp_path):
    with pytest.raises(runner.RunnerError):
        runner.commit_log(
            git_url=f"file://{tmp_path}/missing",
            branch="main",
            since=timezone.now() - timedelta(days=1),
        )


def test_parse_commit_messages_cannot_be_forged_by_a_message():
    sha = "a" * 40
    forged = "b" * 40
    output = (
        f"{sha}\n\n{OWNER_EMAIL}\n2026-11-19T10:00:00+00:00\n2026-11-19T10:00:00+00:00\n"
        f"subject\n\n{forged}\n\nother@x.dev\n2026-11-19T10:00:00+00:00\n\x00"
    )
    commits = runner.parse_commit_messages(output)
    assert [c["sha"] for c in commits] == [sha]
    assert forged in commits[0]["message"]


# --- authorship rules ------------------------------------------------------------


def test_owner_commit_without_trailer_is_owner_text(repo, fixture_repo, git_source):
    sha = fixture_repo.commit("Rework the scrubber budget\n\nWhy: hostile input was slow.")
    ingestion.ingest_repo_commits()
    episode = _episodes()[sha]
    assert episode.owner_text == "Rework the scrubber budget\n\nWhy: hostile input was slow."
    assert episode.context_text == ""
    assert episode.metadata["owner_authored"] is True
    assert episode.metadata["authorship"] == "owner"
    assert episode.metadata["files_changed"] == 1
    assert episode.metadata["insertions"] == 2
    assert episode.workspace_id == repo.workspace_id
    assert episode.sensitivity == "normal"
    assert episode.source_id == git_source.id


@pytest.mark.parametrize(
    "message",
    [
        "feat: x\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
        "fix: y\n\nco-authored-by: Codex <codex@openai.com>",
        "docs: z\n\nClaude-Session: https://claude.ai/code/session_x",
        "chore: w\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ],
)
def test_agent_trailer_keeps_metadata_only(repo, fixture_repo, message):
    sha = fixture_repo.commit(message)
    ingestion.ingest_repo_commits()
    episode = _episodes()[sha]
    assert episode.owner_text == ""
    assert episode.context_text == ""
    assert episode.metadata["authorship"] == "agent_trailer"
    assert episode.metadata["owner_authored"] is False


def test_foreign_author_keeps_metadata_only(repo, fixture_repo):
    sha = fixture_repo.commit("someone else's words", email="colleague@example.com")
    ingestion.ingest_repo_commits()
    episode = _episodes()[sha]
    assert episode.owner_text == ""
    assert episode.metadata["authorship"] == "foreign_author"
    # The author address is never stored.
    assert "colleague@example.com" not in str(episode.metadata)


def test_harness_branch_and_produced_commits_keep_metadata_only(repo, fixture_repo, personal):
    named = fixture_repo.commit("Merge harness/p3-0b5e1c2a-1111-2222-3333-444455556666 work")
    produced = fixture_repo.commit("Plain-looking message", ago=timedelta(hours=2))
    HarnessImplementationJob.objects.create(
        workspace=personal, repo=repo, goal="g", produced_commit_sha=produced
    )
    ingestion.ingest_repo_commits()
    episodes = _episodes()
    assert episodes[named].metadata["authorship"] == "harness_branch"
    assert episodes[produced].metadata["authorship"] == "harness_branch"
    assert episodes[named].owner_text == episodes[produced].owner_text == ""


def test_merge_commit_keeps_metadata_only(repo, fixture_repo):
    fixture_repo.commit("base", ago=timedelta(hours=5))
    fixture_repo._git("checkout", "-q", "-b", "topic")
    fixture_repo.commit("topic work", ago=timedelta(hours=4))
    fixture_repo._git("checkout", "-q", "main")
    merge = fixture_repo.merge("topic", "Merge pull request #7 from me/topic")
    ingestion.ingest_repo_commits()
    episodes = _episodes()
    assert episodes[merge].metadata["authorship"] == "merge"
    assert episodes[merge].owner_text == ""
    assert len(episodes) == 3


def test_sensitive_repo_episodes_are_private(personal, fixture_repo, git_source):
    sensitive = make_repo(personal, fixture_repo, sensitive=True, name="client-x")
    add_rule(git_source, REPO_RULE, str(sensitive.id), personal)
    sha = fixture_repo.commit("client work")
    ingestion.ingest_repo_commits()
    assert _episodes()[sha].sensitivity == "private"


def test_secrets_in_a_commit_message_never_reach_the_db(repo, fixture_repo):
    fixture_repo.commit(f"oops pasted a key {FAKE_KEY}")
    ingestion.ingest_repo_commits()
    with connection.cursor() as cursor:
        cursor.execute("SELECT owner_text, context_text, metadata::text FROM memory_episode")
        rows = cursor.fetchall()
    assert rows
    assert FAKE_KEY not in str(rows)
    assert "[SECRET:" in rows[0][0]


# --- mapping ---------------------------------------------------------------------


def test_firm_repo_lands_only_in_the_firm_workspace(firm, personal, fixture_repo, git_source):
    firm_repo = make_repo(firm, fixture_repo, name="acme-app")
    add_rule(git_source, REPO_RULE, str(firm_repo.id), firm)
    sha = fixture_repo.commit("firm change")
    ingestion.ingest_repo_commits()
    episode = Episode.objects.get(external_id=f"commit:{firm_repo.id}:{sha}")
    assert episode.workspace_id == firm.id
    assert not Episode.objects.filter(workspace=personal).exists()


def test_unmapped_repo_stores_nothing_and_counts(personal, fixture_repo, git_source):
    make_repo(personal, fixture_repo)  # no rule
    fixture_repo.commit("one")
    fixture_repo.commit("two", ago=timedelta(hours=2))
    ingestion.ingest_repo_commits()
    assert not Episode.all_objects.exists()
    assert _counter(git_source).dropped_unmapped == 2


# --- cursor and idempotency ------------------------------------------------------


def test_cursor_advances_and_reruns_are_idempotent(repo, fixture_repo, git_source):
    first = fixture_repo.commit("first", ago=timedelta(hours=4))
    second = fixture_repo.commit("second", ago=timedelta(hours=2))
    result = ingestion.ingest_repo_commits()
    assert result["results"] == {"stored": 2}
    repo.refresh_from_db()
    assert repo.last_ingested_sha == second
    assert repo.last_ingested_at is not None

    # Nothing new: the cursor's ancestry is skipped, nothing re-recorded.
    assert ingestion.ingest_repo_commits()["results"] == {}
    assert _counter(git_source).duplicate == 0

    third = fixture_repo.commit("third", ago=timedelta(minutes=30))
    assert ingestion.ingest_repo_commits()["results"] == {"stored": 1}
    assert set(_episodes()) == {first, second, third}
    assert _counter(git_source).stored == 3
    repo.refresh_from_db()
    assert repo.last_ingested_sha == third


def test_lost_cursor_falls_back_to_port_dedupe(repo, fixture_repo, git_source):
    fixture_repo.commit("first")
    ingestion.ingest_repo_commits()
    HarnessRepo.objects.filter(pk=repo.pk).update(last_ingested_sha="f" * 40)
    assert ingestion.ingest_repo_commits()["results"] == {"duplicate": 1}
    assert Episode.objects.count() == 1


def test_paused_source_clones_nothing_and_never_backfills(
    repo, fixture_repo, git_source, monkeypatch
):
    fixture_repo.commit("during pause", ago=timedelta(hours=2))
    git_source.paused_at = timezone.now()
    git_source.save()

    def boom(**kwargs):
        raise AssertionError("no clone while paused")

    monkeypatch.setattr(runner, "commit_log", boom)
    assert ingestion.ingest_repo_commits()["repos"] == 0
    repo.refresh_from_db()
    assert repo.last_ingested_sha == ""
    paused_until = repo.last_ingested_at
    assert paused_until is not None

    seen = {}

    def fake_log(*, git_url, branch, since, clone_env):
        seen["since"] = since
        return []

    monkeypatch.setattr(runner, "commit_log", fake_log)
    git_source.paused_at = None
    git_source.save()
    ingestion.ingest_repo_commits()
    # The window starts exactly at the end of the pause (no overlap).
    assert seen["since"] == paused_until
    assert not Episode.all_objects.exists()


def test_one_failing_repo_never_stops_the_others(personal, fixture_repo, git_source, monkeypatch):
    broken = make_repo(personal, fixture_repo, name="broken")
    good = make_repo(personal, fixture_repo, name="good")
    add_rule(git_source, REPO_RULE, str(good.id), personal)
    add_rule(git_source, REPO_RULE, str(broken.id), personal)
    fixture_repo.commit("x")
    real = runner.commit_log

    def flaky(**kwargs):
        if kwargs["git_url"] == broken.git_url and flaky.first:
            flaky.first = False
            raise runner.RunnerError("clone failed")
        return real(**kwargs)

    flaky.first = True
    monkeypatch.setattr(runner, "commit_log", flaky)
    result = ingestion.ingest_repo_commits()
    assert result["failed"] == 1
    assert result["repos"] == 1
    assert Episode.objects.count() == 1


def test_disabled_repos_are_not_read(personal, fixture_repo, git_source, monkeypatch):
    disabled = make_repo(personal, fixture_repo)
    HarnessRepo.objects.filter(pk=disabled.pk).update(enabled=False)
    monkeypatch.setattr(runner, "commit_log", lambda **kw: pytest.fail("read a disabled repo"))
    assert ingestion.ingest_repo_commits()["repos"] == 0


def test_a_port_failure_is_counted_not_raised(repo, fixture_repo, monkeypatch):
    fixture_repo.commit("x")

    def explode(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ingestion.ports, "record_episode", explode)
    result = ingestion.ingest_repo_commits()
    assert result["results"] == {"error": 1}


# --- wiring ----------------------------------------------------------------------


def test_task_wrapper_calls_the_producer(monkeypatch):
    from harness import tasks

    monkeypatch.setattr(ingestion, "ingest_repo_commits", lambda: {"repos": 7})
    assert tasks.ingest_repo_commits() == {"repos": 7}
