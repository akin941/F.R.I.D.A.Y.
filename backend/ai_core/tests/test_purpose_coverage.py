"""Purpose coverage (Phase 5 D11): every `purpose=` a production module
passes to the gateway -- `complete()`, `embed()`, `transcribe()`,
`synthesize()` or `record_external_usage()` -- must be tier-mapped in
`settings.AI_PURPOSE_TIERS` or listed in `settings.AI_UNTIERED_PURPOSES`,
so a new purpose can never silently fall outside the tier map.

Static (AST), not runtime: every call site is found whether or not a test
happens to exercise it. A purpose must be a string literal or a
module-level string constant, so this scan can see it."""

import ast
from pathlib import Path

from django.conf import settings

from ai_core.models import ModelTier

BACKEND = Path(__file__).resolve().parents[2]
GATEWAY_CALLS = {"complete", "embed", "transcribe", "synthesize", "record_external_usage"}
_SKIP_DIRS = {".venv", "tests", "migrations", "node_modules", "__pycache__"}
# The gateway and adapters forward their caller's `purpose` parameter. The
# eval runner (Phase 8B D11) answers under `memory.eval_answer` or, in a
# compression compare, under a candidate that `check_compression_purpose`
# has verified is classified eligible (so already tiered here).
_PASS_THROUGH_MODULES = {"ai_core/gateway.py", "memory/evals/runner.py"}


def _production_modules():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if _SKIP_DIRS.intersection(rel.parts) or rel.name.startswith("test_"):
            continue
        if rel.name == "conftest.py" or rel.parts[:2] == ("ai_core", "adapters"):
            continue
        yield rel, path


def _module_constants(tree):
    constants = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _call_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _purpose_expr(node):
    """The expression passed as `purpose` in a gateway call: the `purpose=`
    keyword, else -- for the gateway methods, whose first parameter is
    `purpose` -- the first positional argument. `_UNSEEN` when the call
    carries neither but splats `**kwargs` (the purpose could hide in it),
    `None` when it plainly passes none."""
    for keyword in node.keywords:
        if keyword.arg == "purpose":
            return keyword.value
    if _call_name(node.func) != "record_external_usage" and node.args:
        return node.args[0]
    if any(keyword.arg is None for keyword in node.keywords):
        return _UNSEEN
    return None


_UNSEEN = object()


def collect_purposes():
    """`({purpose: [site, ...]}, [unresolvable site, ...])`."""
    found, unresolved = {}, []
    for rel, path in _production_modules():
        tree = ast.parse(path.read_text(), filename=str(rel))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node.func) not in GATEWAY_CALLS:
                continue
            value = _purpose_expr(node)
            if value is None:
                continue
            site = f"{rel}:{node.lineno}"
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.setdefault(value.value, []).append(site)
            elif isinstance(value, ast.Name) and value.id in constants:
                found.setdefault(constants[value.id], []).append(site)
            elif str(rel) not in _PASS_THROUGH_MODULES:
                unresolved.append(site)
    return found, unresolved


def test_scan_finds_the_known_call_sites():
    # Guards the scanner itself: an AST walk that silently matched nothing
    # would make the coverage test below pass vacuously.
    found, _ = collect_purposes()
    for purpose in (
        "chat",
        "router.intent",
        "memory.extraction",
        "agent.plan",
        "facade.ephemeral",  # passed as a module constant
        "harness.review_summary",
        "harness.review",  # record_external_usage
        "knowledge.embedding",  # embed
    ):
        assert purpose in found, purpose


def test_every_production_purpose_is_tiered_or_explicitly_untiered():
    found, _ = collect_purposes()
    tiered = set(settings.AI_PURPOSE_TIERS)
    untiered = set(settings.AI_UNTIERED_PURPOSES)
    missing = {
        purpose: sites
        for purpose, sites in found.items()
        if purpose not in tiered and purpose not in untiered
    }
    assert not missing, (
        "Unmapped gateway purposes -- add each to AI_PURPOSE_TIERS or, with a "
        f"reason, AI_UNTIERED_PURPOSES (config/settings/base.py): {missing}"
    )


def test_every_purpose_is_statically_visible():
    _, unresolved = collect_purposes()
    assert not unresolved, (
        "purpose= must be a string literal or module-level constant so the "
        f"coverage scan can see it: {unresolved}"
    )


def test_tier_map_names_only_real_tiers_and_never_overlaps_untiered():
    assert set(settings.AI_PURPOSE_TIERS.values()) <= set(ModelTier.Tier.values)
    assert not set(settings.AI_PURPOSE_TIERS) & set(settings.AI_UNTIERED_PURPOSES)


def test_review_purpose_split():
    # D11: the gateway summary routes as its own cheap purpose; the
    # external-CLI review rows keep `harness.review` and are untiered.
    assert settings.AI_PURPOSE_TIERS["harness.review_summary"] == "cheap"
    assert "harness.review" not in settings.AI_PURPOSE_TIERS
    assert "harness.review" in settings.AI_UNTIERED_PURPOSES


def _call(source):
    return ast.parse(source).body[0].value


def test_purpose_is_seen_positionally_and_through_splats():
    # A positional purpose is still found; a `**kwargs` call without an
    # explicit purpose is reported as unseen, never silently skipped.
    assert _purpose_expr(_call("gateway.complete('chat', messages)")).value == "chat"
    assert _purpose_expr(_call("gateway.embed(purpose='x', texts=t)")).value == "x"
    assert _purpose_expr(_call("gateway.complete(**kwargs)")) is _UNSEEN
    assert _purpose_expr(_call("record_external_usage(**kwargs)")) is _UNSEEN
    assert _purpose_expr(_call("task.complete()")) is None
