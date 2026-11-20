import pytest


@pytest.fixture(autouse=True)
def _self_repo_identifiers_configured(settings):
    """Implementation-job creation fails closed when
    HARNESS_SELF_REPO_IDENTIFIERS is empty (Phase 3 branch 5). Harness tests
    run with an identifier that matches none of their fixture repos; the
    tests that exercise the guard itself override it explicitly."""
    settings.HARNESS_SELF_REPO_IDENTIFIERS = ["git@example.invalid:friday/self.git"]
