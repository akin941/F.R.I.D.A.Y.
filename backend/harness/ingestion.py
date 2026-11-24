"""Harness-side producers for the Phase 8A ingestion port (brief D5,
branch 4). `memory` may not import `harness`, so harness pushes its own
items through `memory.ingestion.ports.record_episode` -- exactly like
`record_work_log` -- and decides which of its text is owner-authored.

Two producers live here:

- **Git commits** (`ingest_repo_commits`, a runner-beat task: it needs the
  runner's clone credentials). For every enabled `HarnessRepo` it reads the
  commits since the repo's cursor through `runner.commit_log` (blob-less,
  `--shallow-since`, messages + `--shortstat` counts only -- never a diff
  or file content) and records one episode per commit. The commit message
  is `owner_text` **only** when the author e-mail is in
  `MEMORY_OWNER_GIT_EMAILS`, the message has no agent trailer
  (`Co-Authored-By: Claude...`, Codex, `Claude-Session:`, "Generated with
  Claude Code"), it is not a merge commit (machine-written subject) and it
  is not a harness-produced commit (a `harness/pN-...` branch named in the
  message, or a `produced_commit_sha` of one of the repo's implementation
  jobs). Every other commit keeps metadata only. A `sensitive` repo's
  episodes are `private`.
- **Harness outcomes** (`record_discovery_outcome`,
  `record_implementation_outcome`), called at the terminal-state hooks
  beside `record_work_log`. `HarnessJob.goal` / the implementation goal is
  `owner_text` when the installation owner typed it (a human `is_owner`
  creator); the model-produced briefing / diff / review summary is
  `context_text`, bounded to 1000 chars. Failed and cancelled jobs produce
  an episode too, with their status.

Producers never raise into the harness state machine: an ingestion failure
is logged and swallowed (the port's own counters record what it saw), and
each write runs in its own savepoint so a database error cannot poison the
caller's transaction.
"""

import logging
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from harness import graphify_execution, runner
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo, HarnessReview
from memory.ingestion import ports
from memory.models import IngestionSource, Memory
from skills import ports as skills_ports

logger = logging.getLogger(__name__)

GIT = IngestionSource.Kind.GIT
HARNESS = IngestionSource.Kind.HARNESS

CONTEXT_SUMMARY_MAX_CHARS = 1000
# Later runs re-read a day before the cursor's committer date: a commit
# authored earlier but pushed (merged) after the last run still lands in the
# window. Commits the cursor already covers are skipped by ancestry, not
# re-recorded.
CURSOR_OVERLAP = timedelta(hours=24)

# Agent-written commits under the owner's identity (brief D5, R2). Matched
# case-insensitively against the whole message.
AGENT_TRAILER_PATTERNS = (
    re.compile(
        r"(?im)^\s*co-authored-by:.*\b(claude|anthropic|codex|openai|chatgpt|gpt|copilot|"
        r"cursor|devin|aider|gemini|jules)\b"
    ),
    re.compile(r"(?im)^\s*claude-session:"),
    re.compile(r"(?i)generated (with|by) \[?(claude|codex)"),
    re.compile(r"(?im)^\s*(generated-by|assisted-by|ai-agent):"),
)
# The harness's own write-mode branch names (`harness/p3-<job uuid>`).
HARNESS_BRANCH_PATTERN = re.compile(r"\bharness/p\d+-[0-9a-fA-F-]{8,}")

AUTHORSHIP_OWNER = "owner"
AUTHORSHIP_FOREIGN = "foreign_author"
AUTHORSHIP_AGENT = "agent_trailer"
AUTHORSHIP_HARNESS = "harness_branch"
AUTHORSHIP_MERGE = "merge"


def _safely(what, func, *args, **kwargs):
    """Run one producer write in its own savepoint; never raise."""
    try:
        with transaction.atomic():
            return func(*args, **kwargs)
    except Exception:
        logger.exception("Ingestion producer %s failed", what)
        return None


def _parse_when(value, fallback):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback
    if timezone.is_naive(parsed):
        return fallback
    return parsed


# --- git commits --------------------------------------------------------------


def commit_authorship(commit, *, owner_emails, harness_shas):
    """Why a commit's message is (or is not) owner-authored text (D5)."""
    if len(commit.get("parents") or []) > 1:
        return AUTHORSHIP_MERGE
    if commit.get("author_email", "").lower() not in owner_emails:
        return AUTHORSHIP_FOREIGN
    message = commit.get("message") or ""
    if any(pattern.search(message) for pattern in AGENT_TRAILER_PATTERNS):
        return AUTHORSHIP_AGENT
    if commit.get("sha") in harness_shas or HARNESS_BRANCH_PATTERN.search(message):
        return AUTHORSHIP_HARNESS
    return AUTHORSHIP_OWNER


def _covered_by_cursor(commits, cursor_sha):
    """The cursor commit and every ancestor of it present in this read --
    already handed to the port on an earlier run."""
    if not cursor_sha:
        return set()
    by_sha = {c["sha"]: c for c in commits}
    if cursor_sha not in by_sha:
        return set()
    covered, stack = set(), [cursor_sha]
    while stack:
        sha = stack.pop()
        if sha in covered or sha not in by_sha:
            continue
        covered.add(sha)
        stack.extend(by_sha[sha]["parents"])
    return covered


def _record_commit(repo, commit, authorship, now):
    owner_authored = authorship == AUTHORSHIP_OWNER
    return ports.record_episode(
        source=GIT,
        kind="git.commit",
        external_id=f"commit:{repo.id}:{commit['sha']}",
        occurred_at=_parse_when(commit.get("authored_at"), now),
        mapping_key=f"repo:{repo.id}",
        owner_text=commit.get("message", "") if owner_authored else "",
        metadata={
            "sha": commit["sha"],
            "repo_id": str(repo.id),
            "repo": repo.name,
            "branch": repo.default_branch,
            "authored_at": commit.get("authored_at", ""),
            "files_changed": int(commit.get("files_changed") or 0),
            "insertions": int(commit.get("insertions") or 0),
            "deletions": int(commit.get("deletions") or 0),
            "owner_authored": owner_authored,
            "authorship": authorship,
        },
        sensitivity=(Memory.Sensitivity.PRIVATE if repo.sensitive else Memory.Sensitivity.NORMAL),
    )


def ingest_repo(repo, *, now=None):
    """Read and record one repo's new commits; advance its cursor. Returns
    `{status: count}` of the port's results. Raises `runner.RunnerError`
    when the read fails (the cursor is left untouched)."""
    now = now or timezone.now()
    if repo.last_ingested_at is None:
        since = now - timedelta(days=settings.MEMORY_GIT_INGEST_LOOKBACK_DAYS)
    elif repo.last_ingested_sha:
        since = repo.last_ingested_at - CURSOR_OVERLAP
    else:
        # A cursor with no sha marks the end of a paused/disabled window:
        # nothing committed before it is ever read.
        since = repo.last_ingested_at
    commits = runner.commit_log(
        git_url=repo.git_url, branch=repo.default_branch, since=since, clone_env={}
    )
    covered = _covered_by_cursor(commits, repo.last_ingested_sha)
    owner_emails = {e.lower() for e in settings.MEMORY_OWNER_GIT_EMAILS}
    harness_shas = set(
        HarnessImplementationJob.objects.filter(repo=repo)
        .exclude(produced_commit_sha="")
        .values_list("produced_commit_sha", flat=True)
    )
    results = {}
    # Oldest first, so the episode order follows history.
    for commit in reversed(commits):
        if commit["sha"] in covered:
            continue
        authorship = commit_authorship(commit, owner_emails=owner_emails, harness_shas=harness_shas)
        result = _safely(
            f"git commit of repo {repo.id}", _record_commit, repo, commit, authorship, now
        )
        status = result.status if result is not None else "error"
        results[status] = results.get(status, 0) + 1
    if commits:
        tip = commits[0]
        HarnessRepo.objects.filter(pk=repo.pk).update(
            last_ingested_sha=tip["sha"],
            last_ingested_at=_parse_when(tip.get("committed_at"), now),
        )
    # Phase 8B branch 5 (brief D10): a new default-branch head starts one
    # Graphify run (with no commit in the window the head comes from the
    # remote). Never raises into this producer.
    _safely(
        f"graphify trigger of repo {repo.id}",
        graphify_execution.poll_graphify,
        repo,
        head_sha=commits[0]["sha"] if commits else None,
    )
    return results


def ingest_repo_commits(*, now=None):
    """The runner-beat entry point (`harness.tasks.ingest_repo_commits`).
    While the git source is disabled or paused nothing is cloned and every
    enabled repo's cursor moves to `now` without a sha, so re-enabling never
    back-fills the paused window (ADR-012: a paused source ingests nothing).
    One repo's failure never stops the others."""
    now = now or timezone.now()
    repos = HarnessRepo.objects.filter(enabled=True).order_by("created_at")
    if not ports.source_accepting(GIT):
        repos.update(last_ingested_at=now, last_ingested_sha="")
        # Graphify indexes code, not owner text: its trigger does not
        # depend on the git source (it asks the remote for the head).
        for repo in repos.filter(graphify_enabled=True):
            _safely(f"graphify trigger of repo {repo.id}", graphify_execution.poll_graphify, repo)
        return {"repos": 0, "failed": 0, "results": {}}
    totals, done, failed = {}, 0, 0
    for repo in repos:
        try:
            results = ingest_repo(repo, now=now)
        except Exception:
            logger.exception("Commit ingestion for harness repo %s failed", repo.pk)
            failed += 1
            continue
        done += 1
        for status, count in results.items():
            totals[status] = totals.get(status, 0) + count
    return {"repos": done, "failed": failed, "results": totals}


# --- harness outcomes ---------------------------------------------------------


def _owner_typed(job):
    """The goal is the owner's own words only when the installation owner
    (a human) created the job."""
    user = job.created_by
    return bool(user is not None and user.is_owner and not user.is_agent)


def _bounded(text, limit=CONTEXT_SUMMARY_MAX_CHARS):
    return (text or "").strip()[:limit]


def _goal_fields(job):
    if _owner_typed(job):
        return job.goal, ""
    # Someone else's (or an agent's) request: context only, never quotable.
    return "", f"Goal (not owner-authored): {_bounded(job.goal, 500)}"


def _join(*parts):
    return "\n".join(p for p in parts if p)


def _record_discovery(job_id, cost):
    job = HarnessJob.objects.select_related("repo", "created_by").filter(pk=job_id).first()
    terminal = (
        HarnessJob.Status.SUCCEEDED,
        HarnessJob.Status.FAILED,
        HarnessJob.Status.CANCELLED,
    )
    if job is None or job.status not in terminal:
        return None
    owner_text, goal_context = _goal_fields(job)
    summary = _bounded((job.briefing or {}).get("summary", ""))
    return ports.record_episode(
        source=HARNESS,
        kind="harness.discovery",
        external_id=f"harness:{job.id}",
        occurred_at=job.updated_at or timezone.now(),
        mapping_key=f"repo:{job.repo_id}",
        owner_text=owner_text,
        context_text=_join(goal_context, f"Briefing summary: {summary}" if summary else ""),
        metadata={
            "job_type": "discovery",
            "job_id": str(job.id),
            "status": job.status,
            "failure_reason": job.failure_reason,
            "agent": job.agent,
            "repo_id": str(job.repo_id),
            "repo": job.repo.name,
            "cost_usd": str(cost) if cost is not None else "",
            "duration_seconds": job.elapsed_seconds,
            "owner_authored_goal": bool(owner_text),
        },
        sensitivity=job.sensitivity,
    )


def record_discovery_outcome(job, *, cost=None):
    """One `harness` episode for a terminal discovery job. Never raises."""
    return _safely(f"harness discovery {job.pk}", _record_discovery, job.pk, cost)


def _record_implementation(job_id):
    job = (
        HarnessImplementationJob.objects.select_related("repo", "created_by")
        .filter(pk=job_id)
        .first()
    )
    terminal = (
        HarnessImplementationJob.Status.SUCCEEDED,
        HarnessImplementationJob.Status.FAILED,
        HarnessImplementationJob.Status.CANCELLED,
    )
    if job is None or job.status not in terminal:
        return None
    owner_text, goal_context = _goal_fields(job)
    review = job.reviews.exclude(summary="").order_by("-created_at").first()
    diff_summary = _bounded(job.diff_summary)
    review_summary = _bounded(review.summary) if review is not None else ""
    context = _join(
        goal_context,
        f"Change summary: {diff_summary}" if diff_summary else "",
        f"Review summary: {review_summary}" if review_summary else "",
    )
    return ports.record_episode(
        source=HARNESS,
        kind="harness.implementation",
        external_id=f"harness-impl:{job.id}",
        occurred_at=job.updated_at or timezone.now(),
        mapping_key=f"repo:{job.repo_id}",
        owner_text=owner_text,
        context_text=_bounded(context, 2 * CONTEXT_SUMMARY_MAX_CHARS),
        metadata={
            "job_type": "implementation",
            "job_id": str(job.id),
            "status": job.status,
            "failure_reason": job.failure_reason,
            "agent": job.agent,
            "repo_id": str(job.repo_id),
            "repo": job.repo.name,
            "branch": job.branch_name if job.status == job.Status.SUCCEEDED else "",
            "tests_passed": (job.test_result or {}).get("passed"),
            "review_verdict": review.verdict if review is not None else "",
            "duration_seconds": job.elapsed_seconds,
            "owner_authored_goal": bool(owner_text),
        },
        sensitivity=job.sensitivity,
    )


def _record_playbook(job_id):
    job = (
        HarnessImplementationJob.objects.select_related("repo", "created_by", "workspace")
        .filter(pk=job_id)
        .first()
    )
    if job is None or job.status != HarnessImplementationJob.Status.SUCCEEDED:
        return None
    # Phase 12 D17: only the owner's own goal is ever a playbook's quote.
    if not _owner_typed(job):
        return None
    # Sensitive repos stay out of playbooks entirely.
    if job.repo.sensitive or job.sensitivity == Memory.Sensitivity.PRIVATE:
        return None
    if (job.test_result or {}).get("passed") is not True:
        return None
    review = (
        job.reviews.filter(status=HarnessReview.Status.SUCCEEDED).order_by("-created_at").first()
    )
    if review is None or review.verdict != HarnessReview.Verdict.APPROVED:
        return None
    diff_summary = _bounded(job.diff_summary, 500)
    review_summary = _bounded(review.summary, 450)
    context = _join(
        f"Change summary: {diff_summary}" if diff_summary else "",
        f"Review summary: {review_summary}" if review_summary else "",
    )
    return skills_ports.record_playbook_source(
        workspace=job.workspace,
        job_id=job.id,
        repo_key=str(job.repo_id),
        repo_label=job.repo.name,
        goal=job.goal,
        # The job's snapshot of the owner-configured `HarnessRepo.test_command`.
        test_command=job.repo_test_command,
        # Paths only (never contents); `.claude/skills/` paths never feed a
        # playbook (D17 "Exclusion" (4)) -- the port drops them too.
        changed_paths=skills_ports.strip_skill_paths(job.changed_files),
        test_passed=True,
        exit_code=(job.test_result or {}).get("exit_code"),
        context_text=_bounded(context),
        sensitive=False,
    )


def record_playbook_outcome(job):
    """Phase 12 D17: one `skills` playbook source for a succeeded, approved,
    tested implementation job whose goal the owner typed (sensitive repos
    record nothing). Never raises; idempotent per job."""
    return _safely(f"playbook source {job.pk}", _record_playbook, job.pk)


def record_implementation_outcome(job):
    """One `harness` episode for a terminal implementation job. Never
    raises; idempotent through the port's `(source, external_id)` dedupe.
    Beside it, the Phase 12 playbook source (`record_playbook_outcome`)."""
    result = _safely(f"harness implementation {job.pk}", _record_implementation, job.pk)
    record_playbook_outcome(job)
    return result
