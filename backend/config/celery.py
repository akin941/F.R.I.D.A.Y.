import os

from celery import Celery
from celery.schedules import crontab

# Production by default (Phase 4 branch 1): a server started without an
# explicit DJANGO_SETTINGS_MODULE fails hard on a missing environment
# (config/settings/prod.py) instead of booting with DEBUG=True. Every dev
# path names config.settings.dev explicitly (compose, Makefile).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

app = Celery("friday")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# MemoryExtractionJob recovery scan (MVP 2 §4): a Celery periodic task, not a
# separate beat service. The compose worker runs embedded beat (`-B`) —
# see deploy/docker/compose.yml. Safe ONLY while there is exactly one worker
# container (the current and foreseeable dev/CI topology): embedded beat
# would double-schedule if more than one worker process ran `-B`. Scaling to
# multiple workers requires splitting beat into its own service first.
_BEAT_SCHEDULE = {
    "memory-extraction-recovery-scan": {
        "task": "memory.tasks.recover_stale_extraction_jobs",
        "schedule": 60.0,
    },
    "memory-embedding-recovery-scan": {
        "task": "memory.tasks.recover_stale_memory_embeddings",
        "schedule": 60.0,
    },
    # Phase 8B branch 4 (D8): retrievable facts only.
    "memory-fact-embedding-recovery-scan": {
        "task": "memory.tasks.recover_stale_fact_embeddings",
        "schedule": 60.0,
    },
    "knowledge-ingestion-recovery-scan": {
        "task": "knowledge.tasks.recover_stale_knowledge_documents",
        "schedule": 60.0,
    },
    # Every minute (Phase 10 D6: a `node_action` approval lives 10 minutes),
    # still only a backstop to the decision-time check.
    "approval-request-expiry-sweep": {
        "task": "approvals.tasks.sweep_expired_approval_requests",
        "schedule": 60.0,
    },
    # Phase 10 D6/D7: stale dispatches and node actions whose gateway is
    # gone end terminally (never retried); scrubbed tails are cleared after
    # NODE_ACTION_TAIL_RETENTION_DAYS.
    "node-action-sweep": {
        "task": "nodes.tasks.sweep_node_actions_task",
        "schedule": 60.0,
    },
    "node-action-tail-retention": {
        "task": "nodes.tasks.clear_node_action_tails_task",
        "schedule": 24 * 60 * 60.0,
    },
    "vault-export-recovery-scan": {
        "task": "tools.tasks.recover_stale_vault_export_runs",
        "schedule": 60.0,
    },
    "agent-execution-recovery-scan": {
        "task": "agents.tasks.recover_stale_agents",
        "schedule": 60.0,
    },
    "telegram-link-code-expiry-sweep": {
        "task": "integrations.tasks.sweep_expired_link_codes",
        "schedule": 60.0,
    },
    "harness-job-recovery-scan": {
        "task": "harness.tasks.recover_stale_harness_jobs",
        "schedule": 60.0,
    },
    "harness-implementation-job-recovery-scan": {
        "task": "harness.tasks.recover_stale_harness_implementation_jobs",
        "schedule": 60.0,
    },
    "harness-review-recovery-scan": {
        "task": "harness.tasks.recover_stale_harness_reviews",
        "schedule": 60.0,
    },
    # Connectors (Phase 5 branch 4, brief D3/D6): lost-dispatch / lost-worker
    # recovery for gated writes (never a re-send), and the 15-minute pin beat.
    "connector-write-recovery-scan": {
        "task": "connectors.tasks.recover_stale_connector_writes",
        "schedule": 60.0,
    },
    "connector-pin-check": {
        "task": "connectors.tasks.check_connector_pins",
        "schedule": 15 * 60.0,
    },
    # Phase 5 branch 6 (brief D10): lost deploy runs; runner beat only.
    "deploy-run-recovery-scan": {
        "task": "deploys.tasks.recover_stale_deploy_runs",
        "schedule": 60.0,
    },
    "jwt-flush-expired-tokens": {
        "task": "accounts.tasks.flush_expired_tokens",
        "schedule": 24 * 60 * 60.0,
    },
    # ADR-009: trashed rows are purged TRASH_RETENTION_DAYS after deletion.
    "trash-retention-purge-tasks": {
        "task": "tasks.tasks.purge_expired_trashed_tasks_task",
        "schedule": 24 * 60 * 60.0,
    },
    "trash-retention-purge-documents": {
        "task": "knowledge.tasks.purge_expired_trashed_documents_task",
        "schedule": 24 * 60 * 60.0,
    },
    # Phase 8A D8: re-assemble the deterministic `self` core block (no
    # model call; a new version only when its sources changed).
    "assistant-self-block-assembly": {
        "task": "assistant.tasks.assemble_self_block_task",
        "schedule": 24 * 60 * 60.0,
    },
    # Phase 8A (ADR-012 decision 4, d10): raw episodes past their TTL
    # (30 days personal, 14 days firm) are hard-deleted daily.
    "memory-episode-ttl-purge": {
        "task": "memory.ingestion.tasks.purge_expired_episodes_task",
        "schedule": 24 * 60 * 60.0,
    },
    # Phase 8A branch 4 (brief D5): the git-commit producer clones with the
    # runner's credentials, so it runs on the runner beat (`harness.tasks.`
    # prefix below); the calendar producer reads Google from the compose
    # worker like every connector read. Both are no-ops while their
    # ingestion source is disabled or paused.
    "memory-ingest-git-commits": {
        "task": "harness.tasks.ingest_repo_commits",
        "schedule": 6 * 60 * 60.0,
    },
    # Phase 8A branch 5 (brief D9/D10): the nightly learning run (consolidate
    # leftovers, then plan owner-text extraction jobs) and the extraction
    # jobs' lease-recovery scan.
    "memory-learning-nightly": {
        "task": "memory.ingestion.tasks.run_learning_night_task",
        "schedule": 24 * 60 * 60.0,
    },
    # Phase 8B D5: the weekly reflection, Sunday 03:30 in the app timezone
    # (Celery's Django fixup takes TIME_ZONE, Europe/Istanbul).
    "memory-learning-weekly-reflection": {
        "task": "memory.ingestion.tasks.run_weekly_reflection_task",
        "schedule": crontab(minute=30, hour=3, day_of_week=0),
    },
    "memory-episode-extraction-recovery-scan": {
        "task": "memory.ingestion.tasks.recover_stale_episode_extraction_jobs",
        "schedule": 60.0,
    },
    # Phase 8B branch 5 (brief D10): lost or lease-expired Graphify runs.
    # The runs themselves start from the git producer above (a new
    # default-branch head); both live on the runner beat.
    "harness-graphify-recovery-scan": {
        "task": "harness.tasks.recover_stale_graphify_runs",
        "schedule": 60.0,
    },
    "memory-ingest-calendar-metadata": {
        "task": "connectors.tasks.ingest_calendar_metadata",
        "schedule": 6 * 60 * 60.0,
    },
    # Phase 8B branch 8 (brief D13): the transcript pull through the owner's
    # node, on the compose worker beat (`tools.` prefix). A no-op while both
    # transcript sources are disabled or no node is reachable.
    "memory-pull-transcripts": {
        "task": "tools.tasks.pull_transcripts_task",
        "schedule": float(os.environ.get("MEMORY_TRANSCRIPT_PULL_MINUTES") or 15) * 60.0,
    },
    # Phase 6 A5: voice turn traces (timings only) live VOICE_TRACE_RETENTION_DAYS.
    "voice-turn-trace-prune": {
        "task": "voice.tasks.prune_voice_turn_traces_task",
        "schedule": 24 * 60 * 60.0,
    },
    # Phase 12 (brief D3, ADR-016): the one runner of owner schedules. The
    # schedules themselves are rows only the owner's web views write; this
    # entry and the prune below are the only proactive beat literals, and no
    # task, tool or model writes the beat schedule.
    "proactive-heartbeat": {
        "task": "proactive.tasks.heartbeat",
        "schedule": 60.0,
    },
    # Phase 12 D5: read/dismissed notices past PROACTIVE_NOTICE_RETENTION_DAYS.
    "proactive-notice-prune": {
        "task": "proactive.tasks.prune",
        "schedule": 24 * 60 * 60.0,
    },
}

# Harness recovery scans (Phase 3 branch 8, review P1): the scans tear down
# containers and clean up worktrees that exist only on the runner host, so
# they run on the runner's own embedded beat (`make harness-runner` sets
# HARNESS_RUNNER_BEAT=true), never on the compose worker's beat. With no
# runner up, nothing is published -- no scan messages pile up in Redis for a
# queue nobody consumes -- and nothing needs recovering, because no harness
# job can make progress without a runner anyway; the scans resume when the
# runner starts. The deploy recovery scan (Phase 5 branch 6) follows the same
# rule: deploy runs execute only on the runner (`-Q harness,deploy`), the one
# host holding the deploy SSH key.
_RUNNER_SCAN_PREFIXES = ("harness.tasks.", "deploys.tasks.")


def build_beat_schedule(*, runner_mode):
    return {
        name: entry
        for name, entry in _BEAT_SCHEDULE.items()
        if entry["task"].startswith(_RUNNER_SCAN_PREFIXES) == runner_mode
    }


app.conf.beat_schedule = build_beat_schedule(
    runner_mode=os.environ.get("HARNESS_RUNNER_BEAT", "").lower() == "true"
)
# Both beats' schedules, read-only, for `manage.py preflight`'s
# `learning_beat` check (Phase 8A closeout): `ops` may not import `config`,
# and one process only runs one of the two beats.
app.conf.friday_beat_schedules = {
    "worker": build_beat_schedule(runner_mode=False),
    "runner": build_beat_schedule(runner_mode=True),
}
