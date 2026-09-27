"""Self-modification hard-block helpers (Phase 3 branch 2, brief §boundary;
ADR-004: "agents can never modify their own definitions or permissions").

A `HarnessRepo` whose git remote resolves to this Jarvis installation's own
repo can still be registered and used for Phase 2 read-only discovery
(unchanged, no risk there), but `create_harness_implementation_job`/
`create_harness_review` refuse outright -- `HarnessInvalidTransition`,
before Gate A is ever requested, not a review-time or Gate-B-time check
that could be argued around case by case.

The pure canonicalization functions moved to `core.repo_identity` in
Phase 5 branch 1 (ADR-010, brief D7) so `connectors` and `deploys` can
share them without importing `harness`. This module re-exports them
unchanged, so existing `harness.repo_identity` imports keep working.
"""

from core.repo_identity import canonicalize_git_url, is_self_repo

__all__ = ["canonicalize_git_url", "is_self_repo"]
