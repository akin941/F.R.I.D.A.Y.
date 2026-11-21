"""Phase 8B branch 4 (brief D8, D9): the `hybrid` eval arm.

`memory_eval compare --retrieval hybrid` on the public set runs and reports
both arms (legacy arm A, hybrid arm B) side by side; the hybrid retriever
renders the `<memory_fact>` block the chat turn uses, and a fact case's
relevant fact is ranked."""

import json
from pathlib import Path

import pytest
from django.core.management import call_command

from memory.evals.retrieval import retriever_for
from memory.evals.runner import run_compare
from memory.evals.world import ensure_world, load_case
from memory.models import MemoryFact
from memory.tests.test_evals import (  # noqa: F401 (fixtures)
    cases_of,
    eval_mode,
    llm,
    make_case,
    prompt,
    providers,
)

pytestmark = pytest.mark.django_db


def test_compare_retrieval_hybrid_reports_both_arms(eval_mode, providers, prompt, llm, capsys):  # noqa: F811
    call_command(
        "memory_eval", "compare", "--set", "public", "--retrieval", "hybrid", "--retrieval-only"
    )
    out = capsys.readouterr().out
    assert "legacy (arm a)" in out and "hybrid (arm b)" in out
    assert "recall_at_10" in out
    files = sorted(Path(eval_mode.MEMORY_EVAL_RESULTS_DIR).glob("*.json"))
    arms = {json.loads(f.read_text())["arm"]: json.loads(f.read_text()) for f in files}
    assert set(arms) == {"a", "b"}
    assert arms["a"]["retrieval_mode"] == "legacy" and arms["b"]["retrieval_mode"] == "hybrid"
    assert arms["b"]["recall_at_10"] is not None
    assert arms["b"]["leak_rate"] == 0.0
    assert not llm.calls  # retrieval only: no answer call


def test_the_hybrid_arm_ranks_a_fact_and_renders_the_fact_block(eval_mode, providers):  # noqa: F811
    case = cases_of(
        make_case(
            setup={
                "facts": [
                    {
                        "ref": "f1",
                        "subject": {"name": "api", "kind": "service"},
                        "predicate": "deployed_to",
                        "statement": "api is deployed to Hetzner",
                        "valid_from": "2026-09-15",
                        "object": {"name": "hetzner", "kind": "tool"},
                    }
                ]
            },
            question="Where is the api deployed?",
            expected={"must_contain": ["hetzner"], "must_not_contain": [], "relevant_ids": ["f1"]},
        )
    )[0]
    world = ensure_world()
    loaded = load_case(world, case)
    # The production fact embedding job ran for the setup fact.
    assert MemoryFact.all_objects.get(statement="api is deployed to Hetzner").embedding is not None
    retrieved = retriever_for("hybrid")(
        actor=world.owner,
        workspace=world.workspace("personal"),
        query_text=case.question,
        personal_data=False,
    )
    assert loaded.refs(retrieved.ranked_ids) == ["f1"]
    assert "<memory_fact>" in retrieved.context_text
    assert "api is deployed to Hetzner (2026-09-1" in retrieved.context_text


def test_run_compare_with_the_hybrid_candidate(eval_mode, providers, prompt, llm, tmp_path):  # noqa: F811
    (arm_a, arm_b), paths = run_compare(
        cases_of(make_case()),
        set_name="public",
        candidate_mode="hybrid",
        retrieval_only=True,
        results_dir=tmp_path,
    )
    assert (arm_a["retrieval_mode"], arm_b["retrieval_mode"]) == ("legacy", "hybrid")
    assert arm_b["verdict"] in {"pass", "fail", "inconclusive"}
    assert len(paths) == 2
