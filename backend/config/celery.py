import os

from celery import Celery

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
    "knowledge-ingestion-recovery-scan": {
        "task": "knowledge.tasks.recover_stale_knowledge_documents",
        "schedule": 60.0,
    },
    "approval-request-expiry-sweep": {
        "task": "approvals.tasks.sweep_expired_approval_requests",
        "schedule": 60.0,
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
