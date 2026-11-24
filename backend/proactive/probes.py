"""The `ai_core.budgets` probe (Phase 12 D1, branch 2).

`ai_core` imports no business app (ADR-003) and so cannot register a probe
with a workspace-shaped `collect` of its own; `proactive` registers it at
startup over the `ai_core.budgets` read (`usage_at_or_above`). Gateway caps
are installation-wide: the probe reports the same numbers in every
workspace, as its workspace's data class -- budget names and percentages
only, never a prompt or a purpose's traffic.
"""

import re

from django.conf import settings

from ai_core import budgets
from core import probes

PROBE_NAME = "ai_core.budgets"
_WINDOW_LABEL = {"daily": "günlük", "monthly": "aylık"}
_BUDGET_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,50}$")


def _label(usage):
    name = usage.name if _BUDGET_NAME.match(usage.name or "") else "bütçe"
    window = _WINDOW_LABEL.get(usage.window, usage.window)
    return probes.safe_label(f"Bütçe {name} ({window}): %{min(usage.percent, 999)}", "Bütçe")


def collect(workspace, owner, now) -> probes.ProbeResult:
    rows = budgets.usage_at_or_above(int(settings.PROACTIVE_BUDGET_WARN_PERCENT), now=now)
    rows = rows[: int(settings.PROACTIVE_PROBE_MAX_KEYS)]
    items = tuple(
        probes.ProbeItem(
            key=f"{usage.budget_id}:{usage.window}", label=_label(usage), link="/today"
        )
        for usage in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=len(rows),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(f"{usage.budget_id}:{usage.window}" for usage in rows),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="ai_core",
    collect=collect,
    digest_section="budgets",
    notice_kind=PROBE_NAME,
    link="/today",
    notice_template="{label}",
)


def register():
    probes.register_probe(SPEC)
