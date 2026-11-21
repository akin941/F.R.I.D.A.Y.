"""The eval compression arm (Phase 8B branch 6, brief D11).

`compare --compression chat` on the public set with a fake sidecar: both
arms answer under `chat` with the eval-only budget; arm B's adapter payload
differs from arm A's for every `compression`-tagged case and is identical
for every case without `history`; an identity sidecar or a set without a
`compression` case is `inconclusive`, never `pass`; the loader refuses a
short `compression` case; `memory.eval_answer` is never compressed. Fake
provider, fake sidecar; no real LLM, no real Headroom."""

# The shared eval fixtures are imported from test_evals; pytest injects them
# as arguments, which ruff reads as redefinitions.
# ruff: noqa: F811

import copy
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command

from ai_core import compression
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import AIBudget, ModelUsage
from memory.evals import metrics
from memory.evals.loader import LoaderError, load_public_cases, parse_case_file
from memory.evals.results import read_result_file, validate_result
from memory.evals.runner import (
    ANSWER_PURPOSE,
    EvalRefused,
    RunConfig,
    run_cases,
    run_compression_compare,
)
from memory.evals.schema import COMPRESSION_TAG
from memory.tests.test_evals import (  # noqa: F401 - pytest fixtures
    case_line,
    cases_of,
    eval_mode,
    make_case,
    prompt,
    providers,
)

pytestmark = pytest.mark.django_db

CHAT = "chat"


@pytest.fixture
def compression_eval(eval_mode):  # noqa: F811 - the imported fixture
    eval_mode.AI_LEARNING_EVAL_PURPOSES = (ANSWER_PURPOSE, CHAT)
    eval_mode.AI_COMPRESSION_ENABLED = False
    eval_mode.AI_COMPRESSION_PURPOSES = frozenset()
    return eval_mode


class Sidecar:
    def __init__(self, shorten=True):
        self.shorten = shorten
        self.requests = []

    def compress(self, messages, *, model):
        self.requests.append(copy.deepcopy(messages))
        out = [
            {"role": "assistant", "content": m["content"][: len(m["content"]) // 3]}
            if self.shorten
            else dict(m)
            for m in messages
        ]
        return {
            "messages": out,
            "tokens_before": sum(len(m["content"]) // 4 for m in messages),
            "tokens_after": sum(len(m["content"]) // 4 for m in out),
            "transforms_applied": ["fake"],
            "lossless": not self.shorten,
        }


@pytest.fixture
def sidecar(monkeypatch):
    fake = Sidecar()
    monkeypatch.setattr(compression, "get_client", lambda: fake)
    return fake


@pytest.fixture
def chat_llm(monkeypatch):
    """Capture every adapter payload by (arm, purpose); the answer contains
    every expected phrase of the case, so both arms score the same."""
    calls = {"a": [], "b": []}
    answers = {}
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        arm = "b" if django_settings.AI_COMPRESSION_ENABLED else "a"
        calls[arm].append((purpose, copy.deepcopy(messages)))
        if purpose in (CHAT, ANSWER_PURPOSE):
            return answers.get(messages[-1]["content"], "I do not know.")
        return original(self, purpose, messages)

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)

    class Capture:
        def __init__(self):
            self.calls = calls
            self.answers = answers

        def answer_all(self, cases):
            for case in cases:
                answers[case.question] = " ".join(case.expected.must_contain) or "ok"

    return Capture()


def _compare(cases, purpose=CHAT, results_dir=None, tmp_path=None):
    return run_compression_compare(
        cases,
        set_name="public",
        purpose=purpose,
        results_dir=results_dir or tmp_path,
    )


def test_compare_on_the_public_set_compresses_only_history_cases(
    compression_eval, providers, prompt, sidecar, chat_llm, tmp_path
):
    cases = load_public_cases()
    chat_llm.answer_all(cases)
    (arm_a, arm_b), paths = _compare(cases, tmp_path=tmp_path)

    # Both arms answer under `chat`, never `memory.eval_answer`.
    rows = ModelUsage.objects.filter(context_report__eval_run_id=arm_a["run_id"])
    assert set(rows.values_list("purpose", flat=True)) == {CHAT}
    assert rows.filter(context_report__eval_arm="a").count() == len(cases)
    assert rows.filter(context_report__eval_arm="b").count() == len(cases)
    assert not rows.filter(context_report__eval_arm="a", context_report__has_key="compression")

    a_payloads = [p for purpose, p in chat_llm.calls["a"] if purpose == CHAT]
    b_payloads = [p for purpose, p in chat_llm.calls["b"] if purpose == CHAT]
    assert len(a_payloads) == len(b_payloads) == len(cases)
    tagged = 0
    for case, a_payload, b_payload in zip(cases, a_payloads, b_payloads, strict=True):
        assert a_payload[-1]["content"] == case.question
        if COMPRESSION_TAG in case.tags:
            tagged += 1
            assert a_payload != b_payload, case.id
            assert a_payload[0] == b_payload[0]  # the system message never changes
            for x, y in zip(a_payload, b_payload, strict=True):
                if x["role"] != "assistant":
                    assert x == y
        if not case.history:
            assert a_payload == b_payload, case.id
    assert tagged >= 10

    assert arm_b["pre_tokens"] > arm_b["post_tokens"] > 0
    assert arm_a["pre_tokens"] is None and arm_a["post_tokens"] is None
    assert arm_a["compression_purpose"] == arm_b["compression_purpose"] == CHAT
    assert arm_a["answer_accuracy"] == arm_b["answer_accuracy"] == 1.0
    assert arm_b["verdict"] == "pass" and arm_a["verdict"] is None
    # Exactly the D9 keys, and recordable.
    for path in paths:
        values = validate_result(read_result_file(path))
        assert values["compression_purpose"] == CHAT


def test_an_identity_sidecar_is_inconclusive(
    compression_eval, providers, prompt, chat_llm, monkeypatch, tmp_path
):
    fake = Sidecar(shorten=False)
    monkeypatch.setattr(compression, "get_client", lambda: fake)
    cases = load_public_cases()
    chat_llm.answer_all(cases)
    (_, arm_b), _ = _compare(cases, tmp_path=tmp_path)
    assert fake.requests  # the sidecar was asked
    assert arm_b["pre_tokens"] == arm_b["post_tokens"]
    assert arm_b["verdict"] == "inconclusive"


def test_a_set_without_compression_cases_is_inconclusive(
    compression_eval, providers, prompt, sidecar, chat_llm, tmp_path
):
    cases = cases_of(make_case(id="plain-1"), make_case(id="plain-2", question="Which editor?"))
    chat_llm.answer_all(cases)
    (_, arm_b), _ = _compare(cases, tmp_path=tmp_path)
    assert sidecar.requests == []
    assert arm_b["verdict"] == "inconclusive"
    assert (arm_b["pre_tokens"], arm_b["post_tokens"]) == (0, 0)


def test_the_loader_refuses_a_short_compression_case(tmp_path):
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a" * 900},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "b" * 900},
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text(case_line(tags=[COMPRESSION_TAG], history=history) + "\n")
    with pytest.raises(LoaderError, match="cases.jsonl:1: .*assistant messages"):
        parse_case_file(path)
    history[3]["content"] = "b" * 1100
    path.write_text(case_line(tags=[COMPRESSION_TAG], history=history) + "\n")
    assert parse_case_file(path)[0].history


def test_memory_eval_answer_is_never_compressed(
    compression_eval, providers, prompt, sidecar, chat_llm
):
    compression_eval.AI_COMPRESSION_ENABLED = True
    compression_eval.AI_COMPRESSION_PURPOSES = frozenset({ANSWER_PURPOSE, CHAT})
    cases = [c for c in load_public_cases() if COMPRESSION_TAG in c.tags][:3]
    run_cases(cases, RunConfig("public", "run"), results_dir=None)
    assert sidecar.requests == []
    rows = ModelUsage.objects.filter(purpose=ANSWER_PURPOSE)
    assert rows.count() == 3
    assert all(r.context_report["compression"]["skipped"] == "bypass" for r in rows)
    with pytest.raises(EvalRefused, match="not a compression-eligible purpose"):
        run_compression_compare(cases, set_name="public", purpose=ANSWER_PURPOSE, results_dir=None)


@pytest.mark.parametrize("purpose", ["router.intent", "memory.reflection", "made.up"])
def test_bypass_and_unclassified_purposes_are_refused(compression_eval, purpose):
    with pytest.raises(EvalRefused, match="not a compression-eligible purpose"):
        run_compression_compare([], set_name="public", purpose=purpose, results_dir=None)


def test_a_purpose_the_eval_cap_does_not_cover_is_refused(compression_eval, providers, prompt):
    compression_eval.AI_LEARNING_EVAL_PURPOSES = (ANSWER_PURPOSE,)
    with pytest.raises(EvalRefused, match="does not cover 'chat'"):
        run_compression_compare([], set_name="public", purpose=CHAT, results_dir=None)
    compression_eval.AI_LEARNING_EVAL_PURPOSES = (ANSWER_PURPOSE, CHAT)
    AIBudget.objects.create(
        name="learning_eval", purposes=[ANSWER_PURPOSE], monthly_limit_usd=Decimal("5")
    )
    with pytest.raises(EvalRefused, match="budget row does not cover"):
        run_compression_compare([], set_name="public", purpose=CHAT, results_dir=None)


def test_the_compare_refuses_the_live_settings(db, settings):
    settings.MEMORY_EVAL_MODE = False
    with pytest.raises(EvalRefused, match="eval settings"):
        run_compression_compare([], set_name="public", purpose=CHAT, results_dir=None)


def test_the_eval_settings_cover_chat_and_keep_compression_off():
    source = (Path(__file__).resolve().parents[2] / "config" / "settings" / "eval.py").read_text()
    assert 'AI_LEARNING_EVAL_PURPOSES = ("memory.eval_answer", "chat")' in source
    assert "AI_COMPRESSION_ENABLED = False" in source


def test_compare_command_prints_tokens_and_verdict(
    compression_eval, providers, prompt, sidecar, chat_llm, capsys
):
    chat_llm.answer_all(load_public_cases())
    call_command("memory_eval", "compare", "--set", "public", "--compression", CHAT)
    out = capsys.readouterr().out
    assert "chat compression off (arm a)" in out and "chat compression on (arm b)" in out
    assert "pre_tokens" in out and "post_tokens" in out and "pass" in out


def test_compare_command_options(compression_eval, providers, prompt):
    with pytest.raises(CommandError):
        call_command(
            "memory_eval",
            "compare",
            "--set",
            "public",
            "--compression",
            CHAT,
            "--retrieval-mode",
            "legacy",
        )
    with pytest.raises(CommandError, match="drop --retrieval-only"):
        call_command(
            "memory_eval", "compare", "--set", "public", "--compression", CHAT, "--retrieval-only"
        )


@pytest.mark.parametrize(
    ("a", "b", "cases", "applied", "verdict"),
    [
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.8, "pre_tokens": 10, "post_tokens": 5},
            3,
            2,
            "pass",
        ),
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.795, "pre_tokens": 10, "post_tokens": 5},
            3,
            2,
            "pass",
        ),
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.78, "pre_tokens": 10, "post_tokens": 5},
            3,
            2,
            "fail",
        ),
        (
            {"answer_accuracy": 0.8, "leak_rate": 0.0},
            {"answer_accuracy": 0.8, "leak_rate": 0.1, "pre_tokens": 10, "post_tokens": 5},
            3,
            2,
            "fail",
        ),
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.8, "pre_tokens": 10, "post_tokens": 10},
            3,
            2,
            "inconclusive",
        ),
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.8, "pre_tokens": 10, "post_tokens": 5},
            0,
            2,
            "inconclusive",
        ),
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.8, "pre_tokens": 10, "post_tokens": 5},
            3,
            0,
            "inconclusive",
        ),
        (
            {"answer_accuracy": None},
            {"answer_accuracy": 0.8, "pre_tokens": 10, "post_tokens": 5},
            3,
            2,
            "inconclusive",
        ),
        (
            {"answer_accuracy": 0.8},
            {"answer_accuracy": 0.8, "pre_tokens": None, "post_tokens": None},
            3,
            2,
            "inconclusive",
        ),
    ],
)
def test_compression_verdict(a, b, cases, applied, verdict):
    assert (
        metrics.compression_verdict(a, b, compression_cases=cases, applied_calls=applied) == verdict
    )
