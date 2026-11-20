"""Nightly consolidation and the proposal caps (Phase 8A brief D10, D13).

Once a workspace has no open extraction job, its provenance-checked
candidates are consolidated -- one `memory.consolidation` call per workspace
and data class (personal-class/private candidates with `personal_data=True`,
never mixed with cloud-eligible ones):

1. **Deterministic dedupe** first: a candidate whose fingerprint matches any
   memory of the workspace (or another candidate), whose core-block line is
   already in the block, or whose embedding (the existing `memory.retrieval`
   path) is near an approved memory's is dropped and counted.
2. **The model** merges near-duplicates and ranks what is left; it can only
   select candidates by index and reword their title/content -- the quote,
   episode, source and target stay the code-held, verified ones.
3. **Caps in code** (d6): at most `MAX_PROPOSALS_PER_NIGHT` new proposals per
   owner per night across all workspaces (memory + core-block proposals; the
   rest are discarded and counted, never queued) and the weekly core-block
   cap (`memory.services.CORE_BLOCK_PROPOSALS_PER_WEEK`).

It **never applies** anything: every output row is `pending_approval` /
`pending`. A budget refusal or a transient gateway error leaves the
candidates for the next night; a schema error discards them (terminal).
"""

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from pgvector.django import CosineDistance

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from audit.models import AuditLog
from audit.services import record_audit
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.core_blocks import CORE_BLOCK_CHAR_BUDGETS
from memory.ingestion import services
from memory.ingestion.extraction import (
    MEMORY_TARGET,
    drop_inactive_source_candidates,
    inactive_source_reason,
    prompt_version,
)
from memory.models import (
    CoreBlockProposal,
    CoreMemoryBlock,
    EpisodeExtractionJob,
    IngestionSource,
    Memory,
    compute_fingerprint,
)
from memory.provenance import normalise
from memory.services import (
    CoreBlockInvalid,
    CoreBlockProposalCapReached,
    create_core_block_proposal,
)
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

PROMPT_NAME = "friday_memory_consolidation"
SCHEMA_VERSION = 1
# d6: per owner per night, across all workspaces -- enforced here, not in a
# prompt.
MAX_PROPOSALS_PER_NIGHT = 10
# Cosine distance under which a candidate counts as a near-duplicate of an
# approved memory (similarity >= 0.92).
SIMILARITY_DISTANCE = 0.08
LEASE_SECONDS = 420
MAX_EXISTING_MEMORIES = 50
MAX_EXISTING_CHARS = 300
MAX_TITLE_CHARS = 255
MAX_CONTENT_CHARS = 1000
FENCE_WORDS = ("candidate", "existing", "memor", "core_block")

_OPEN = (EpisodeExtractionJob.Status.PENDING, EpisodeExtractionJob.Status.RUNNING)


class ConsolidationParseError(Exception):
    """Schema v1 violation: terminal, the candidates are discarded."""


def parse_consolidation(raw_text, candidate_count):
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        raise ConsolidationParseError("invalid JSON") from None
    if not isinstance(payload, dict) or set(payload) != {"version", "proposals"}:
        raise ConsolidationParseError("unexpected top-level shape")
    version = payload["version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ConsolidationParseError("unsupported schema version")
    proposals = payload["proposals"]
    if not isinstance(proposals, list) or len(proposals) > candidate_count:
        raise ConsolidationParseError("'proposals' must be a list no longer than the candidates")
    seen, parsed = set(), []
    for position, item in enumerate(proposals):
        if not isinstance(item, dict) or set(item) != {"index", "title", "content"}:
            raise ConsolidationParseError(f"proposal {position} has unexpected keys")
        index = item["index"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ConsolidationParseError(f"proposal {position} has a non-integer index")
        if not 0 <= index < candidate_count or index in seen:
            raise ConsolidationParseError(f"proposal {position} has an invalid index")
        seen.add(index)
        for key, cap in (("title", MAX_TITLE_CHARS), ("content", MAX_CONTENT_CHARS)):
            value = item[key]
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > cap:
                raise ConsolidationParseError(f"proposal {position} has an invalid {key}")
        parsed.append(item)
    return parsed


def dispatch_consolidation(workspace_id):
    from django.conf import settings

    from memory.ingestion.tasks import consolidate_workspace_task

    if settings.CELERY_TASK_ALWAYS_EAGER:
        consolidate_workspace_task.delay(str(workspace_id))
        return
    try:
        consolidate_workspace_task.delay(str(workspace_id))
    except Exception:
        logger.exception("Failed to publish consolidation for workspace %s", workspace_id)


def night_window(now=None):
    """The current learning night: the local calendar day of the run."""
    local = timezone.localtime(now or timezone.now())
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def learned_proposals_tonight(owner_id, now=None):
    start, end = night_window(now)
    window = Q(created_at__gte=start, created_at__lt=end)
    memories = Memory.objects.filter(
        window, workspace__owner_id=owner_id, ingestion_source__isnull=False
    ).count()
    blocks = CoreBlockProposal.objects.filter(
        window, workspace__owner_id=owner_id, ingestion_source__isnull=False
    ).count()
    return memories + blocks


# --- claim / release ---------------------------------------------------------


def _claim(workspace_id, personal_data):
    now = timezone.now()
    with transaction.atomic():
        jobs = list(
            EpisodeExtractionJob.objects.select_for_update(skip_locked=True)
            .filter(
                workspace_id=workspace_id,
                personal_data=personal_data,
                consolidated_at__isnull=True,
            )
            .exclude(status__in=_OPEN)
            .filter(Q(consolidation_token__isnull=True) | Q(consolidation_lease_expires_at__lt=now))
            .order_by("created_at", "id")
        )
        empty = [j.id for j in jobs if not j.candidates]
        if empty:
            EpisodeExtractionJob.objects.filter(pk__in=empty).update(consolidated_at=now)
        jobs = [j for j in jobs if j.candidates]
        if not jobs:
            return None
        token = uuid.uuid4()
        EpisodeExtractionJob.objects.filter(pk__in=[j.id for j in jobs]).update(
            consolidation_token=token,
            consolidation_lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
        )
    candidates = [c for job in jobs for c in job.candidates]
    return token, candidates


def _fenced_jobs(token):
    return list(EpisodeExtractionJob.objects.select_for_update().filter(consolidation_token=token))


def _release(token):
    with transaction.atomic():
        EpisodeExtractionJob.objects.filter(consolidation_token=token).update(
            consolidation_token=None, consolidation_lease_expires_at=None
        )


def _close_jobs(jobs):
    now = timezone.now()
    EpisodeExtractionJob.objects.filter(pk__in=[j.id for j in jobs]).update(
        consolidated_at=now,
        consolidation_token=None,
        consolidation_lease_expires_at=None,
        # The candidates are now proposals or discarded: the job keeps no
        # owner-derived text past consolidation.
        candidates=[],
    )


# --- deterministic dedupe ----------------------------------------------------


def _block_contents(workspace):
    return {
        block.label: block.content for block in CoreMemoryBlock.objects.filter(workspace=workspace)
    }


def _near_approved(workspace, candidates, personal_data):
    """Indexes of memory-target candidates whose embedding is near an
    approved memory's (existing `memory.retrieval` path). Best effort: any
    gateway failure skips this step (fingerprints still apply)."""
    texts = [f"{c['title']}\n\n{c['content']}" for c in candidates]
    if (
        not texts
        or not Memory.objects.filter(
            workspace=workspace, status=Memory.Status.APPROVED, embedding__isnull=False
        ).exists()
    ):
        return set()
    try:
        result = InferenceGateway().embed(
            purpose="memory.retrieval",
            texts=texts,
            workspace=workspace,
            context_report={"step": "consolidation_dedupe", "count": len(texts)},
            personal_data=personal_data,
        )
    except GatewayError:
        return set()
    provider_id = result.usage.provider_id
    near = set()
    for index, vector in enumerate(result.vectors):
        rows = Memory.objects.filter(
            workspace=workspace,
            status=Memory.Status.APPROVED,
            embedding__isnull=False,
            embedding_provider_id=provider_id,
        )
        try:
            with transaction.atomic():
                hit = (
                    rows.annotate(distance=CosineDistance("embedding", vector))
                    .filter(distance__lt=SIMILARITY_DISTANCE)
                    .exists()
                )
            if hit:
                near.add(index)
        except Exception:  # dimension mismatch on a legacy row: skip, never fail
            logger.warning("Similarity dedupe skipped for one candidate", exc_info=True)
    return near


def dedupe(workspace, candidates, personal_data):
    """Returns `(kept, drops)`; drops maps source id -> Counter."""
    drops = defaultdict(Counter)
    blocks = _block_contents(workspace)
    seen = set()
    kept = []
    for candidate in candidates:
        target = candidate["target"]
        fingerprint = compute_fingerprint(candidate["content"])
        key = (target, fingerprint)
        duplicate = key in seen
        if target == MEMORY_TARGET:
            duplicate = (
                duplicate
                or Memory.objects.filter(workspace=workspace, fingerprint=fingerprint).exists()
            )
        else:
            duplicate = duplicate or normalise(candidate["content"]) in normalise(
                blocks.get(target, "")
            )
        seen.add(key)
        if duplicate:
            drops[candidate["source_id"]]["duplicate"] += 1
            continue
        kept.append(candidate)
    memory_candidates = [c for c in kept if c["target"] == MEMORY_TARGET]
    near = _near_approved(workspace, memory_candidates, personal_data)
    near_ids = {id(memory_candidates[i]) for i in near}
    result = []
    for candidate in kept:
        if id(candidate) in near_ids:
            drops[candidate["source_id"]]["duplicate"] += 1
            continue
        result.append(candidate)
    return result, drops


# --- the call ----------------------------------------------------------------


def _fence(text):
    return neutralize_fence(strip_invisible_formatting(str(text or "")), *FENCE_WORDS)


def build_consolidation_messages(prompt_content, workspace, candidates):
    lines = ["<candidates>"]
    for index, candidate in enumerate(candidates):
        lines.append(
            f'<candidate index="{index}" type="{candidate["type"]}" target="{candidate["target"]}">'
        )
        lines.append(f"title: {_fence(candidate['title'])}")
        lines.append(f"content: {_fence(candidate['content'])}")
        lines.append("</candidate>")
    lines.append("</candidates>")
    lines.append("<existing>")
    # `cloud_eligible()` only (ADR-005): a private memory never reaches a
    # gateway payload, whatever the provider.
    for memory in (
        Memory.objects.filter(workspace=workspace)
        .cloud_eligible()
        .order_by("-updated_at")[:MAX_EXISTING_MEMORIES]
    ):
        body = _fence(memory.content[:MAX_EXISTING_CHARS])
        lines.append(f"<memory>{_fence(memory.title)}: {body}</memory>")
    for block in CoreMemoryBlock.objects.filter(
        workspace=workspace, sensitivity=Memory.Sensitivity.NORMAL
    ).exclude(label=CoreMemoryBlock.Label.SELF):
        if block.content:
            lines.append(f'<core_block label="{block.label}">')
            lines.append(_fence(block.content))
            lines.append("</core_block>")
    lines.append("</existing>")
    return [
        {"role": "system", "content": prompt_content},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _record_drops(drops):
    for source_id, reasons in drops.items():
        source = IngestionSource.objects.filter(pk=source_id).first()
        if source is not None:
            services.record_learning_drops(source, reasons)


def consolidate_workspace(workspace_id, now=None):
    """Consolidate a workspace's waiting candidates (both data classes).
    Waits while any extraction job of the workspace is still open. Returns
    the number of proposals created."""
    if EpisodeExtractionJob.objects.filter(workspace_id=workspace_id, status__in=_OPEN).exists():
        return 0
    created = 0
    for personal_data in (False, True):
        claimed = _claim(workspace_id, personal_data)
        if claimed is None:
            continue
        token, candidates = claimed
        try:
            created += _consolidate_claimed(workspace_id, personal_data, token, candidates, now)
        except Exception:
            logger.exception("Unexpected consolidation failure for workspace %s", workspace_id)
            _release(token)
    return created


def _consolidate_claimed(workspace_id, personal_data, token, candidates, now):
    workspace = Workspace.objects.get(pk=workspace_id)
    # No model spend on a source that stopped accepting since extraction;
    # `_finish` re-checks under the job lock for the race with forget.
    candidates, inactive = drop_inactive_source_candidates(candidates)
    kept, drops = dedupe(workspace, candidates, personal_data)
    for source_id, reasons in inactive.items():
        drops[source_id].update(reasons)
    ranked = []
    if kept:
        version = prompt_version(PROMPT_NAME)
        sources = IngestionSource.objects.filter(pk__in={c["source_id"] for c in kept})
        try:
            result = InferenceGateway().complete(
                purpose="memory.consolidation",
                messages=build_consolidation_messages(version.content, workspace, kept),
                actor=None,
                workspace=workspace,
                prompt_version=version,
                context_report={
                    "candidate_count": len(kept),
                    "source_kinds": sorted({s.kind for s in sources}),
                    "personal_data": personal_data,
                },
                personal_data=personal_data,
            )
        except BudgetExceeded:
            _release(token)  # next night (D13); nothing loops
            return 0
        except GatewayError as exc:
            if exc.retryable:
                _release(token)
                return 0
            return _finish(token, [], drops, discard=kept, reason="consolidation_failed", now=now)
        try:
            proposals = parse_consolidation(result.text, len(kept))
        except ConsolidationParseError:
            return _finish(
                token, [], drops, discard=kept, reason="consolidation_schema_invalid", now=now
            )
        chosen = set()
        for item in proposals:
            candidate = dict(kept[item["index"]])
            chosen.add(item["index"])
            if item["title"]:
                candidate["title"] = item["title"].strip()
            if item["content"]:
                candidate["content"] = item["content"].strip()
            ranked.append(candidate)
        for index, candidate in enumerate(kept):
            if index not in chosen:
                drops[candidate["source_id"]]["merged"] += 1
    return _finish(token, ranked, drops, now=now)


def _finish(token, ranked, drops, *, discard=(), reason="", now=None):
    for candidate in discard:
        drops[candidate["source_id"]][reason] += 1
    created = 0
    with transaction.atomic():
        jobs = _fenced_jobs(token)
        if not jobs:
            return 0  # fenced out: another consolidation took over
        workspace = jobs[0].workspace
        owner_id = workspace.owner_id
        # Serialize per owner so two workspaces' consolidations share the
        # nightly cap exactly.
        list(Workspace.objects.select_for_update().filter(owner_id=owner_id))
        used = learned_proposals_tonight(owner_id, now)
        sources = {
            str(s.id): s
            for s in IngestionSource.objects.filter(pk__in={c["source_id"] for c in ranked})
        }
        for candidate in ranked:
            source = sources.get(candidate["source_id"])
            # Re-checked under the job lock: a source forgotten, paused or
            # disabled after the claim yields no proposals (its forget
            # cascade ran earlier and would not see them).
            reason = "source_forgotten" if source is None else inactive_source_reason(source)
            if reason:
                drops[candidate["source_id"]][reason] += 1
                continue
            if used >= MAX_PROPOSALS_PER_NIGHT:
                drops[candidate["source_id"]]["nightly_cap"] += 1
                continue
            outcome = _create_proposal(workspace, source, candidate)
            if outcome == "created":
                used += 1
                created += 1
                services.bump_counter(source, "proposals_created")
            else:
                drops[candidate["source_id"]][outcome] += 1
        _close_jobs(jobs)
        _record_drops(drops)
        record_audit(
            action="memory.learning.consolidated",
            actor_type=AuditLog.ActorType.SYSTEM,
            workspace=workspace,
            metadata={
                "proposals_created": created,
                "candidates": len(ranked) + len(discard),
                "drops": {
                    reason: sum(c[reason] for c in drops.values())
                    for reason in {r for c in drops.values() for r in c}
                },
            },
        )
    return created


def _create_proposal(workspace, source, candidate):
    episode_id = candidate.get("episode_id") or None
    if candidate["target"] == MEMORY_TARGET:
        sensitivity = (
            Memory.Sensitivity.PRIVATE
            if candidate.get("sensitivity") == Memory.Sensitivity.PRIVATE
            else Memory.Sensitivity.NORMAL
        )
        try:
            with transaction.atomic():
                memory = Memory.objects.create(
                    workspace=workspace,
                    memory_type=candidate["type"],
                    sensitivity=sensitivity,
                    status=Memory.Status.PENDING_APPROVAL,
                    source=Memory.Source.AI_SUGGESTED,
                    title=candidate["title"][:MAX_TITLE_CHARS],
                    content=candidate["content"],
                    fingerprint=compute_fingerprint(candidate["content"]),
                    ingestion_source=source,
                    source_episode_id=episode_id,
                    provenance_quote=candidate["quote"][:300],
                )
        except IntegrityError:
            return "duplicate"
        record_audit(
            action="memory.learning.proposed",
            actor_type=AuditLog.ActorType.SYSTEM,
            workspace=workspace,
            resource=memory,
            metadata={"source_kind": source.kind, "target": MEMORY_TARGET},
        )
        return "created"
    if candidate.get("sensitivity") == Memory.Sensitivity.PRIVATE or source.is_personal_class:
        # ADR-005, defence in depth (extraction already refuses these): a
        # block has no per-line sensitivity and normal blocks reach cloud
        # payloads, so private/personal-class content never becomes one.
        return "private_block_target"
    label = candidate["target"]
    current = CoreMemoryBlock.objects.filter(workspace=workspace, label=label).first()
    base = current.content if current else ""
    proposed = f"{base.rstrip()}\n{candidate['content']}".strip() if base else candidate["content"]
    if len(proposed) > CORE_BLOCK_CHAR_BUDGETS[label]:
        return "over_budget"
    try:
        with transaction.atomic():
            create_core_block_proposal(
                workspace=workspace,
                label=label,
                proposed_content=proposed,
                provenance_quote=candidate["quote"],
                source_episode_id=episode_id,
                ingestion_source=source,
            )
    except CoreBlockProposalCapReached:
        return "weekly_cap"
    except CoreBlockInvalid:
        return "invalid_target"
    return "created"


def consolidate_waiting(now=None):
    """Nightly recovery: consolidate every workspace whose candidates are
    still waiting (a released claim, a lost dispatch)."""
    workspace_ids = set(
        EpisodeExtractionJob.objects.filter(consolidated_at__isnull=True)
        .exclude(status__in=_OPEN)
        .values_list("workspace_id", flat=True)
    )
    return sum(consolidate_workspace(ws_id, now) for ws_id in workspace_ids)
