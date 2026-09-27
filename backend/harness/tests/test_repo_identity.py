"""The canonicalization table itself lives in `core/tests/test_repo_identity.py`
since Phase 5 branch 1 moved the pure functions to `core.repo_identity`
(ADR-010). What stays here: the harness import path is the very same
function, so the self-modification hard-block cannot drift from the
`connectors`/`deploys` comparisons."""

import core.repo_identity
import harness.repo_identity
from harness import serializers, services


def test_harness_reexports_the_core_functions_unchanged():
    assert harness.repo_identity.canonicalize_git_url is core.repo_identity.canonicalize_git_url
    assert harness.repo_identity.is_self_repo is core.repo_identity.is_self_repo


def test_harness_guards_use_the_core_function():
    assert services.is_self_repo is core.repo_identity.is_self_repo
    assert serializers.is_self_repo is core.repo_identity.is_self_repo


def test_harness_hard_block_still_matches_equivalent_spellings():
    assert harness.repo_identity.is_self_repo(
        "https://github.com/akin941/jarvis-os",
        self_repo_identifiers=["git@github.com:akin941/jarvis-os.git"],
    )
