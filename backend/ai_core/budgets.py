"""Gateway-enforced spending caps (Phase 6 A3, ADR-003 amendment 2026-11-20).

The gateway calls `find_refusal()` at the top of every entry point, BEFORE
provider selection: a refused call reaches no adapter and writes exactly one
`ModelUsage` row (`error_type="budget_exceeded"`, cost 0) -- written by the
gateway, the single usage writer.

A call is refused when a covering, active `AIBudget` has a limit for a
window and the call could add cost (`priced`) and either the window is
already spent (`spent >= limit`) or the call's own pre-call estimate would
push it over (`spent + estimate > limit`). A call whose every candidate
provider is unpriced (local Whisper/Piper/Ollama, the fake) is never
refused -- local cost-0 calls never trip a cap (A3), even in an exhausted
window.

Known residual: two concurrent calls can both pass the check and together
overshoot a cap by at most one call's cost. Friday has one owner and the
voice endpoints are throttled; a row lock per budget would serialize every
audio call for no practical gain.

The same primitive later carries ADR-012's learning caps: add an
`AIBudget` row covering those purposes.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from ai_core.models import AIBudget, ModelUsage

ZERO = Decimal("0")
SESSION_KEY = "budget_session"


@dataclass(frozen=True)
class BudgetRefusal:
    budget: str
    window: str
    limit: Decimal
    spent: Decimal
    estimate: Decimal

    def report(self):
        """A content-free `context_report["budget"]` entry (amounts as
        strings: JSON has no Decimal)."""
        return {
            "name": self.budget,
            "window": self.window,
            "limit_usd": str(self.limit),
            "spent_usd": str(self.spent),
            "estimate_usd": str(self.estimate),
        }


def budgets_for(purpose):
    """Active budgets whose `purposes` list contains `purpose`."""
    return [
        budget
        for budget in AIBudget.objects.filter(is_active=True)
        if purpose in (budget.purposes or [])
    ]


def window_start(window, now=None):
    now = timezone.localtime(now or timezone.now())
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "daily":
        return day
    if window == "monthly":
        return day.replace(day=1)
    raise ValueError(window)


def spent(budget, window, *, session="", now=None):
    """Sum of `ModelUsage.estimated_cost` over the budget's purposes in the
    window. The session window counts rows tagged with `session` (the
    gateway stores it as `context_report["budget_session"]`), at most 1 day
    back so the lookup stays index-bounded."""
    rows = ModelUsage.objects.filter(purpose__in=list(budget.purposes or []))
    if window == "session":
        rows = rows.filter(
            created_at__gte=(now or timezone.now()) - timedelta(days=1),
            **{f"context_report__{SESSION_KEY}": session},
        )
    else:
        rows = rows.filter(created_at__gte=window_start(window, now))
    return rows.aggregate(total=Sum("estimated_cost"))["total"] or ZERO


def _windows(budget, session):
    if budget.session_limit_usd is not None and session:
        yield "session", budget.session_limit_usd
    if budget.daily_limit_usd is not None:
        yield "daily", budget.daily_limit_usd
    if budget.monthly_limit_usd is not None:
        yield "monthly", budget.monthly_limit_usd


def find_refusal(purpose, *, estimate=ZERO, priced=False, session=""):
    """The first cap this call would break, or None. `priced`: could the
    call cost anything at all (some candidate provider has a non-zero
    price)? `estimate`: the call's worst-case pre-call cost (0 when it
    cannot be known before the call, e.g. chat tokens). A session window
    applies only to calls that carry a `session` key."""
    if not priced and estimate <= 0:
        return None
    for budget in budgets_for(purpose):
        for window, limit in _windows(budget, session):
            already = spent(budget, window, session=session)
            if already >= limit or already + estimate > limit:
                return BudgetRefusal(
                    budget=budget.name,
                    window=window,
                    limit=limit,
                    spent=already,
                    estimate=estimate,
                )
    return None
