"""Owner-authored episode extraction with checked provenance (Phase 8A brief
D9, D13).

Nightly, per workspace: `plan_nightly_extraction` triages the workspace's
un-extracted live episodes deterministically (no model call: empty or short
`owner_text`, a duplicate fingerprint) and plans `EpisodeExtractionJob`s --
personal-class (calendar) and `private` episodes in their own jobs, sent with
`personal_data=True` and never batched with cloud-eligible ones.

A job (lease-fenced, the `MemoryExtractionJob` pattern) checks each source's
daily token budget before the call (episodes over it stay un-extracted for
the next night), scrubs every text a second time, and sends **only
`owner_text`** as quotable items; `context_text` and metadata travel inside
an `untrusted_context` fence. The reply is parsed strictly (schema v1, any
deviation is a terminal error), and a candidate survives only when its quote
is a verbatim substring of the cited episode's owner text in this job
(`memory.provenance`). Survivors wait on the job for the workspace's
consolidation (`memory.ingestion.consolidation`); nothing here writes a
memory or a block.
"""

import json
import logging
import math
import uuid
from collections import Counter, defaultdict
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.core_blocks import labels_for_workspace
from memory.ingestion import services
from memory.ingestion.scrubber import scrub_text, scrub_value
from memory.models import (
    CoreMemoryBlock,
    Episode,
    EpisodeExtractionJob,
    IngestionSource,
    Memory,
    compute_fingerprint,
)
from memory.provenance import normalise, quote_is_verbatim, stored_quote
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

PROMPT_NAME = "friday_episode_extraction"
SCHEMA_VERSION = 1
MAX_EPISODES_PER_JOB = 25
MAX_CANDIDATES = 50
MIN_OWNER_TEXT_CHARS = 20
MAX_TITLE_CHARS = 255
MAX_CONTENT_CHARS = 1000
MAX_QUOTE_CHARS = 1000
MAX_METADATA_CHARS = 1000
CLAIM_LEASE_SECONDS = 420
MAX_RETRIES = 3
PENDING_RECOVERY_GRACE_SECONDS = 60

MEMORY_TARGET = "memory"
# `self` is assembler-only (D8): a candidate targeting it is dropped and
# counted, never turned into a proposal.
_TARGETS = {MEMORY_TARGET, *CoreMemoryBlock.Label.values}
_TYPES = frozenset(Memory.Type.values) - {Memory.Type.WORK_LOG}
_TOP_KEYS = {"version", "candidates"}
_CANDIDATE_KEYS = {"type", "title", "content", "quote", "episode_id", "target"}
FENCE_WORDS = ("owner_item", "untrusted_context", "context_item")

_OPEN = (EpisodeExtractionJob.Status.PENDING, EpisodeExtractionJob.Status.RUNNING)


class EpisodeParseError(Exception):
    """The reply did not match schema v1. Terminal, never retried; the
    message never embeds model text."""


def parse_episode_candidates(raw_text):
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EpisodeParseError(f"invalid JSON: {exc.__class__.__name__}") from None
    if not isinstance(payload, dict) or set(payload) != _TOP_KEYS:
        raise EpisodeParseError("unexpected top-level shape")
    version = payload["version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise EpisodeParseError("unsupported schema version")
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise EpisodeParseError("'candidates' must be a list")
    if len(candidates) > MAX_CANDIDATES:
        raise EpisodeParseError(f"too many candidates (> {MAX_CANDIDATES})")
    parsed = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_KEYS:
            raise EpisodeParseError(f"candidate {index} has unexpected keys")
        for key in _CANDIDATE_KEYS:
            if not isinstance(candidate[key], str) or not candidate[key].strip():
                raise EpisodeParseError(f"candidate {index} has an empty/invalid {key}")
        if candidate["type"] not in _TYPES:
            raise EpisodeParseError(f"candidate {index} has an unrecognized type")
        if candidate["target"] not in _TARGETS:
            raise EpisodeParseError(f"candidate {index} has an unrecognized target")
        for key, cap in (
            ("title", MAX_TITLE_CHARS),
            ("content", MAX_CONTENT_CHARS),
            ("quote", MAX_QUOTE_CHARS),
            ("episode_id", 64),
        ):
            if len(candidate[key]) > cap:
                raise EpisodeParseError(f"candidate {index} {key} exceeds {cap} chars")
        parsed.append(dict(candidate))
    return parsed


def needs_personal_data(episode):
    """Personal-class source or `private` episode -> local providers only."""
    return episode.source.is_personal_class or episode.sensitivity == Episode.Sensitivity.PRIVATE


def _accepting_sources():
    return Q(source__enabled=True, source__paused_at__isnull=True)


# --- planning ----------------------------------------------------------------


def plan_nightly_extraction(now=None):
    """Plan tonight's jobs for every workspace with un-extracted live
    episodes of an accepting source. Returns the created job ids; each is
    dispatched once the planning transaction commits."""
    now = now or timezone.now()
    workspace_ids = list(
        Episode.objects.filter(extracted_at__isnull=True)
        .filter(_accepting_sources())
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    created = []
    for workspace_id in workspace_ids:
        created.extend(_plan_workspace(workspace_id, now))
    return created


def _plan_workspace(workspace_id, now):
    night = timezone.localdate(now)
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
        if EpisodeExtractionJob.objects.filter(workspace=workspace, status__in=_OPEN).exists():
            return []
        episodes = list(
            Episode.objects.filter(workspace=workspace, extracted_at__isnull=True)
            .filter(_accepting_sources())
            .select_related("source")
            .order_by("occurred_at", "id")
        )
        seen = {
            compute_fingerprint(normalise(text))
            for text in Episode.objects.filter(workspace=workspace, extracted_at__isnull=False)
            .exclude(owner_text="")
            .values_list("owner_text", flat=True)
        }
        skipped, drops = [], defaultdict(Counter)
        groups = {False: [], True: []}
        for episode in episodes:
            text = normalise(episode.owner_text)
            reason = None
            if not text:
                reason = "triage_no_owner_text"
            elif len(text) < MIN_OWNER_TEXT_CHARS:
                reason = "triage_short"
            else:
                fingerprint = compute_fingerprint(text)
                if fingerprint in seen:
                    reason = "triage_duplicate"
                seen.add(fingerprint)
            if reason:
                skipped.append(episode.id)
                drops[episode.source][reason] += 1
                continue
            groups[needs_personal_data(episode)].append(episode)
        if skipped:
            Episode.all_objects.filter(pk__in=skipped).update(extracted_at=now)
        for source, reasons in drops.items():
            services.record_learning_drops(source, reasons)
        job_ids = []
        for personal_data, members in groups.items():
            for start in range(0, len(members), MAX_EPISODES_PER_JOB):
                chunk = members[start : start + MAX_EPISODES_PER_JOB]
                job = EpisodeExtractionJob.objects.create(
                    workspace=workspace,
                    night=night,
                    personal_data=personal_data,
                    episode_ids=[str(e.id) for e in chunk],
                )
                job_ids.append(job.id)
        for job_id in job_ids:
            transaction.on_commit(lambda job_id=job_id: dispatch_episode_extraction_job(job_id))
    return job_ids


def dispatch_episode_extraction_job(job_id):
    """Publish to the broker; a publication failure is logged, never raised
    (the recovery scan re-dispatches). Eager mode runs inline, so task-body
    errors still propagate in tests (`memory.jobs.dispatch_extraction_job`'s
    rule)."""
    from django.conf import settings

    from memory.ingestion.tasks import run_episode_extraction_job

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_episode_extraction_job.delay(str(job_id))
        return
    try:
        run_episode_extraction_job.delay(str(job_id))
    except Exception:
        logger.exception("Failed to publish episode extraction job %s", job_id)


# --- claiming and finalization -----------------------------------------------


def claim_episode_extraction_job(job_id):
    now = timezone.now()
    with transaction.atomic():
        job = (
            EpisodeExtractionJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=job_id)
                & (
                    Q(status=EpisodeExtractionJob.Status.PENDING)
                    | Q(status=EpisodeExtractionJob.Status.RUNNING, lease_expires_at__lt=now)
                )
            )
            .first()
        )
        if job is None:
            return None
        token = uuid.uuid4()
        job.status = EpisodeExtractionJob.Status.RUNNING
        job.claim_token = token
        job.lease_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        job.save(update_fields=["status", "claim_token", "lease_expires_at", "updated_at"])
    return job_id, token


def _fenced(job_id, claim_token):
    return (
        EpisodeExtractionJob.objects.select_for_update()
        .filter(pk=job_id, status=EpisodeExtractionJob.Status.RUNNING, claim_token=claim_token)
        .first()
    )


def _close(job, status, *, error_type="", error_message="", candidates=None):
    job.status = status
    job.claim_token = None
    job.lease_expires_at = None
    job.error_type = error_type
    job.error_message = error_message[:500]
    fields = ["status", "claim_token", "lease_expires_at", "error_type", "error_message"]
    if candidates is not None:
        job.candidates = candidates
        fields.append("candidates")
    job.save(update_fields=[*fields, "updated_at"])


def _mark_extracted(episode_ids):
    if episode_ids:
        Episode.all_objects.filter(pk__in=list(episode_ids)).update(extracted_at=timezone.now())


@transaction.atomic
def finalize_success(job_id, claim_token, *, candidates, sent_episode_ids, drops=None):
    job = _fenced(job_id, claim_token)
    if job is None:
        return False
    _close(job, EpisodeExtractionJob.Status.SUCCEEDED, candidates=candidates)
    _mark_extracted(sent_episode_ids)
    _record_drops(drops)
    workspace_id = job.workspace_id
    from memory.ingestion.consolidation import dispatch_consolidation

    transaction.on_commit(lambda: dispatch_consolidation(workspace_id))
    return True


@transaction.atomic
def finalize_deferred(job_id, claim_token, *, error_type):
    """Budget refusal (per-source or gateway): the episodes stay
    un-extracted for the next night; nothing is retried in a loop (D13)."""
    job = _fenced(job_id, claim_token)
    if job is None:
        return False
    _close(job, EpisodeExtractionJob.Status.DEFERRED, error_type=error_type)
    workspace_id = job.workspace_id
    from memory.ingestion.consolidation import dispatch_consolidation

    transaction.on_commit(lambda: dispatch_consolidation(workspace_id))
    return True


@transaction.atomic
def finalize_failure(
    job_id, claim_token, *, error_type, error_message, retryable, sent_episode_ids=()
):
    """Retryable failures go back to `pending` until MAX_RETRIES (the
    recovery scan retries them; the episodes then wait for the next night).
    A terminal failure -- a schema error -- marks the sent episodes
    extracted so the same input is never re-sent."""
    job = _fenced(job_id, claim_token)
    if job is None:
        return False
    if retryable:
        job.retry_count += 1
        status = (
            EpisodeExtractionJob.Status.PENDING
            if job.retry_count < MAX_RETRIES
            else EpisodeExtractionJob.Status.FAILED
        )
        job.save(update_fields=["retry_count"])
    else:
        status = EpisodeExtractionJob.Status.FAILED
        _mark_extracted(sent_episode_ids)
    _close(job, status, error_type=error_type, error_message=error_message)
    if status == EpisodeExtractionJob.Status.FAILED:
        workspace_id = job.workspace_id
        from memory.ingestion.consolidation import dispatch_consolidation

        transaction.on_commit(lambda: dispatch_consolidation(workspace_id))
    return True


def _record_drops(drops):
    for source_id, reasons in (drops or {}).items():
        source = IngestionSource.objects.filter(pk=source_id).first()
        if source is not None:
            services.record_learning_drops(source, reasons)


# --- the call ----------------------------------------------------------------


def prompt_version(name):
    template = PromptTemplate.objects.filter(name=name).select_related("active_version").first()
    if template is None or template.active_version_id is None:
        raise ImproperlyConfigured(f"{name} prompt template is not configured")
    return template.active_version


def _fence(text):
    return neutralize_fence(strip_invisible_formatting(text or ""), *FENCE_WORDS)


def _second_scrub(episode, terms):
    """Defence in depth (D3): every text is scrubbed again right before it
    leaves for the gateway, so a rule added after ingestion still applies."""
    metadata, _ = scrub_value(episode.metadata or {}, terms=terms)
    metadata_text = json.dumps(metadata, sort_keys=True, ensure_ascii=False, default=str)
    return {
        "episode": episode,
        "owner_text": _fence(scrub_text(episode.owner_text, terms=terms).text),
        "context_text": _fence(scrub_text(episode.context_text, terms=terms).text),
        "metadata": _fence(metadata_text[:MAX_METADATA_CHARS]),
    }


def build_episode_messages(prompt_content, items):
    owner = ["<owner_items>"]
    context = ["<untrusted_context>"]
    for item in items:
        episode = item["episode"]
        owner.append(
            f'<owner_item episode_id="{episode.id}" source="{episode.source.kind}" '
            f'kind="{episode.kind}" occurred_at="{episode.occurred_at.isoformat()}">'
        )
        owner.append(item["owner_text"])
        owner.append("</owner_item>")
        context.append(f'<context_item episode_id="{episode.id}">')
        if item["context_text"]:
            context.append(item["context_text"])
        context.append(f"metadata: {item['metadata']}")
        context.append("</context_item>")
    owner.append("</owner_items>")
    context.append("</untrusted_context>")
    return [
        {"role": "system", "content": prompt_content},
        {"role": "user", "content": "\n".join(owner + context)},
    ]


def apply_source_budgets(episodes):
    """D13: per source, today's spent tokens plus the batch's estimates must
    stay within `daily_token_budget`. Returns `(within, deferred)` in order."""
    spent = {}
    within, deferred = [], []
    for episode in episodes:
        source = episode.source
        used = spent.setdefault(source.id, services.tokens_spent_today(source))
        if used + episode.token_estimate > source.daily_token_budget:
            deferred.append(episode)
            continue
        spent[source.id] = used + episode.token_estimate
        within.append(episode)
    return within, deferred


def _spend_tokens(episodes, usage):
    """Attribute the call's tokens to the sources in proportion to their
    episodes' estimates (the audit view's tokens-vs-budget)."""
    total = (usage.input_tokens or 0) + (usage.output_tokens or 0)
    weights = Counter()
    sources = {}
    for episode in episodes:
        weights[episode.source_id] += max(episode.token_estimate, 1)
        sources[episode.source_id] = episode.source
    weight_sum = sum(weights.values())
    if not total or not weight_sum:
        return
    for source_id, weight in weights.items():
        services.bump_counter(
            sources[source_id], "tokens_spent", math.ceil(total * weight / weight_sum)
        )


def verify_candidates(raw, items_by_id, workspace):
    """The provenance check, in code (D9). Returns `(survivors, drops)` where
    drops maps source id -> Counter of reasons."""
    allowed_labels = labels_for_workspace(workspace) - {CoreMemoryBlock.Label.SELF}
    fallback_source = next(iter(items_by_id.values()))["episode"].source_id if items_by_id else None
    survivors, drops = [], defaultdict(Counter)
    for candidate in raw:
        item = items_by_id.get(candidate["episode_id"])
        source_id = item["episode"].source_id if item else fallback_source
        target = candidate["target"]
        if target == CoreMemoryBlock.Label.SELF:
            drops[source_id]["self_target"] += 1
            continue
        if item is None or not quote_is_verbatim(candidate["quote"], item["owner_text"]):
            drops[source_id]["no_provenance"] += 1
            continue
        if target != MEMORY_TARGET and target not in allowed_labels:
            drops[source_id]["invalid_target"] += 1
            continue
        episode = item["episode"]
        survivors.append(
            {
                "type": candidate["type"],
                "title": candidate["title"].strip(),
                "content": candidate["content"].strip(),
                "quote": stored_quote(candidate["quote"]),
                "episode_id": str(episode.id),
                "source_id": str(episode.source_id),
                "target": target,
                "sensitivity": episode.sensitivity,
            }
        )
    return survivors, {str(k): v for k, v in drops.items() if k is not None}


def process_claimed_job(job_id, claim_token):
    job = EpisodeExtractionJob.objects.select_related("workspace").get(pk=job_id)
    workspace = job.workspace
    episodes = [
        e
        for e in Episode.objects.filter(
            pk__in=job.episode_ids, workspace=workspace, extracted_at__isnull=True
        )
        .select_related("source")
        .order_by("occurred_at", "id")
        if e.source.is_accepting and needs_personal_data(e) == job.personal_data
    ]
    if not episodes:
        finalize_success(job_id, claim_token, candidates=[], sent_episode_ids=[])
        return
    within, deferred = apply_source_budgets(episodes)
    deferred_counts = defaultdict(Counter)
    for episode in deferred:
        deferred_counts[str(episode.source_id)]["deferred_budget"] += 1
    _record_drops(deferred_counts)
    if not within:
        finalize_deferred(job_id, claim_token, error_type="source_budget")
        return

    version = prompt_version(PROMPT_NAME)
    terms = services.scrub_terms_for(workspace)
    items = [_second_scrub(e, terms) for e in within]
    report = {
        "job_id": str(job.id),
        "source_kinds": sorted({e.source.kind for e in within}),
        "episode_count": len(within),
        "token_estimate": sum(e.token_estimate for e in within),
    }
    try:
        result = InferenceGateway().complete(
            purpose="memory.episode_extraction",
            messages=build_episode_messages(version.content, items),
            actor=None,
            workspace=workspace,
            prompt_version=version,
            context_report=report,
            personal_data=job.personal_data,
        )
    except BudgetExceeded:
        finalize_deferred(job_id, claim_token, error_type="budget_exceeded")
        return
    except GatewayError as exc:
        finalize_failure(
            job_id,
            claim_token,
            error_type=exc.error_type,
            error_message=exc.error_type,
            retryable=exc.retryable,
        )
        return

    sent_ids = [e.id for e in within]
    _spend_tokens(within, result.usage)
    try:
        raw = parse_episode_candidates(result.text)
    except EpisodeParseError as exc:
        finalize_failure(
            job_id,
            claim_token,
            error_type="schema_invalid",
            error_message=str(exc),
            retryable=False,
            sent_episode_ids=sent_ids,
        )
        return
    items_by_id = {str(item["episode"].id): item for item in items}
    survivors, drops = verify_candidates(raw, items_by_id, workspace)
    finalize_success(
        job_id, claim_token, candidates=survivors, sent_episode_ids=sent_ids, drops=drops
    )


def run_job(job_id):
    claimed = claim_episode_extraction_job(job_id)
    if claimed is None:
        return
    claimed_id, token = claimed
    try:
        process_claimed_job(claimed_id, token)
    except Exception:
        # Never leak a claimed job; the exception text is never persisted.
        logger.exception("Unexpected failure in episode extraction job %s", claimed_id)
        finalize_failure(
            claimed_id,
            token,
            error_type="unexpected",
            error_message="Unexpected error -- see worker logs.",
            retryable=True,
        )


def recover_stale_jobs(now=None):
    now = now or timezone.now()
    grace = now - timedelta(seconds=PENDING_RECOVERY_GRACE_SECONDS)
    stale = list(
        EpisodeExtractionJob.objects.filter(
            Q(status=EpisodeExtractionJob.Status.PENDING, updated_at__lt=grace)
            | Q(status=EpisodeExtractionJob.Status.RUNNING, lease_expires_at__lt=now)
        ).values_list("id", flat=True)
    )
    for job_id in stale:
        dispatch_episode_extraction_job(job_id)
    return len(stale)
