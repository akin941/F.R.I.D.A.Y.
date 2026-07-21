import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("jarvis")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# MemoryExtractionJob recovery scan (MVP 2 §4): a Celery periodic task, not a
# separate beat service. The compose worker runs embedded beat (`-B`) —
# see deploy/docker/compose.yml. Safe ONLY while there is exactly one worker
# container (the current and foreseeable dev/CI topology): embedded beat
# would double-schedule if more than one worker process ran `-B`. Scaling to
# multiple workers requires splitting beat into its own service first.
app.conf.beat_schedule = {
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
}
