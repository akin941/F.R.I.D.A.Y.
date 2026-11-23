"""Eval metrics (brief D9). Pure functions, no database, no model.

Deterministic:
- **recall@10**: the share of a case's `relevant_ids` among the first 10
  retrieved rows; averaged over the cases that name any.
- **leak rate**: the share of cases (among those with `must_not_contain`)
  whose retrieved context contains any `must_not_contain` term.
- **temporal correctness**: over the cases tagged `temporal`, the share
  whose every relevant row is in the top 10 and whose context leaks no
  `must_not_contain` term (the superseded or out-of-window value). With the
  case format's fields this is the as-of check: the right version was
  retrieved and the wrong one was not.

Answer accuracy (no LLM judge): the share of answered cases whose answer
contains every `must_contain` key phrase and no `must_not_contain` term,
after `normalise()` on both sides (substring match).

Every aggregate is `None` when no case qualifies (never a misleading 0).
"""

import re
import unicodedata
from dataclasses import dataclass

RECALL_K = 10
_DIGITS = 4
_SPACE = re.compile(r"\s+")


def normalise(text):
    """Lenient, symmetric text form for phrase matching: NFKC, Turkish-aware
    lowercasing (`İ` -> `i`), diacritics dropped (`ş` -> `s`), dotless `ı`
    -> `i`, whitespace collapsed. So "İstanbul", "ISTANBUL" and "istanbul"
    match, and so do "Işık" and "isik"."""
    text = unicodedata.normalize("NFKC", str(text)).replace("İ", "i").lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return _SPACE.sub(" ", text.replace("ı", "i")).strip()


def contains(haystack, phrase):
    needle = normalise(phrase)
    return bool(needle) and needle in normalise(haystack)


def recall_at_k(retrieved_refs, relevant_refs, k=RECALL_K):
    """`None` when the case names no relevant row."""
    relevant = set(relevant_refs)
    if not relevant:
        return None
    return len(relevant.intersection(list(retrieved_refs)[:k])) / len(relevant)


def leaked(context_text, must_not_contain):
    """`None` when the case has no leak terms."""
    if not must_not_contain:
        return None
    return any(contains(context_text, term) for term in must_not_contain)


def answer_correct(answer, must_contain, must_not_contain):
    return all(contains(answer, p) for p in must_contain) and not any(
        contains(answer, t) for t in must_not_contain
    )


def temporal_correct(*, is_temporal, recall, leak):
    if not is_temporal:
        return None
    return recall == 1.0 and not leak


@dataclass
class CaseOutcome:
    """One case's metric inputs. Held in memory only; never written out (the
    result file carries aggregates only, D9)."""

    recall: float | None = None
    leak: bool | None = None
    temporal: bool | None = None
    answered: bool = False
    correct: bool | None = None


def _mean(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), _DIGITS)


def aggregate(outcomes):
    """The aggregate metrics of a run (the D9 result-file metric keys)."""
    return {
        "recall_at_10": _mean(o.recall for o in outcomes),
        "leak_rate": _mean(o.leak for o in outcomes),
        "temporal_correctness": _mean(o.temporal for o in outcomes),
        "answer_accuracy": _mean(o.correct for o in outcomes if o.answered),
    }


def compare_verdict(arm_a, arm_b):
    """A retrieval compare (D8: the closeout flips the default only if the
    candidate is >= the baseline on recall@10 and answer accuracy): `pass`
    when arm B is at least arm A on both (answer accuracy only when both
    arms have one) and leaks no more; `inconclusive` when recall is missing
    on either side; otherwise `fail`."""
    if arm_a.get("recall_at_10") is None or arm_b.get("recall_at_10") is None:
        return "inconclusive"
    checks = [arm_b["recall_at_10"] >= arm_a["recall_at_10"]]
    if arm_a.get("answer_accuracy") is not None and arm_b.get("answer_accuracy") is not None:
        checks.append(arm_b["answer_accuracy"] >= arm_a["answer_accuracy"])
    if arm_a.get("leak_rate") is not None and arm_b.get("leak_rate") is not None:
        checks.append(arm_b["leak_rate"] <= arm_a["leak_rate"])
    return "pass" if all(checks) else "fail"


# D11 gate: a purpose may be compressed only at <= 1 point accuracy loss.
COMPRESSION_MAX_ACCURACY_LOSS = 0.01


def compression_verdict(arm_a, arm_b, *, compression_cases, applied_calls):
    """A compression compare (D11). Never vacuous:

    - `inconclusive` when the set has no `compression`-tagged case, when arm
      B compressed nothing (no applied call, or `post_tokens` not below
      `pre_tokens`), or when either arm has no answer accuracy;
    - `fail` when arm B loses more than 1 point of answer accuracy or leaks
      more than arm A;
    - otherwise `pass`."""
    pre, post = arm_b.get("pre_tokens"), arm_b.get("post_tokens")
    if compression_cases == 0 or applied_calls == 0 or pre is None or post is None or post >= pre:
        return "inconclusive"
    if arm_a.get("answer_accuracy") is None or arm_b.get("answer_accuracy") is None:
        return "inconclusive"
    loss = arm_a["answer_accuracy"] - arm_b["answer_accuracy"]
    if loss > COMPRESSION_MAX_ACCURACY_LOSS + 1e-9:
        return "fail"
    if (arm_b.get("leak_rate") or 0) > (arm_a.get("leak_rate") or 0):
        return "fail"
    return "pass"
