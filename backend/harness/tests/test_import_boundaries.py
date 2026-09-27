"""`ModelUsage` stays `ai_core`-owned even for out-of-process calls (brief
§"Boundary vs. existing systems"): `harness` calls
`ai_core.gateway.record_external_usage()`, it never imports or constructs
`ModelUsage` itself. import-linter operates at the `ai_core` package
level (harness legitimately imports `ai_core.gateway`, and -- branch 3 --
`ai_core.models.PromptTemplate`, which is fine: the constraint is
specifically about `ModelUsage`, not every name in `ai_core.models`), so
it can't by itself catch a stray `from ai_core.models import ModelUsage`
-- this is a source-level check for exactly that, per the brief's own
test-strategy line ("harness's own tests separately assert it never
imports ai_core.models.ModelUsage directly")."""

import re
from pathlib import Path

_HARNESS_SOURCE_FILES = [
    path
    for path in Path(__file__).resolve().parent.parent.glob("*.py")
    if path.name != "__init__.py"
]

# Matches actual code usage -- any import statement naming ModelUsage
# (bare `import ModelUsage`, `from ... import ModelUsage`, mixed with
# other names, or aliased with `as`), a constructor call, or `.objects`
# manager access -- not an explanatory comment/docstring mentioning the
# word (several already do, harmlessly, e.g. "no manual ModelUsage row
# needed here"). Codex code-review P3, branch 3 round 4: the original
# `import\s+ModelUsage` only matched a bare `import ModelUsage`, so
# `from ai_core.models import PromptTemplate, ModelUsage` or
# `ModelUsage as Usage` -- exactly the mixed-import form this branch's
# own new `PromptTemplate` import made newly plausible -- would have
# passed undetected.
_MODEL_USAGE_CODE_USAGE = re.compile(
    r"^\s*(?:from\s+\S+\s+import\s+.*\bModelUsage\b|import\s+.*\bModelUsage\b)"
    r"|ModelUsage\s*\("
    r"|ModelUsage\.objects",
    re.MULTILINE,
)


def test_harness_never_references_ai_core_models_directly():
    offenders = []
    for path in _HARNESS_SOURCE_FILES:
        text = path.read_text()
        if _MODEL_USAGE_CODE_USAGE.search(text):
            offenders.append(path.name)
    assert offenders == [], (
        f"{offenders} reference ModelUsage directly -- it must only ever be "
        "constructed via ai_core.gateway.record_external_usage()/.complete()/.embed()."
    )


def test_regex_catches_mixed_and_aliased_import_forms():
    """Codex code-review P3, branch 3 round 4: guards the fix itself --
    a bare `import\\s+ModelUsage` check would miss these forms, which is
    exactly the gap this branch's own new `PromptTemplate` import made
    newly plausible (a reviewer/author could add ModelUsage to the same
    `from ai_core.models import ...` line without noticing)."""
    assert _MODEL_USAGE_CODE_USAGE.search("from ai_core.models import PromptTemplate, ModelUsage")
    assert _MODEL_USAGE_CODE_USAGE.search("from ai_core.models import ModelUsage as Usage")
    assert _MODEL_USAGE_CODE_USAGE.search("import ai_core.models.ModelUsage")
    assert not _MODEL_USAGE_CODE_USAGE.search(
        "# no manual ModelUsage row needed here, the gateway records it"
    )
