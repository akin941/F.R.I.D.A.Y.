"""The weekly reflection (Phase 8B brief D5): proposal-only, capped.

Sunday 03:30 Europe/Istanbul (`config/celery.py`), once per workspace with
at least `MIN_APPROVED_ITEMS` items approved this ISO week, one
`memory.reflection` call (tier `mid`, prompt `friday_memory_reflection`)
sees:

- the owner texts of this week's live, cloud-eligible episodes (at most
  `MAX_OWNER_TEXTS`, newest first, each <= `MAX_OWNER_TEXT_CHARS`,
  scrubbed a second time) -- the only quotable text;
- fenced as untrusted context: the week's approved memories and facts
  (`cloud_eligible()` / `model_eligible()` only) and the normal
  `current_focus` and `active_projects` blocks.

The call can reach a cloud provider, so nothing `private` and no owner
text of a `private` or personal-class episode (`needs_personal_data`)
enters it, and it is sent with `personal_data=False`.

The reply is a schema-v2 episode-extraction reply (D4), at most
`MAX_REFLECTION_PROPOSALS` candidates. Each candidate's quote is verified in
code against the supplied owner texts (no quote, no proposal); `self` and
`developer` targets are dropped. Survivors become proposals under the same
caps as the nightly run (`consolidation.create_capped_proposals`: the
night's 10, the facts' 6, the weekly core-block 3). Nothing is applied.

One run per workspace per ISO week: a `memory.learning.reflection_started`
audit row claims the week before the call, so a budget refusal or a failure
is counted and never retried in a loop.
"""

import logging
from collections import Counter, defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from audit.models import AuditLog
from audit.services import record_audit
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.ingestion import consolidation, services
from memory.ingestion.extraction import (
    FACT_TARGET,
    EpisodeParseError,
    build_episode_messages,
    needs_personal_data,
    parse_episode_candidates,
    prompt_version,
    second_scrub,
    verify_candidates,
)
from memory.models import CoreMemoryBlock, Episode, Memory, MemoryFact
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

PROMPT_NAME = "friday_memory_reflection"
PURPOSE = "memory.reflection"
MIN_APPROVED_ITEMS = 5
MAX_REFLECTION_PROPOSALS = 5
MAX_OWNER_TEXTS = 40
MAX_OWNER_TEXT_CHARS = 1000
MAX_CONTEXT_ITEMS = 50
MAX_CONTEXT_CHARS = 300
EXCLUDED_TARGETS = frozenset({CoreMemoryBlock.Label.SELF, CoreMemoryBlock.Label.DEVELOPER})
CONTEXT_BLOCKS = (CoreMemoryBlock.Label.CURRENT_FOCUS, CoreMemoryBlock.Label.ACTIVE_PROJECTS)
FENCE_WORDS = ("reflection_context", "approved_item", "core_block")
STARTED = "memory.learning.reflection_started"
FINISHED = "memory.learning.reflection_finished"


def week_window(now=None):
    """This ISO week so far: local Monday 00:00 -> now."""
    local = timezone.localtime(now or timezone.now())
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, local


def approved_items_count(workspace, start, end):
    """Approved items this week. `Memory` has no decision timestamp, so an
    approved memory counts by its `updated_at` (set by the approval; a
    later owner edit in the same week also counts it, which is harmless for
    a threshold). Facts count by `decided_at`."""
    memories = Memory.objects.filter(
        workspace=workspace,
        status=Memory.Status.APPROVED,
        updated_at__gte=start,
        updated_at__lt=end,
    ).count()
    facts = MemoryFact.objects.filter(
        workspace=workspace,
        status=MemoryFact.Status.APPROVED,
        origin=MemoryFact.Origin.LEARNED,
        decided_at__gte=start,
        decided_at__lt=end,
    ).count()
    return memories + facts


def _already_claimed(workspace, start):
    return AuditLog.objects.filter(
        action=STARTED, workspace=workspace, created_at__gte=start
    ).exists()


def _fence(text):
    return neutralize_fence(strip_invisible_formatting(str(text or "")), *FENCE_WORDS)


def reflection_episodes(workspace, start, end):
    """The quotable week: live episodes of accepting sources, normal and not
    personal-class, with owner text; newest first."""
    rows = (
        Episode.objects.filter(
            workspace=workspace,
            occurred_at__gte=start,
            occurred_at__lt=end,
            sensitivity=Episode.Sensitivity.NORMAL,
            source__enabled=True,
            source__paused_at__isnull=True,
            source__forgotten_at__isnull=True,
        )
        .exclude(owner_text="")
        .select_related("source")
        .order_by("-occurred_at", "-id")
    )
    out = []
    for episode in rows:
        if needs_personal_data(episode):
            continue
        out.append(episode)
        if len(out) >= MAX_OWNER_TEXTS:
            break
    return out


def _context_lines(workspace, start, end):
    lines = ["<reflection_context>"]
    # `cloud_eligible()` / `model_eligible()` only (ADR-005): nothing
    # private reaches this payload.
    for memory in (
        Memory.objects.filter(workspace=workspace, updated_at__gte=start, updated_at__lt=end)
        .cloud_eligible()
        .order_by("-updated_at")[:MAX_CONTEXT_ITEMS]
    ):
        body = _fence(memory.content[:MAX_CONTEXT_CHARS])
        lines.append(f'<approved_item kind="memory">{_fence(memory.title)}: {body}</approved_item>')
    for fact in (
        MemoryFact.objects.filter(workspace=workspace, decided_at__gte=start, decided_at__lt=end)
        .model_eligible()
        .order_by("-decided_at")[:MAX_CONTEXT_ITEMS]
    ):
        lines.append(f'<approved_item kind="fact">{_fence(fact.statement)}</approved_item>')
    for block in CoreMemoryBlock.objects.filter(
        workspace=workspace, label__in=CONTEXT_BLOCKS, sensitivity=Memory.Sensitivity.NORMAL
    ):
        if block.content:
            lines.append(f'<core_block label="{block.label}">')
            lines.append(_fence(block.content))
            lines.append("</core_block>")
    lines.append("</reflection_context>")
    return lines


def build_reflection_messages(prompt_content, workspace, items, start, end):
    messages = build_episode_messages(prompt_content, items)
    messages[1]["content"] += "\n" + "\n".join(_context_lines(workspace, start, end))
    return messages


def _finish_audit(workspace, outcome, *, created=0, drops=None):
    record_audit(
        action=FINISHED,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        metadata={
            "outcome": outcome,
            "proposals_created": created,
            "drops": consolidation.summarise_drops(drops or {}),
        },
    )
    return {"workspace": str(workspace.pk), "outcome": outcome, "proposals_created": created}


def reflect_workspace(workspace, now=None):
    """One workspace's weekly reflection. Returns `{workspace, outcome,
    proposals_created}`; outcomes: `too_few_items`, `already_ran`,
    `no_owner_text`, `budget_exceeded`, `failed`, `schema_invalid`, `ok`."""
    start, end = week_window(now)
    summary = {"workspace": str(workspace.pk), "proposals_created": 0}
    if approved_items_count(workspace, start, end) < MIN_APPROVED_ITEMS:
        return {**summary, "outcome": "too_few_items"}
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=workspace.pk)
        if _already_claimed(workspace, start):
            return {**summary, "outcome": "already_ran"}
        record_audit(action=STARTED, actor_type=AuditLog.ActorType.SYSTEM, workspace=workspace)
    episodes = reflection_episodes(workspace, start, end)
    if not episodes:
        return _finish_audit(workspace, "no_owner_text")
    terms = services.scrub_terms_for(workspace)
    items = [second_scrub(e, terms, max_owner_chars=MAX_OWNER_TEXT_CHARS) for e in episodes]
    version = prompt_version(PROMPT_NAME)
    try:
        result = InferenceGateway().complete(
            purpose=PURPOSE,
            messages=build_reflection_messages(version.content, workspace, items, start, end),
            actor=None,
            workspace=workspace,
            prompt_version=version,
            context_report={
                "episode_count": len(items),
                "source_kinds": sorted({e.source.kind for e in episodes}),
                "week_start": start.date().isoformat(),
            },
            personal_data=False,
        )
    except BudgetExceeded:
        return _finish_audit(workspace, "budget_exceeded")
    except GatewayError:
        return _finish_audit(workspace, "failed")
    try:
        raw = parse_episode_candidates(result.text, max_candidates=MAX_REFLECTION_PROPOSALS)
    except EpisodeParseError:
        return _finish_audit(workspace, "schema_invalid")
    drops = defaultdict(Counter)
    fallback_source = str(episodes[0].source_id)
    allowed = []
    for candidate in raw:
        if candidate["target"] in EXCLUDED_TARGETS:
            drops[fallback_source]["reflection_target"] += 1
            continue
        allowed.append(candidate)
    items_by_id = {str(item["episode"].id): item for item in items}
    survivors, verify_drops = verify_candidates(allowed, items_by_id, workspace)
    for source_id, reasons in verify_drops.items():
        drops[source_id].update(reasons)
    survivors, inactive = consolidation.drop_inactive_source_candidates(survivors)
    for source_id, reasons in inactive.items():
        drops[source_id].update(reasons)
    ranked = [c for c in survivors if c["target"] != FACT_TARGET]
    facts = [(c, None) for c in survivors if c["target"] == FACT_TARGET]
    with transaction.atomic():
        consolidation.lock_owner(workspace.owner_id)
        created = consolidation.create_capped_proposals(
            workspace, ranked, drops, facts=facts, now=now
        )
        consolidation.record_drops(drops)
    return _finish_audit(workspace, "ok", created=created, drops=drops)


def run_weekly_reflection(now=None):
    """Every workspace with enough approved items this week; never raises
    for one workspace's failure."""
    start, end = week_window(now)
    workspace_ids = set(
        Memory.objects.filter(
            status=Memory.Status.APPROVED, updated_at__gte=start, updated_at__lt=end
        ).values_list("workspace_id", flat=True)
    ) | set(
        MemoryFact.objects.filter(
            status=MemoryFact.Status.APPROVED,
            origin=MemoryFact.Origin.LEARNED,
            decided_at__gte=start,
            decided_at__lt=end,
        ).values_list("workspace_id", flat=True)
    )
    results = []
    for workspace in Workspace.objects.filter(pk__in=workspace_ids).order_by("created_at"):
        try:
            results.append(reflect_workspace(workspace, now))
        except Exception:
            logger.exception("Weekly reflection failed for workspace %s", workspace.pk)
            results.append({"workspace": str(workspace.pk), "outcome": "unexpected"})
    return results
