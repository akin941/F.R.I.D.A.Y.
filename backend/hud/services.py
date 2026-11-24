"""HUD read model (Phase 6 branch 5, brief A8): the four server-side
sections of the `/hud` event stream -- approvals, today's tasks, live cost
and health. The browser's voice state is client-side and never crosses this
boundary; the Mac node's mic state (Phase 11 D15) arrives as a node event
and is read here through `nodes` like the rest of the node line.

Rules this module keeps:

- **Reads only.** No row is written, no gateway call is made (the
  `ai_core` edge is ModelUsage/provider/budget reads, ADR-003 untouched).
- **Workspace scoping (ADR-001).** Every business queryset passes through
  `for_actor()` narrowed to the one workspace the stream was opened for;
  another workspace's approvals or tasks never appear. ModelUsage rows (no
  `for_actor()` manager: they are gateway records, not business rows) are
  filtered by that same, already-authorized workspace -- the voice receipt's
  pattern.
- **No content.** Approvals carry their tool name, origin, channel and age
  -- never the payload; tasks carry their title (the owner's own row);
  cost and health carry numbers and status words only, never a prompt.
- **No decisions (D8).** Approvals are listed for a deep link to
  `/approvals/<id>`; nothing here (or on the HUD) decides one.

Phase 9 branch 7 (brief D11) adds the `node` block to `health`: the
actor's own node link (status word, last heartbeat, short catalogue hash,
invocable/advertised counts, spool depth, node version) through
`nodes.selectors.node_health` -- codes and counts only, never a path or
content.

Phase 10 branch 5 (brief D12) adds a fifth section, `terminal`: the
actor's last five node action executions in the stream's workspace
(`for_actor()` and `actor=`), with capability label, repo name, status,
exit code and duration -- never args and never output. The output itself
travels only through the terminal stream endpoint (`hud.views`), read from
the Redis stream the node gateway writes (`nodes.output`).

Phase 12 branch 2 (brief D5/D7/D20) adds `notices` (unread count and the
five latest notice titles and links) and `digest` (the newest morning
digest's card), both read through `proactive.api` only (D19).
"""

import posixpath
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ai_core import budgets
from ai_core.models import AIBudget, ModelProvider, ModelUsage
from approvals.models import ApprovalRequest
from nodes.action_rules import repo_path_of
from nodes.capabilities import get_action_contract
from nodes.models import EXECUTION_TERMINAL_STATUSES, NodeActionExecution
from nodes.selectors import node_health
from proactive import api as proactive_api
from tasks.models import Task

ZERO = Decimal("0")
# Errors that say nothing about whether a provider is up: the caller hung
# up, or a local budget refused the call before the provider was asked.
_NOT_PROVIDER_FAILURES = {"client_disconnect", "budget_exceeded", "no_provider"}
_HEALTH_CAPABILITIES = (
    ModelProvider.Capability.CHAT,
    ModelProvider.Capability.EMBEDDING,
    ModelProvider.Capability.STT,
    ModelProvider.Capability.TTS,
)
SECTIONS = ("approvals", "tasks", "cost", "health", "terminal", "notices", "digest")
# Phase 10 D12: the terminal section lists the actor's last five executions.
TERMINAL_SECTION_LIMIT = 5


def _money(value):
    return str((value or ZERO).quantize(Decimal("0.000001")))


def _iso(value):
    return value.isoformat() if value else None


def _limit():
    return max(1, int(settings.HUD_LIST_LIMIT))


def approvals_section(actor, workspace):
    """Pending, still-decidable approval requests (the activity summary's
    freshness rule) plus the "tool running" signal: requests approved within
    `HUD_TOOL_RUNNING_SECONDS` (an approval dispatches its tool right after
    the decision, so a fresh approval is the closest server-side signal of a
    running tool)."""
    now = timezone.now()
    scoped = ApprovalRequest.objects.for_actor(actor, workspace=workspace)
    fresh_since = now - timedelta(hours=settings.APPROVAL_REQUEST_TTL_HOURS)
    pending = scoped.filter(status=ApprovalRequest.Status.PENDING, created_at__gte=fresh_since)
    items = [
        {
            "id": str(row.id),
            "tool_name": row.tool_name,
            "origin": row.origin,
            "requested_channel": row.requested_channel,
            "created_at": _iso(row.created_at),
        }
        for row in pending.order_by("-created_at", "-id")[: _limit()]
    ]
    running_since = now - timedelta(seconds=settings.HUD_TOOL_RUNNING_SECONDS)
    running = scoped.filter(
        status=ApprovalRequest.Status.APPROVED, decided_at__gte=running_since
    ).count()
    return {"pending_count": pending.count(), "items": items, "tools_running": running}


def tasks_section(actor, workspace, *, today=None):
    """Open tasks due today or overdue (`/today`'s view; the server date
    unless the client sent its local date)."""
    today = today or timezone.localdate()
    open_due = Task.objects.for_actor(actor, workspace=workspace).filter(
        status=Task.Status.OPEN, due_date__isnull=False, due_date__lte=today
    )
    items = [
        {
            "id": str(row.id),
            "title": row.title,
            "due_date": row.due_date.isoformat(),
            "priority": row.priority,
            "overdue": row.due_date < today,
        }
        for row in open_due.order_by("due_date", "created_at", "id")[: _limit()]
    ]
    return {"date": today.isoformat(), "open_count": open_due.count(), "items": items}


def cost_section(workspace):
    """Today's `ModelUsage` in this workspace: numbers only."""
    start = budgets.window_start("daily")
    rows = ModelUsage.objects.filter(workspace=workspace, created_at__gte=start)
    totals = rows.aggregate(
        calls=Count("id"),
        failed=Count("id", filter=Q(success=False)),
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        cost=Sum("estimated_cost"),
    )
    return {
        "since": _iso(start),
        "calls": totals["calls"] or 0,
        "failed_calls": totals["failed"] or 0,
        "input_tokens": totals["input_tokens"] or 0,
        "output_tokens": totals["output_tokens"] or 0,
        "estimated_cost": _money(totals["cost"]),
        "local_only": not rows.filter(estimated_cost__gt=0).exists(),
    }


def _provider_status(provider, usage_rows, since):
    if not provider.is_active:
        return "disabled", None
    last = (
        usage_rows.filter(provider=provider, created_at__gte=since)
        .exclude(error_type__in=_NOT_PROVIDER_FAILURES)
        .order_by("-created_at")
        .first()
    )
    if last is None:
        return "unknown", None
    status = "ok" if last.success else "down"
    return status, {"at": _iso(last.created_at), "error_type": last.error_type or None}


def _is_priced(provider):
    return any(
        (
            provider.input_price_per_mtok,
            provider.output_price_per_mtok,
            provider.price_per_audio_second,
            provider.price_per_mchar,
        )
    )


def _budget_rows():
    rows = []
    for budget in AIBudget.objects.filter(is_active=True).order_by("name"):
        windows = []
        for window, limit in (
            ("daily", budget.daily_limit_usd),
            ("monthly", budget.monthly_limit_usd),
        ):
            if limit is None:
                continue
            used = budgets.spent(budget, window)
            windows.append(
                {
                    "window": window,
                    "limit_usd": _money(limit),
                    "spent_usd": _money(used),
                    "exhausted": used >= limit,
                }
            )
        rows.append(
            {
                "name": budget.name,
                "windows": windows,
                "exhausted": any(w["exhausted"] for w in windows),
            }
        )
    return rows


def _preflight():
    """The last `manage.py preflight` summary, written by that command into
    the shared cache under `HUD_PREFLIGHT_CACHE_KEY` (`ops` is a leaf no app
    may import; the key in settings is the only coupling)."""
    value = cache.get(settings.HUD_PREFLIGHT_CACHE_KEY)
    if not isinstance(value, dict):
        return None
    keys = ("status", "pass", "fail", "warn", "at", "failed_checks")
    return {key: value.get(key) for key in keys}


def node_section(actor):
    """The health panel's node line (Phase 9 D11, branch 7): the actor's own
    node link through `nodes.selectors.node_health` -- a status word, the
    last heartbeat, a short catalogue hash, counts, the spool depth and the
    node's version. No path, no capability name, no read output. The nodes
    are the actor's own (`for_actor()` plus `owner=actor`), so the same
    line shows in each of the owner's workspace streams and never another
    user's node."""
    block = node_health(actor)
    return {
        "status": block["status"],
        "last_heartbeat_at": _iso(block.get("last_heartbeat_at")),
        "catalogue_sha256": block.get("catalogue_sha256", ""),
        "invocable_count": block.get("invocable_count", 0),
        "advertised_count": block.get("advertised_count", 0),
        "spool_depth": block.get("spool_depth", 0),
        "node_version": block.get("node_version", ""),
        "voice": _voice_line(block.get("voice") or {}),
    }


def _voice_line(voice):
    """Phase 11 D15: the node's mic state (`nodes.selectors.voice_summary`,
    read through the `nodes` edge, never `voice`): a state word (`unknown`
    when stale or offline), the local mode, the session flag and the voice
    child's daily counters. No audio, no transcript, no draft list."""
    return {
        "state": voice.get("state", "unknown"),
        "mode": voice.get("mode", ""),
        "session_open": bool(voice.get("session_open", False)),
        "wake_accepts_today": int(voice.get("wake_accepts_today", 0)),
        "wake_discards_today": int(voice.get("wake_discards_today", 0)),
        "storm_mutes_today": int(voice.get("storm_mutes_today", 0)),
        "at": _iso(voice.get("at")),
    }


def health_section(workspace, actor=None):
    """Provider status (the last call in the window, per provider), budgets
    used, the last preflight result and (Phase 9 D11) the actor's node
    link. Mic state is client-side."""
    since = timezone.now() - timedelta(minutes=settings.HUD_PROVIDER_WINDOW_MINUTES)
    # The workspace's own calls plus system calls (no workspace: preflight,
    # scheduled jobs) -- never another workspace's rows.
    usage_rows = ModelUsage.objects.filter(Q(workspace=workspace) | Q(workspace__isnull=True))
    providers = []
    for provider in ModelProvider.objects.filter(capability__in=_HEALTH_CAPABILITIES).order_by(
        "capability", "priority", "name"
    ):
        status, last_call = _provider_status(provider, usage_rows, since)
        providers.append(
            {
                "name": provider.name,
                "capability": provider.capability,
                "status": status,
                "personal_data_allowed": provider.personal_data_allowed,
                "local": not _is_priced(provider),
                "last_call": last_call,
            }
        )
    budget_rows = _budget_rows()
    return {
        "providers": providers,
        "providers_down": sum(1 for p in providers if p["status"] == "down"),
        "budgets": budget_rows,
        "budgets_exhausted": sum(1 for b in budget_rows if b["exhausted"]),
        "preflight": _preflight(),
        "node": node_section(actor) if actor is not None else None,
    }


def _execution_repo_name(execution, contract):
    """The repo's mapped name (the last segment of a `repo_path` the
    approval resolved to this workspace), never more of the raw path; "" for
    capabilities without one (confirm-class has no approval, no args)."""
    if contract is None or execution.approval_id is None:
        return ""
    payload = execution.approval.payload if execution.approval is not None else None
    args = payload.get("args") if isinstance(payload, dict) else None
    if not isinstance(args, dict):
        return ""
    path = repo_path_of(contract, args)
    if not path:
        return ""
    return posixpath.basename(str(path).rstrip("/"))[:120]


def _execution_duration_ms(execution, now):
    start = execution.started_at or execution.created_at
    end = execution.finished_at or now
    if start is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def execution_summary(execution, *, now=None):
    """One execution as the HUD shows it: label, repo name, status, exit
    code, duration -- never args or output."""
    now = now or timezone.now()
    contract = get_action_contract(execution.capability)
    return {
        "id": str(execution.id),
        "capability": execution.capability,
        "label": contract.label if contract is not None else execution.capability,
        "class": execution.capability_class,
        "repo_name": _execution_repo_name(execution, contract),
        "status": execution.status,
        "running": execution.status == NodeActionExecution.Status.RUNNING,
        "terminal": execution.status in EXECUTION_TERMINAL_STATUSES,
        "exit_code": execution.exit_code,
        "duration_ms": _execution_duration_ms(execution, now),
        "created_at": _iso(execution.created_at),
        "finished_at": _iso(execution.finished_at),
    }


def actor_executions(actor, workspace):
    """The actor's own node action executions in this workspace (D12),
    through `for_actor()`; newest first."""
    return (
        NodeActionExecution.objects.for_actor(actor, workspace=workspace)
        .filter(actor=actor)
        .select_related("approval")
        .order_by("-created_at", "-id")
    )


def terminal_section(actor, workspace):
    """The actor's last `TERMINAL_SECTION_LIMIT` executions in the stream's
    workspace (D12): capability label, repo name, status, exit code and
    duration -- no output (the stream endpoint carries that)."""
    now = timezone.now()
    rows = list(actor_executions(actor, workspace)[:TERMINAL_SECTION_LIMIT])
    items = [execution_summary(row, now=now) for row in rows]
    return {
        "items": items,
        "running_count": sum(1 for item in items if item["running"]),
    }


def notices_section(actor, workspace):
    """Phase 12 D5/D20: the unread proactive notices of the stream's
    workspace (`proactive.api.notice_section`, `for_actor()`): the unread
    count and the five latest titles and links -- Friday templates over D1
    labels, no deep content. Nothing here marks a notice read."""
    return proactive_api.notice_section(actor=actor, workspace=workspace)


def digest_section(actor, workspace):
    """Phase 12 D7/D20: the newest morning digest's card (id, spoken text,
    time, opened/spoken flags, `speak_on_open`), or None. "Dinle" speaks it
    in the browser through the speech endpoint; the HUD decides nothing."""
    return proactive_api.digest_section(actor=actor, workspace=workspace)


def build_sections(actor, workspace, *, today=None):
    return {
        "approvals": approvals_section(actor, workspace),
        "tasks": tasks_section(actor, workspace, today=today),
        "cost": cost_section(workspace),
        "health": health_section(workspace, actor),
        "terminal": terminal_section(actor, workspace),
        "notices": notices_section(actor, workspace),
        "digest": digest_section(actor, workspace),
    }


def changed_sections(previous, current):
    """The sections whose content differs. A delta replaces whole sections:
    simple for the client and small at HUD list sizes."""
    return {name: current[name] for name in SECTIONS if previous.get(name) != current[name]}
