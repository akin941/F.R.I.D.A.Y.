"""One eval run over a case set (brief D9).

Per case: clear the previous case's setup rows, load this case's rows,
retrieve with the mode under test as the eval owner in the case's
workspace, score the retrieval, and (unless `retrieval_only`) generate the
answer through the gateway under `memory.eval_answer`:

- one system message (the prompt plus the retrieved context), the case's
  `history`, then the question as the last `user` message, the layout
  `build_context` uses (D11);
- `personal_data=True` for a `sensitivity: private` case or when a
  personal-class or `private` episode reached the context
  (`needs_personal_data`). With no provider allowed for that, the case is
  counted `no_provider` with **no call** (`has_eligible_provider`, which
  writes no `ModelUsage` either);
- `context_report` carries `eval_run_id` (and `eval_arm` in a compare), so
  every eval `ModelUsage` row is attributable and never mistaken for chat.

The `learning_eval` cap (D9): before the run and before every answer call,
the month's eval spend is the larger of the eval database's `ModelUsage`
sum and the result files' `spend_usd` sum plus this run's own spend. A run
that would start over the cap is refused with no call; a run that reaches
it stops cleanly, counts the rest `budget_stopped`, and still writes its
partial result file. The gateway's own `BudgetExceeded` stops it the same
way.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from ai_core.budgets import spent
from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import AIBudget, ModelUsage, PromptTemplate
from memory.evals import metrics
from memory.evals.results import month_spend, write_result
from memory.evals.retrieval import BASELINE_MODE, retriever_for
from memory.evals.world import clear_case_rows, ensure_world, load_case
from memory.ingestion.extraction import needs_personal_data

ANSWER_PURPOSE = "memory.eval_answer"
PROMPT_NAME = "friday_memory_eval_answer"
BUDGET_NAME = "learning_eval"


class EvalRefused(Exception):
    """The run may not start (wrong settings, over the cap, no prompt)."""


def require_eval_settings():
    """D9 isolation: `run|baseline|compare` only under `config.settings.eval`
    (its own database)."""
    if getattr(settings, "MEMORY_EVAL_MODE", False) is not True:
        raise EvalRefused(
            "memory_eval run|baseline|compare run only under the eval settings: "
            "set DJANGO_SETTINGS_MODULE=config.settings.eval (its own database)."
        )


def _prompt_version():
    template = PromptTemplate.objects.filter(name=PROMPT_NAME).first()
    if template is None or template.active_version_id is None:
        raise EvalRefused(f"the {PROMPT_NAME} prompt is not configured: run seed_ai_defaults")
    return template.active_version


# --- the learning_eval cap ---------------------------------------------------


def eval_purposes():
    return tuple(getattr(settings, "AI_LEARNING_EVAL_PURPOSES", (ANSWER_PURPOSE,)))


def budget_limit():
    """The cap: the `learning_eval` AIBudget row when there is one (the owner
    may edit it), else the setting. `None` = no cap."""
    row = AIBudget.objects.filter(name=BUDGET_NAME).first()
    if row is not None:
        return row.monthly_limit_usd if row.is_active else None
    value = getattr(settings, "AI_BUDGET_LEARNING_EVAL_MONTHLY_USD", "")
    return Decimal(str(value)) if value not in (None, "") else None


class _PurposesBudget:
    """`ai_core.budgets.spent` takes a budget-like object."""

    def __init__(self, purposes):
        self.purposes = list(purposes)


def db_month_spend(now=None):
    return spent(_PurposesBudget(eval_purposes()), "monthly", now=now)


def run_spend(run_id, arm=None):
    rows = ModelUsage.objects.filter(context_report__eval_run_id=str(run_id))
    if arm:
        rows = rows.filter(context_report__eval_arm=arm)
    return rows.aggregate(total=Sum("estimated_cost"))["total"] or Decimal("0")


@dataclass
class BudgetGuard:
    """D9: `max(db sum, result-file sum + this run's spend)` against the
    cap. `prior_files` is read once, before the run (this run's own file
    does not exist yet)."""

    limit: Decimal | None
    prior_files: Decimal
    run_id: uuid.UUID

    def month_total(self):
        own = run_spend(self.run_id)
        return max(db_month_spend(), self.prior_files + own)

    def exhausted(self):
        return self.limit is not None and self.month_total() >= self.limit


def budget_guard(run_id, results_dirs):
    return BudgetGuard(limit=budget_limit(), prior_files=month_spend(results_dirs), run_id=run_id)


# --- the run -----------------------------------------------------------------


@dataclass
class RunConfig:
    set_name: str
    kind: str
    retrieval_mode: str = BASELINE_MODE
    retrieval_only: bool = False
    arm: str | None = None
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    compression_purpose: str | None = None


def _answer_messages(prompt, retrieved, case):
    system = prompt.content
    if retrieved.context_text:
        system = f"{system}\n{retrieved.context_text}"
    return [
        {"role": "system", "content": system},
        *({"role": m["role"], "content": m["content"]} for m in case.history),
        {"role": "user", "content": case.question},
    ]


def _context_report(config):
    report = {"eval_run_id": str(config.run_id)}
    if config.arm:
        report["eval_arm"] = config.arm
    return report


def run_cases(cases, config, *, results_dir, spend_dirs=()):
    """Run `cases` and write the result file. Returns `(result, path)`.
    `spend_dirs` are every results directory whose files count toward the
    cap (both sets' result directories)."""
    require_eval_settings()
    retriever = retriever_for(config.retrieval_mode)
    prompt = None if config.retrieval_only else _prompt_version()
    guard = budget_guard(config.run_id, spend_dirs or (results_dir,))
    if not config.retrieval_only and guard.exhausted():
        raise EvalRefused(
            f"the {BUDGET_NAME} monthly cap is spent; the run was refused with no model call"
        )

    started_at = timezone.now()
    world = ensure_world()
    gateway = InferenceGateway()
    outcomes = []
    counts = {"answered": 0, "no_provider": 0, "budget_stopped": 0}

    for index, case in enumerate(cases):
        if not config.retrieval_only and guard.exhausted():
            counts["budget_stopped"] = len(cases) - index
            break
        clear_case_rows(world)
        loaded = load_case(world, case)
        workspace = world.workspace(case.workspace_ref)
        retrieved = retriever(
            actor=world.owner,
            workspace=workspace,
            query_text=case.question,
            personal_data=case.is_private,
        )
        ranked = loaded.refs(retrieved.ranked_ids)
        outcome = metrics.CaseOutcome(
            recall=metrics.recall_at_k(ranked, case.expected.relevant_ids),
            leak=metrics.leaked(retrieved.context_text, case.expected.must_not_contain),
        )
        outcome.temporal = metrics.temporal_correct(
            is_temporal=case.is_temporal, recall=outcome.recall, leak=outcome.leak
        )
        outcomes.append(outcome)
        if config.retrieval_only:
            continue

        personal_data = case.is_private or any(needs_personal_data(e) for e in retrieved.episodes)
        if not gateway.has_eligible_provider(purpose=ANSWER_PURPOSE, personal_data=personal_data):
            counts["no_provider"] += 1
            continue
        try:
            result = gateway.complete(
                ANSWER_PURPOSE,
                _answer_messages(prompt, retrieved, case),
                actor=world.owner,
                workspace=workspace,
                prompt_version=prompt,
                context_report=_context_report(config),
                personal_data=personal_data,
            )
        except BudgetExceeded:
            counts["budget_stopped"] = len(cases) - index
            break
        except GatewayError:
            continue  # unanswered: not counted as answered, no answer metric
        counts["answered"] += 1
        outcome.answered = True
        outcome.correct = metrics.answer_correct(
            result.text, case.expected.must_contain, case.expected.must_not_contain
        )

    clear_case_rows(world)
    result = {
        "run_id": str(config.run_id),
        "set": config.set_name,
        "kind": config.kind,
        "retrieval_mode": config.retrieval_mode,
        "compression_purpose": config.compression_purpose,
        "arm": config.arm,
        "started_at": started_at.isoformat(),
        "finished_at": timezone.now().isoformat(),
        "total": len(cases),
        **counts,
        "spend_usd": str(run_spend(config.run_id, config.arm)),
        **metrics.aggregate(outcomes),
        "pre_tokens": None,
        "post_tokens": None,
        "verdict": None,
    }
    return result, None if results_dir is None else write_result(results_dir, result)


def run_compare(cases, *, set_name, candidate_mode, retrieval_only, results_dir, spend_dirs=()):
    """A retrieval compare (D8): arm A is the baseline mode, arm B the
    candidate, same run id. The verdict is written on arm B."""
    retriever_for(candidate_mode)  # unknown mode: refused before anything runs
    run_id = uuid.uuid4()
    arm_a, _ = run_cases(
        cases,
        RunConfig(
            set_name=set_name,
            kind="compare",
            retrieval_mode=BASELINE_MODE,
            retrieval_only=retrieval_only,
            arm="a",
            run_id=run_id,
        ),
        results_dir=None,
        spend_dirs=spend_dirs or (results_dir,),
    )
    arm_b, _ = run_cases(
        cases,
        RunConfig(
            set_name=set_name,
            kind="compare",
            retrieval_mode=candidate_mode,
            retrieval_only=retrieval_only,
            arm="b",
            run_id=run_id,
        ),
        results_dir=None,
        spend_dirs=spend_dirs or (results_dir,),
    )
    arm_b["verdict"] = metrics.compare_verdict(arm_a, arm_b)
    paths = [write_result(results_dir, arm_a), write_result(results_dir, arm_b)]
    return (arm_a, arm_b), paths
