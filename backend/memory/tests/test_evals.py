"""Phase 8B branch 3 (brief D9): the memory eval set.

Case schema bounds and unknown keys; metrics on hand-computed examples; the
public seed and the private-directory loader; the "never commit private
cases" scan; the eval-settings refusal; the runner on FakeAdapter (answers
keyed by case id, one `ModelUsage` per answered case with `eval_run_id`);
the eligibility of eval setup rows (`private` cases, cloud-only providers,
non-`cloud_eligible` rows never in a payload); the `learning_eval` cap
across runs and across a recreated eval database; the metrics-only
`MemoryEvalRun` and `memory_eval record`; and the owner-only
`GET /api/v1/memory/evals/runs` (role x endpoint).

No real LLM: FakeAdapter only, its `memory.eval_answer` text overridden per
test.
"""

import ast
import json
import os
import secrets
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.db import models as djm
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.devices import enroll_node_device, issue_pairing_code
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice, User
from accounts.services import register_user
from ai_core.adapters.fake import FakeAdapter
from ai_core.gateway import InferenceGateway
from ai_core.models import AIBudget, ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from core.db import WorkspaceScopedModel
from memory.evals import metrics
from memory.evals.loader import (
    CASES_DIR,
    LoaderError,
    check_private_dir,
    load_private_cases,
    load_public_cases,
    stray_case_files,
)
from memory.evals.results import (
    RESULT_KEYS,
    ResultError,
    ensure_dir,
    validate_result,
    write_result,
)
from memory.evals.runner import (
    ANSWER_PURPOSE,
    PROMPT_NAME,
    BudgetGuard,
    EvalRefused,
    RunConfig,
    run_cases,
    run_compare,
)
from memory.evals.schema import CaseError, validate_case
from memory.evals.serializers import RUN_FIELDS
from memory.evals.world import EVAL_OWNER_EMAIL, clear_case_rows, ensure_world, load_case
from memory.models import Memory, MemoryEntity, MemoryEvalRun, MemoryFact
from memory.tests.ingestion_helpers import PASSWORD
from workspaces.models import Workspace, WorkspaceMember

BACKEND = Path(__file__).resolve().parents[2]
URL = "/api/v1/memory/evals/runs"


# --- helpers -----------------------------------------------------------------------


def make_case(**overrides):
    raw = {
        "id": "c1",
        "workspace_ref": "personal",
        "tags": ["retrieval"],
        "setup": {
            "memories": [
                {
                    "ref": "m1",
                    "type": "preference",
                    "title": "Editor",
                    "content": "The owner prefers Neovim for Python.",
                }
            ]
        },
        "question": "Which editor does the owner prefer?",
        "expected": {"must_contain": ["neovim"], "relevant_ids": ["m1"]},
    }
    raw.update(overrides)
    return raw


def case_line(**overrides):
    return json.dumps(make_case(**overrides), ensure_ascii=False)


@pytest.fixture
def eval_mode(settings, tmp_path):
    settings.MEMORY_EVAL_MODE = True
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.MEMORY_EVAL_PRIVATE_DIR = str(tmp_path / "friday-evals")
    settings.MEMORY_EVAL_RESULTS_DIR = str(tmp_path / "public-results")
    return settings


@pytest.fixture
def prompt(db):
    template = PromptTemplate.objects.create(name=PROMPT_NAME, purpose=ANSWER_PURPOSE)
    version = PromptVersion.objects.create(template=template, version=1, content="Answer.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def chat_provider(*, name="fake-local", personal_data_allowed=True, priced=False):
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=personal_data_allowed,
        input_price_per_mtok=1 if priced else 0,
    )


@pytest.fixture
def providers(db):
    chat = chat_provider()
    ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
        personal_data_allowed=True,
    )
    return chat


@pytest.fixture
def llm(monkeypatch):
    """Capture every eval answer call; answers come from `answers[question]`
    (keyed by case id through `by_id`)."""
    calls, answers = [], {}
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        if purpose != ANSWER_PURPOSE:
            return original(self, purpose, messages)
        calls.append(messages)
        return answers.get(messages[-1]["content"], "I do not know.")

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)

    class Capture:
        def __init__(self):
            self.calls = calls
            self.answers = answers

        def by_id(self, cases, mapping):
            for case in cases:
                if case.id in mapping:
                    answers[case.question] = mapping[case.id]

        def payload(self, index=-1):
            return "\n".join(m["content"] for m in calls[index])

    return Capture()


def answer_rows():
    return ModelUsage.objects.filter(purpose=ANSWER_PURPOSE)


def cases_of(*raws):
    return [validate_case(raw) for raw in raws]


# --- case schema -------------------------------------------------------------------


def test_a_minimal_case_validates():
    case = validate_case(make_case())
    assert case.id == "c1" and case.sensitivity == "normal" and not case.is_private
    assert [m.ref for m in case.memories] == ["m1"]
    assert case.expected.relevant_ids == ("m1",)


@pytest.mark.parametrize(
    "mutate, rule",
    [
        (lambda r: r.update(cases=[]), "unknown key"),
        (lambda r: r.update(workspace_ref="Personal!"), "slug"),
        (lambda r: r.pop("question"), "missing key"),
        (lambda r: r.update(question=""), "must not be empty"),
        (lambda r: r.update(question="x" * 2001), "longer than 2000"),
        (lambda r: r.update(tags=[f"t{i}" for i in range(11)]), "more than 10"),
        (lambda r: r.update(tags=["a", "a"]), "duplicate tag"),
        (lambda r: r.update(sensitivity="secret"), "one of normal, private"),
        (lambda r: r["setup"].update(chunks=[]), "unknown key"),
        (lambda r: r["setup"]["memories"][0].update(embedding=[1]), "unknown key"),
        (lambda r: r["setup"]["memories"][0].update(type="diary"), "must be one of"),
        (lambda r: r["setup"]["memories"][0].update(content="x" * 4001), "longer than 4000"),
        (lambda r: r["setup"]["memories"][0].update(updated_at="yesterday"), "ISO date"),
        (
            lambda r: r["setup"]["memories"][0].update(updated_at="2026-10-01T10:00:00"),
            "UTC offset",
        ),
        (lambda r: r["setup"].update(memories=[r["setup"]["memories"][0]] * 2), "duplicate ref"),
        (lambda r: r["expected"].update(score=1), "unknown key"),
        (lambda r: r["expected"].update(relevant_ids=["m9"]), "names no setup row"),
        (lambda r: r["expected"].update(must_contain=[""]), "must not be empty"),
        (lambda r: r["expected"].update(must_contain=["x"] * 11), "more than 10"),
        (lambda r: r.update(history=[{"role": "system", "content": "x"}]), "user, assistant"),
        (lambda r: r.update(history=[{"role": "user", "content": "x", "n": 1}]), "unknown key"),
        (lambda r: r.update(history=[{"role": "user", "content": "x"}] * 13), "more than 12"),
        (lambda r: r.update(tags=["compression"]), "assistant messages"),
    ],
)
def test_case_schema_refuses(mutate, rule):
    raw = make_case()
    mutate(raw)
    with pytest.raises(CaseError) as excinfo:
        validate_case(raw)
    assert rule in str(excinfo.value)
    assert "Neovim" not in str(excinfo.value)  # never quotes case text


def _fact(ref, **overrides):
    row = {
        "ref": ref,
        "subject": {"name": "api", "kind": "service"},
        "predicate": "deployed_to",
        "object": {"name": "hetzner", "kind": "tool"},
        "statement": "api is deployed to hetzner",
        "valid_from": "2026-01-01",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "facts, rule",
    [
        ([_fact("f1", object_text="x")], "exactly one of"),
        ([{k: v for k, v in _fact("f1").items() if k != "object"}], "exactly one of"),
        ([_fact("f1", predicate="imports")], "must be one of"),
        ([_fact("f1", subject={"name": "m", "kind": "code_module"})], "must be one of"),
        ([_fact("f1", valid_to="2025-01-01")], "earlier than valid_from"),
        ([_fact("f1", superseded_by="f2"), _fact("f2")], "needs a valid_to"),
        ([_fact("f1", valid_to="2026-02-01", superseded_by="f9")], "another fact"),
        (
            [
                _fact("f1", valid_to="2026-02-01", superseded_by="f2"),
                _fact("f2", predicate="uses"),
            ],
            "same workspace, subject and predicate",
        ),
    ],
)
def test_fact_rows_are_checked(facts, rule):
    raw = make_case(setup={"facts": facts}, expected={})
    with pytest.raises(CaseError) as excinfo:
        validate_case(raw)
    assert rule in str(excinfo.value)


def test_a_private_case_marks_every_setup_row_private():
    case = validate_case(
        make_case(
            sensitivity="private",
            setup={
                "memories": make_case()["setup"]["memories"],
                "facts": [_fact("f1")],
                "episodes": [
                    {
                        "ref": "e1",
                        "source": "git",
                        "kind": "commit",
                        "occurred_at": "2026-11-01",
                        "owner_text": "fix the api",
                    }
                ],
            },
        )
    )
    assert case.is_private
    rows = [*case.memories, *case.facts, *case.episodes]
    assert {row.sensitivity for row in rows} == {"private"}


def test_a_compression_case_needs_long_assistant_history():
    history = [
        {"role": "user", "content": "plan?"},
        {"role": "assistant", "content": "a" * 1000},
        {"role": "user", "content": "more?"},
        {"role": "assistant", "content": "b" * 1000},
    ]
    assert validate_case(make_case(tags=["compression"], history=history)).history
    with pytest.raises(CaseError):
        validate_case(make_case(tags=["compression"], history=history[:2]))


# --- metrics -----------------------------------------------------------------------


def test_normalise_is_turkish_aware_and_symmetric():
    assert metrics.normalise("İstanbul") == metrics.normalise("ISTANBUL") == "istanbul"
    assert metrics.normalise("Işık  Şahin") == "isik sahin"
    assert metrics.contains("Deploy hedefi: HETZNER.", "hetzner")
    assert metrics.contains("Çarşamba 18:00", "carsamba")
    assert not metrics.contains("anything", "")


def test_recall_at_k_on_hand_computed_examples():
    assert metrics.recall_at_k(["a", "b", "c"], ["a", "c"]) == 1.0
    assert metrics.recall_at_k(["a", "x", "y"], ["a", "c"]) == 0.5
    ranked = [f"r{i}" for i in range(10)] + ["late"]
    assert metrics.recall_at_k(ranked, ["late"]) == 0.0  # 11th place is out of the top 10
    assert metrics.recall_at_k(["a"], []) is None


def test_leak_answer_and_temporal_checks():
    assert metrics.leaked("<memory>PayrollBox pays</memory>", ["payrollbox"]) is True
    assert metrics.leaked("nothing here", ["payrollbox"]) is False
    assert metrics.leaked("anything", []) is None
    assert metrics.answer_correct("Hetzner'de çalışıyor.", ["hetzner"], ["heroku"])
    assert not metrics.answer_correct("Heroku, then Hetzner", ["hetzner"], ["heroku"])
    assert not metrics.answer_correct("Hetzner", ["hetzner", "2026-09"], [])
    assert metrics.temporal_correct(is_temporal=True, recall=1.0, leak=False) is True
    assert metrics.temporal_correct(is_temporal=True, recall=1.0, leak=True) is False
    assert metrics.temporal_correct(is_temporal=True, recall=0.5, leak=None) is False
    assert metrics.temporal_correct(is_temporal=False, recall=1.0, leak=False) is None


def test_aggregate_means_and_nulls():
    outcome = metrics.CaseOutcome
    result = metrics.aggregate(
        [
            outcome(recall=1.0, leak=False, temporal=True, answered=True, correct=True),
            outcome(recall=0.5, leak=True, temporal=False, answered=True, correct=False),
            outcome(recall=None, leak=None, temporal=None, answered=False),
        ]
    )
    assert result == {
        "recall_at_10": 0.75,
        "leak_rate": 0.5,
        "temporal_correctness": 0.5,
        "answer_accuracy": 0.5,
    }
    assert set(metrics.aggregate([]).values()) == {None}


def test_compare_verdict():
    a = {"recall_at_10": 0.5, "answer_accuracy": 0.5, "leak_rate": 0.0}
    assert metrics.compare_verdict(a, {**a, "recall_at_10": 0.6}) == "pass"
    assert metrics.compare_verdict(a, {**a, "answer_accuracy": 0.4}) == "fail"
    assert metrics.compare_verdict(a, {**a, "leak_rate": 0.1}) == "fail"
    assert metrics.compare_verdict(a, {**a, "recall_at_10": None}) == "inconclusive"


# --- loaders -----------------------------------------------------------------------


def test_the_public_seed_loads_and_covers_the_d9_kinds():
    cases = load_public_cases()
    assert 55 <= len(cases) <= 80
    assert len({c.id for c in cases}) == len(cases)
    tags = {t for c in cases for t in c.tags}
    assert {"leak", "temporal", "supersession", "two_hop", "transcript", "tr", "en"} <= tags
    assert {"codeswitch", "compression", "private"} <= tags
    refs = {c.workspace_ref for c in cases} | {r for c in cases for r in c.workspace_refs}
    assert {"personal", "acme"} <= refs
    # Leak traps: a personal question with a firm term it must not see.
    assert any(
        c.workspace_ref == "personal" and c.expected.must_not_contain and len(c.workspace_refs) > 1
        for c in cases
    )


def _private_dir(tmp_path, mode=0o700):
    directory = tmp_path / "friday-evals"
    directory.mkdir()
    (directory / "work.jsonl").write_text(case_line() + "\n", encoding="utf-8")
    directory.chmod(mode)
    return directory


def test_the_private_loader_reads_a_0700_directory(tmp_path):
    directory = _private_dir(tmp_path)
    (directory / "results").mkdir()
    (directory / "results" / "old.json").write_text("{}")
    assert [c.id for c in load_private_cases(directory)] == ["c1"]


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o701])
def test_the_private_loader_refuses_a_directory_others_can_read(tmp_path, mode):
    directory = _private_dir(tmp_path, mode)
    with pytest.raises(LoaderError, match="readable by others"):
        load_private_cases(directory)


@pytest.mark.parametrize("name", ["notes.txt", "cases.json", "work.jsonl.bak"])
def test_the_private_loader_refuses_any_non_jsonl_file(tmp_path, name):
    directory = _private_dir(tmp_path)
    (directory / name).write_text(case_line())
    with pytest.raises(LoaderError, match=r"only \*\.jsonl"):
        check_private_dir(directory)


def test_the_private_loader_refuses_symlinks_and_other_directories(tmp_path):
    directory = _private_dir(tmp_path)
    (directory / "more").mkdir()
    with pytest.raises(LoaderError, match="only a 'results/'"):
        check_private_dir(directory)
    (directory / "more").rmdir()
    os.symlink(directory / "work.jsonl", directory / "link.jsonl")
    with pytest.raises(LoaderError, match="symlinks"):
        check_private_dir(directory)
    link = tmp_path / "dirlink"
    os.unlink(directory / "link.jsonl")
    os.symlink(directory, link)
    with pytest.raises(LoaderError, match="not a real directory"):
        check_private_dir(link)
    with pytest.raises(LoaderError, match="does not exist"):
        check_private_dir(tmp_path / "missing")


def test_loader_errors_name_the_line_not_its_content(tmp_path):
    directory = _private_dir(tmp_path)
    secret_line = case_line(question="the confidential acquisition of Initech", cases=1)
    (directory / "work.jsonl").write_text(case_line() + "\n" + secret_line + "\n")
    with pytest.raises(LoaderError) as excinfo:
        load_private_cases(directory)
    assert "work.jsonl:2" in str(excinfo.value)
    assert "Initech" not in str(excinfo.value)
    (directory / "work.jsonl").write_text(case_line() + "\n" + case_line() + "\n")
    with pytest.raises(LoaderError, match="duplicate case id"):
        load_private_cases(directory)


def test_no_case_file_is_committed_outside_public():
    """D9: private cases are never committed."""
    assert stray_case_files(CASES_DIR) == []


def test_the_stray_scan_catches_a_file_outside_public(tmp_path):
    root = tmp_path / "cases"
    (root / "public").mkdir(parents=True)
    (root / "public" / "seed.jsonl").write_text(case_line())
    assert stray_case_files(root) == []
    (root / "private").mkdir()
    (root / "private" / "work.jsonl").write_text(case_line())
    (root / "stray.jsonl").write_text(case_line())
    assert {str(p) for p in stray_case_files(root)} == {"private/work.jsonl", "stray.jsonl"}


def test_gitignore_keeps_the_owner_eval_dir_out():
    gitignore = (BACKEND.parent / ".gitignore").read_text()
    assert "friday-evals/" in gitignore


# --- settings isolation ------------------------------------------------------------


def test_run_subcommands_refuse_the_live_settings(db, settings):
    settings.MEMORY_EVAL_MODE = False
    for action in ("run", "baseline", "compare"):
        args = [action, "--set", "public"]
        if action == "compare":
            args += ["--retrieval-mode", "legacy"]
        with pytest.raises(CommandError, match="config.settings.eval"):
            call_command("memory_eval", *args)
    with pytest.raises(EvalRefused):
        run_cases(cases_of(make_case()), RunConfig("public", "run"), results_dir=None)
    assert not ModelUsage.objects.exists()
    assert not Memory.objects.exists()


def _import_settings(module, **env):
    environment = {**os.environ, "DJANGO_DEBUG": "true", **env}
    code = (
        f"import importlib, json; m = importlib.import_module({module!r}); "
        "print(json.dumps([m.DATABASES['default']['NAME'], m.MEMORY_EVAL_MODE]))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_eval_settings_use_their_own_database():
    live = "postgres://u:p@localhost:5432/friday"
    out = _import_settings("config.settings.eval", DATABASE_URL=live, MEMORY_EVAL_DATABASE_NAME="")
    assert out.returncode != 0 and "MEMORY_EVAL_DATABASE_NAME" in out.stderr
    out = _import_settings(
        "config.settings.eval", DATABASE_URL=live, MEMORY_EVAL_DATABASE_NAME="friday"
    )
    assert out.returncode != 0 and "other than the live one" in out.stderr
    env = {"DATABASE_URL": live, "MEMORY_EVAL_DATABASE_NAME": "friday_eval"}
    out = _import_settings("config.settings.eval", **env)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["friday_eval", True]
    out = _import_settings("config.settings.dev", DATABASE_URL=live)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["friday", False]


# --- the runner on FakeAdapter -----------------------------------------------------


def test_a_public_run_answers_keyed_by_case_id(eval_mode, providers, prompt, llm, tmp_path):
    cases = cases_of(
        make_case(id="zebra-one", tags=["quokka"]),
        make_case(
            id="zebra-two",
            question="Which shell?",
            setup={
                "memories": [
                    {"ref": "m1", "type": "preference", "title": "Shell", "content": "fish shell"}
                ]
            },
            expected={"must_contain": ["fish"], "relevant_ids": ["m1"]},
        ),
    )
    llm.by_id(cases, {"zebra-one": "Neovim.", "zebra-two": "bash"})
    result, path = run_cases(
        cases, RunConfig("public", "baseline"), results_dir=tmp_path / "results"
    )
    assert (result["total"], result["answered"], result["no_provider"]) == (2, 2, 0)
    assert result["recall_at_10"] == 1.0 and result["leak_rate"] is None
    assert result["answer_accuracy"] == 0.5
    rows = answer_rows()
    assert rows.count() == 2  # one ModelUsage per answered case
    assert {r.context_report["eval_run_id"] for r in rows} == {result["run_id"]}
    assert all(r.workspace.owner.email == EVAL_OWNER_EMAIL for r in rows)
    # The D11 layout: system context, history, the question last.
    first = llm.calls[0]
    assert first[0]["role"] == "system" and "Neovim" in first[0]["content"]
    assert first[-1] == {"role": "user", "content": "Which editor does the owner prefer?"}
    # The result file: exactly the D9 keys, mode 0600, no case text or ids.
    data = json.loads(path.read_text())
    assert list(data) == list(RESULT_KEYS)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    text = path.read_text()
    for leaked in ("zebra", "quokka", "Neovim", "Which editor", "personal", "fish"):
        assert leaked not in text
    # A run clears its case rows; ModelUsage stays.
    assert not Memory.objects.exists()


def test_retrieval_only_makes_no_answer_call(eval_mode, providers, llm):
    result, _ = run_cases(
        cases_of(make_case()),
        RunConfig("public", "baseline", retrieval_only=True),
        results_dir=None,
    )
    assert result["answered"] == 0 and result["answer_accuracy"] is None
    assert result["recall_at_10"] == 1.0
    assert not llm.calls and not answer_rows().exists()


def test_workspace_filter_first_and_leak_rate(eval_mode, providers, prompt, llm):
    case = make_case(
        tags=["leak"],
        setup={
            "memories": [
                {"ref": "m1", "type": "semantic", "title": "Pay", "content": "bank transfer"},
                {
                    "ref": "fm1",
                    "type": "semantic",
                    "title": "Acme pay",
                    "content": "Acme pays through PayrollBox.",
                    "workspace_ref": "acme",
                },
            ]
        },
        question="How do I pay contractors?",
        expected={"must_not_contain": ["payrollbox"], "relevant_ids": ["m1"]},
    )
    result, _ = run_cases(cases_of(case), RunConfig("public", "run"), results_dir=None)
    assert result["leak_rate"] == 0.0 and result["recall_at_10"] == 1.0
    assert "PayrollBox" not in llm.payload()
    # The firm row existed during the run, in its own workspace.
    assert Workspace.objects.filter(name="eval:acme", type=Workspace.Type.COMPANY).exists()


def test_a_superseded_fact_case_loads_the_graph(eval_mode, providers):
    case = validate_case(
        make_case(
            tags=["temporal"],
            setup={
                "facts": [
                    _fact("f-old", valid_to="2026-09-15", superseded_by="f-new"),
                    _fact("f-new", object={"name": "fly", "kind": "tool"}, valid_from="2026-09-15"),
                ]
            },
            expected={"relevant_ids": ["f-new"]},
        )
    )
    world = ensure_world()
    loaded = load_case(world, case)
    old = MemoryFact.objects.get(statement="api is deployed to hetzner", valid_to__isnull=False)
    new = MemoryFact.objects.get(valid_to__isnull=True)
    assert old.superseded_by_id == new.id
    assert loaded.ref_by_id[str(new.id)] == "f-new"
    assert MemoryEntity.objects.filter(workspace=world.personal, normalised_name="api").count() == 1
    assert set(MemoryFact.objects.model_eligible().values_list("id", flat=True)) == {new.id}
    clear_case_rows(world)
    assert not MemoryFact.all_objects.exists() and not MemoryEntity.all_objects.exists()


def test_clearing_touches_only_the_eval_owners_rows(eval_mode, providers):
    other = register_user(email="someone@test.dev", password=PASSWORD)
    keep = Memory.objects.create(
        workspace=Workspace.objects.get(owner=other),
        memory_type="semantic",
        status="approved",
        source="user_created",
        title="keep",
        content="keep me",
        fingerprint="x",
    )
    world = ensure_world()
    load_case(world, validate_case(make_case()))
    clear_case_rows(world)
    assert list(Memory.objects.all()) == [keep]


# --- eligibility of eval setup rows ------------------------------------------------


def test_a_private_case_is_stored_private_and_answered_with_personal_data(
    eval_mode, providers, prompt, llm
):
    case = validate_case(make_case(sensitivity="private", tags=["private"]))
    world = ensure_world()
    load_case(world, case)
    assert set(Memory.objects.values_list("sensitivity", flat=True)) == {"private"}
    clear_case_rows(world)

    result, _ = run_cases([case], RunConfig("private", "run"), results_dir=None)
    assert result["answered"] == 1
    row = answer_rows().get()
    assert row.context_report.get("personal_data") is True
    assert row.provider.personal_data_allowed
    # cloud_eligible() parity: a private memory never reaches the payload.
    assert "Neovim" not in llm.payload()


def test_a_private_case_with_a_cloud_only_provider_is_no_provider(eval_mode, prompt, llm):
    chat_provider(personal_data_allowed=False)
    case = validate_case(make_case(sensitivity="private"))
    result, _ = run_cases([case], RunConfig("private", "run"), results_dir=None)
    assert (result["answered"], result["no_provider"]) == (0, 1)
    assert result["answer_accuracy"] is None
    assert not llm.calls and not answer_rows().exists()


def test_a_non_cloud_eligible_setup_memory_never_reaches_a_cloud_payload(eval_mode, prompt, llm):
    cloud = chat_provider(name="fake-cloud", personal_data_allowed=False)
    case = make_case(
        setup={
            "memories": [
                {"ref": "m1", "type": "preference", "title": "Editor", "content": "Neovim"},
                {
                    "ref": "m2",
                    "type": "semantic",
                    "title": "Salary",
                    "content": "salary is 4242 EUR",
                    "sensitivity": "private",
                },
            ]
        },
        expected={"must_not_contain": ["4242"], "relevant_ids": ["m1"]},
    )
    result, _ = run_cases(cases_of(case), RunConfig("public", "run"), results_dir=None)
    assert result["answered"] == 1 and result["leak_rate"] == 0.0
    assert answer_rows().get().provider == cloud
    assert "Neovim" in llm.payload() and "4242" not in llm.payload()


# --- the learning_eval cap -----------------------------------------------------------


@pytest.fixture
def priced(monkeypatch, db):
    """A priced chat provider whose every call costs exactly 2 USD, and the
    seeded 5 USD monthly `learning_eval` cap."""
    chat_provider(priced=True)
    ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )
    monkeypatch.setattr(
        InferenceGateway,
        "_estimate_cost",
        staticmethod(lambda provider, i, o: Decimal("0") if provider is None else Decimal("2")),
    )
    monkeypatch.setattr(InferenceGateway, "_schedule_cost_correction", lambda self, usage_id: None)
    return AIBudget.objects.create(
        name="learning_eval", purposes=[ANSWER_PURPOSE], monthly_limit_usd=Decimal("5")
    )


def _cases(n):
    return cases_of(*(make_case(id=f"c{i}", question=f"Which editor, take {i}?") for i in range(n)))


def test_the_cap_holds_across_runs_and_a_recreated_eval_database(
    eval_mode, priced, prompt, llm, tmp_path
):
    results = tmp_path / "results"
    first, _ = run_cases(_cases(2), RunConfig("private", "run"), results_dir=results)
    assert (first["answered"], first["spend_usd"]) == (2, "4.000000")

    # Run 2: 4 USD spent, one call (to 6), then the cap stops it cleanly.
    second, path = run_cases(_cases(3), RunConfig("private", "run"), results_dir=results)
    assert (second["answered"], second["budget_stopped"]) == (1, 2)
    assert second["spend_usd"] == "2.000000"  # this run's own spend
    assert json.loads(path.read_text())["budget_stopped"] == 2  # the partial result
    assert answer_rows().filter(success=True).count() == 3

    # Run 3 would start over the cap: refused with no call.
    calls = len(llm.calls)
    with pytest.raises(EvalRefused, match="cap is spent"):
        run_cases(_cases(1), RunConfig("private", "run"), results_dir=results)
    assert len(llm.calls) == calls

    # The eval DB is dropped and recreated: no ModelUsage row survives, but
    # the result files' spend_usd still holds the cap.
    ModelUsage.objects.all().delete()
    with pytest.raises(EvalRefused, match="cap is spent"):
        run_cases(_cases(1), RunConfig("private", "run"), results_dir=results)
    assert len(llm.calls) == calls


def test_a_gateway_budget_refusal_stops_the_run_with_a_partial_result(
    eval_mode, priced, prompt, llm, tmp_path, monkeypatch
):
    ModelUsage.objects.create(
        purpose=ANSWER_PURPOSE,
        provider=ModelProvider.objects.get(name="fake-local"),
        success=True,
        estimated_cost=Decimal("4.5"),
    )
    priced.monthly_limit_usd = Decimal("4")
    priced.save()
    monkeypatch.setattr(BudgetGuard, "exhausted", lambda self: False)
    result, path = run_cases(_cases(3), RunConfig("public", "run"), results_dir=tmp_path)
    assert (result["answered"], result["budget_stopped"]) == (0, 3)
    assert not llm.calls
    assert answer_rows().filter(error_type="budget_exceeded").count() == 1
    assert path.exists()


def test_result_file_spend_counts_only_this_month(eval_mode, priced, prompt, llm, tmp_path):
    results = tmp_path / "results"
    stale = {key: None for key in RESULT_KEYS}
    stale.update(
        run_id=str(uuid.uuid4()),
        set="private",
        kind="run",
        retrieval_mode="legacy",
        started_at="2020-01-01T00:00:00+00:00",
        finished_at="2020-01-01T00:01:00+00:00",
        total=1,
        answered=1,
        no_provider=0,
        budget_stopped=0,
        spend_usd="100",
    )
    write_result(results, stale)
    result, _ = run_cases(_cases(1), RunConfig("private", "run"), results_dir=results)
    assert result["answered"] == 1


# --- compare -----------------------------------------------------------------------


def test_compare_writes_two_arms_with_a_verdict_on_b(eval_mode, providers, prompt, llm, tmp_path):
    (arm_a, arm_b), paths = run_compare(
        cases_of(make_case()),
        set_name="public",
        candidate_mode="legacy",
        retrieval_only=False,
        results_dir=tmp_path,
    )
    assert arm_a["run_id"] == arm_b["run_id"] and (arm_a["arm"], arm_b["arm"]) == ("a", "b")
    assert arm_a["verdict"] is None and arm_b["verdict"] == "pass"
    assert [p.name.rsplit("-", 1)[1] for p in paths] == ["a.json", "b.json"]
    arms = {r.context_report["eval_arm"] for r in answer_rows()}
    assert arms == {"a", "b"}


def test_compare_refuses_an_unknown_mode_before_running(eval_mode, providers, prompt, llm):
    with pytest.raises(ValueError, match="unknown retrieval mode"):
        run_compare(
            cases_of(make_case()),
            set_name="public",
            candidate_mode="no-such-mode",
            retrieval_only=False,
            results_dir=None,
        )
    assert not llm.calls and not Memory.objects.exists()


def test_baseline_command_prints_a_metrics_table(eval_mode, providers, prompt, llm, capsys):
    call_command("memory_eval", "baseline", "--set", "public", "--retrieval-only")
    out = capsys.readouterr().out
    assert "memory_eval baseline --set public" in out
    assert "recall_at_10" in out and "leak_rate" in out and "temporal_correctness" in out
    assert not llm.calls
    files = list(Path(eval_mode.MEMORY_EVAL_RESULTS_DIR).glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["total"] == len(load_public_cases()) and data["kind"] == "baseline"


def test_private_baseline_command_reads_the_private_dir(
    eval_mode, providers, prompt, llm, tmp_path
):
    directory = Path(eval_mode.MEMORY_EVAL_PRIVATE_DIR)
    directory.mkdir(mode=0o700)
    (directory / "work.jsonl").write_text(case_line() + "\n")
    call_command("memory_eval", "baseline", "--set", "private")
    files = list((directory / "results").glob("*.json"))
    assert len(files) == 1 and oct(files[0].stat().st_mode & 0o777) == "0o600"
    directory.chmod(0o755)
    with pytest.raises(CommandError, match="readable by others"):
        call_command("memory_eval", "baseline", "--set", "private")


def _settings_default(name):
    """The literal `default=` of `name = env(...)` in the base settings (the
    test environment may override the variable itself)."""
    source = (Path(__file__).resolve().parents[2] / "config" / "settings" / "base.py").read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
            (default,) = [kw.value.value for kw in node.value.keywords if kw.arg == "default"]
            return default
    raise AssertionError(f"{name} not found in base settings")


def test_default_dirs_allow_a_public_run_then_a_private_baseline(
    eval_mode, providers, prompt, llm, tmp_path, monkeypatch
):
    """The owner step on default config: a public run, then a private
    baseline. The public results dir must not land inside the private dir
    (which may hold only case files and `results/`), and no directory the
    runs create may be readable by others."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    eval_mode.MEMORY_EVAL_PRIVATE_DIR = _settings_default("MEMORY_EVAL_PRIVATE_DIR")
    eval_mode.MEMORY_EVAL_RESULTS_DIR = _settings_default("MEMORY_EVAL_RESULTS_DIR")
    private = Path(os.path.expanduser(eval_mode.MEMORY_EVAL_PRIVATE_DIR))
    public = Path(os.path.expanduser(eval_mode.MEMORY_EVAL_RESULTS_DIR))
    assert private.parent == home and not public.is_relative_to(private)

    call_command("memory_eval", "baseline", "--set", "public", "--retrieval-only")
    assert len(list(public.glob("*.json"))) == 1
    assert public.stat().st_mode & 0o777 == 0o700

    private.mkdir(mode=0o700)
    (private / "work.jsonl").write_text(case_line() + "\n")
    call_command("memory_eval", "baseline", "--set", "private", "--retrieval-only")
    assert len(list((private / "results").glob("*.json"))) == 1


def test_ensure_dir_creates_missing_ancestors_0700(tmp_path):
    old_umask = os.umask(0o022)
    try:
        leaf = ensure_dir(tmp_path / "a" / "b" / "c")
    finally:
        os.umask(old_umask)
    for path in (tmp_path / "a", tmp_path / "a" / "b", leaf):
        assert path.stat().st_mode & 0o777 == 0o700, path
    # An existing directory keeps its mode.
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    ensure_dir(shared / "results")
    assert shared.stat().st_mode & 0o777 == 0o755
    assert (shared / "results").stat().st_mode & 0o777 == 0o700


def test_results_written_first_leave_the_private_dir_loadable(tmp_path):
    """A private run whose results dir is created before the private dir
    exists must not leave the private dir readable by others."""
    private = tmp_path / "friday-evals"
    old_umask = os.umask(0o022)
    try:
        write_result(private / "results", {**dict.fromkeys(RESULT_KEYS), "run_id": "r1"})
    finally:
        os.umask(old_umask)
    (private / "work.jsonl").write_text(case_line() + "\n")
    assert [c.id for c in load_private_cases(private)] == ["c1"]


# --- MemoryEvalRun and record ------------------------------------------------------


def test_memory_eval_run_stores_metrics_only():
    assert issubclass(MemoryEvalRun, WorkspaceScopedModel)
    allowed = (
        djm.UUIDField,
        djm.DateTimeField,
        djm.PositiveIntegerField,
        djm.DecimalField,
        djm.FloatField,
        djm.ForeignKey,
    )
    free_text = {"retrieval_mode", "compression_purpose"}  # identifiers, <= 64 chars
    for field in MemoryEvalRun._meta.concrete_fields:
        assert not isinstance(field, djm.TextField | djm.JSONField), field.name
        if isinstance(field, djm.CharField):
            assert field.choices or field.name in free_text, field.name
            assert field.max_length <= 64, field.name
        else:
            assert isinstance(field, allowed), field.name
    names = {f.name for f in MemoryEvalRun._meta.concrete_fields}
    assert not names & {"cases", "case_ids", "question", "answer", "tags", "workspace_ref"}


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


def result_file(tmp_path, **overrides):
    data = {
        "run_id": str(uuid.uuid4()),
        "set": "private",
        "kind": "baseline",
        "retrieval_mode": "legacy",
        "compression_purpose": None,
        "arm": None,
        "started_at": "2026-11-20T10:00:00+00:00",
        "finished_at": "2026-11-20T10:05:00+00:00",
        "total": 100,
        "answered": 90,
        "no_provider": 8,
        "budget_stopped": 2,
        "spend_usd": "1.25",
        "recall_at_10": 0.61,
        "leak_rate": 0.0,
        "temporal_correctness": 0.5,
        "answer_accuracy": 0.7,
        "pre_tokens": None,
        "post_tokens": None,
        "verdict": None,
    }
    data.update(overrides)
    path = tmp_path / f"{data['run_id']}.json"
    path.write_text(json.dumps(data))
    return path, data


def test_record_writes_the_owners_personal_workspace(owner, personal, tmp_path):
    path, data = result_file(tmp_path)
    call_command("memory_eval", "record", str(path), "--owner", owner.email)
    run = MemoryEvalRun.objects.get()
    assert run.workspace == personal and str(run.run_id) == data["run_id"]
    assert (run.total, run.answered, run.recall_at_10) == (100, 90, 0.61)
    assert run.spend_usd == Decimal("1.25") and run.arm == "" and run.verdict == ""
    call_command("memory_eval", "record", str(path), "--owner", owner.email)  # idempotent
    assert MemoryEvalRun.objects.count() == 1
    from audit.models import AuditLog

    audit = AuditLog.objects.get(action="memory.eval.recorded")
    assert set(audit.metadata) == {"set", "kind", "arm"}


def test_record_refuses_another_workspace_and_non_owners(owner, personal, tmp_path):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel="web")
    path, _ = result_file(tmp_path)
    with pytest.raises(CommandError, match="personal workspace"):
        call_command(
            "memory_eval", "record", str(path), "--owner", owner.email, "--workspace", str(firm.id)
        )
    member = register_user(email="member@test.dev", password=PASSWORD)
    with pytest.raises(CommandError, match="installation owner"):
        call_command("memory_eval", "record", str(path), "--owner", member.email)
    assert not MemoryEvalRun.objects.exists()


@pytest.mark.parametrize(
    "overrides, rule",
    [
        ({"cases": [{"id": "c1"}]}, "unknown key"),
        ({"workspace_ref": "personal"}, "unknown key"),
        ({"questions": ["q"]}, "unknown key"),
        ({"set": "secret"}, "set:"),
        ({"total": -1}, "total:"),
        ({"total": True}, "total:"),
        ({"recall_at_10": 1.5}, "recall_at_10:"),
        ({"spend_usd": "-1"}, "spend_usd:"),
        ({"arm": "a"}, "only a compare"),
        ({"finished_at": "2026-11-19T10:00:00+00:00"}, "earlier than"),
        ({"answered": 101}, "exceeds total"),
        ({"retrieval_mode": "legacy; drop"}, "retrieval_mode:"),
    ],
)
def test_record_refuses_a_malformed_or_widened_result_file(owner, tmp_path, overrides, rule):
    path, _ = result_file(tmp_path, **overrides)
    with pytest.raises(CommandError, match=rule):
        call_command("memory_eval", "record", str(path), "--owner", owner.email)
    assert not MemoryEvalRun.objects.exists()


def test_record_refuses_a_missing_key():
    data = dict.fromkeys(RESULT_KEYS)
    del data["verdict"]
    with pytest.raises(ResultError, match="missing key"):
        validate_result(data)


def test_record_refuses_the_eval_settings(owner, tmp_path, settings):
    settings.MEMORY_EVAL_MODE = True
    path, _ = result_file(tmp_path)
    with pytest.raises(CommandError, match="LIVE database"):
        call_command("memory_eval", "record", str(path), "--owner", owner.email)


def test_a_runner_result_file_is_recordable(eval_mode, providers, prompt, llm, owner, tmp_path):
    _, path = run_cases(
        cases_of(make_case()), RunConfig("public", "baseline"), results_dir=tmp_path
    )
    eval_mode.MEMORY_EVAL_MODE = False
    call_command("memory_eval", "record", str(path), "--owner", owner.email)
    run = MemoryEvalRun.objects.get()
    assert (run.set, run.kind, run.retrieval_mode, run.total) == ("public", "baseline", "legacy", 1)


# --- the read endpoint: role x endpoint ---------------------------------------------


def _pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _node_client(owner):
    raw_code, _ = issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    _, secret = enroll_node_device(raw_code=raw_code, node_name="Mac", client_ip="10.0.0.2")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    return client


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal.startswith("owner-pat-"):
        return _pat_client(owner, workspace, principal.removeprefix("owner-pat-"))
    if principal == "node-secret":
        return _node_client(owner)
    if principal == "non-owner":
        member = register_user(email="member@test.dev", password=PASSWORD)
        WorkspaceMember.objects.create(
            workspace=workspace, user=member, role=WorkspaceMember.Role.MEMBER
        )
        return web_client(member)
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


@pytest.fixture
def recorded(owner, personal, tmp_path):
    path, _ = result_file(tmp_path)
    call_command("memory_eval", "record", str(path), "--owner", owner.email)
    return MemoryEvalRun.objects.get()


@pytest.mark.parametrize(
    "principal, expected",
    [
        ("owner-web", 200),
        ("owner-pat-cli", 401),
        ("owner-pat-openai", 401),
        ("owner-pat-hook", 401),
        ("node-secret", 401),
        ("non-owner", 403),
        ("agent", 403),
        ("anonymous", 401),
    ],
)
def test_eval_runs_role_matrix(owner, personal, recorded, web_client, principal, expected):
    response = _client(principal, owner, personal, web_client).get(URL)
    assert response.status_code == expected, response.content
    if expected == 200:
        assert len(response.json()) == 1
    for method in ("post", "patch", "delete"):
        assert getattr(web_client(owner), method)(URL).status_code == 405


def test_eval_runs_carry_only_the_metric_fields(owner, recorded, web_client):
    (row,) = web_client(owner).get(URL).json()
    assert tuple(row) == RUN_FIELDS
    assert row["arm"] is None and row["verdict"] is None and row["compression_purpose"] is None
    assert row["recall_at_10"] == 0.61 and row["spend_usd"] == "1.250000"


def test_a_firm_only_member_sees_no_eval_runs(owner, recorded):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel="web")
    member = register_user(email="firm@test.dev", password=PASSWORD)
    WorkspaceMember.objects.filter(user=member).delete()  # drop their own personal ws
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.ADMIN)
    assert not MemoryEvalRun.objects.for_actor(member).exists()
    assert MemoryEvalRun.objects.for_actor(owner).count() == 1


# --- seeding, purposes, import boundaries ------------------------------------------


def test_seed_creates_the_eval_prompt_and_cap(db):
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name=PROMPT_NAME)
    assert template.purpose == ANSWER_PURPOSE and template.active_version is not None
    budget = AIBudget.objects.get(name="learning_eval")
    assert budget.purposes == [ANSWER_PURPOSE] and budget.monthly_limit_usd == Decimal("5")
    # Separate from the shared learning cap.
    assert ANSWER_PURPOSE not in AIBudget.objects.get(name="learning").purposes


def test_the_eval_purpose_is_tiered(settings):
    assert settings.AI_PURPOSE_TIERS[ANSWER_PURPOSE] == "mid"
    assert ANSWER_PURPOSE not in settings.AI_LEARNING_PURPOSES


def _imports(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_no_production_memory_module_imports_the_eval_set():
    """D16: `memory.evals` is never imported by production code. Inside
    `memory` only the URL table and the `memory_eval` command may (other
    apps are covered by import-linter)."""
    allowed = {Path("memory/urls.py"), Path("memory/management/commands/memory_eval.py")}
    offenders = []
    for path in sorted((BACKEND / "memory").rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[1] in ("evals", "tests", "migrations") or rel in allowed:
            continue
        if any(m == "memory.evals" or m.startswith("memory.evals.") for m in _imports(path)):
            offenders.append(str(rel))
    assert offenders == []
