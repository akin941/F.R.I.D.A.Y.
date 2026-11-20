"""harness.W001: a pre-rename `HARNESS_SELF_REPO_IDENTIFIERS` (only the
old `akin941/jarvis-os` remote) is flagged at boot."""

import pytest

from harness.checks import check_self_repo_identifiers_renamed


def _ids(settings, identifiers):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = identifiers
    return [message.id for message in check_self_repo_identifiers_renamed()]


def test_legacy_only_identifiers_warn(settings):
    assert _ids(settings, ["git@github.com:akin941/jarvis-os.git"]) == ["harness.W001"]


def test_unrelated_identifiers_warn(settings):
    assert _ids(settings, ["git@example.invalid:friday/self.git"]) == ["harness.W001"]


def test_empty_list_is_not_this_check(settings):
    assert _ids(settings, []) == []


@pytest.mark.parametrize(
    "identifier",
    [
        "https://github.com/akin941/F.R.I.D.A.Y..git",
        "git@github.com:akin941/F.R.I.D.A.Y..git",
        "https://github.com/akin941/F.R.I.D.A.Y.",
    ],
)
def test_renamed_remote_in_any_spelling_is_silent(settings, identifier):
    assert _ids(settings, ["git@github.com:akin941/jarvis-os.git", identifier]) == []


def test_check_is_registered_and_is_a_warning(settings):
    from django.core import checks

    settings.HARNESS_SELF_REPO_IDENTIFIERS = ["git@github.com:akin941/jarvis-os.git"]
    found = [m for m in checks.run_checks() if m.id == "harness.W001"]
    assert len(found) == 1
    assert found[0].level == checks.WARNING
