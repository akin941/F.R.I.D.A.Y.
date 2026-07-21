import pytest

from harness.repo_identity import canonicalize_git_url, is_self_repo

# Known-equivalent pairs (brief §boundary, round-2 MEDIUM finding, O-5): a
# direct test table drives this so the hard-block fires on any of them.
_EQUIVALENT_PAIRS = [
    ("git@github.com:akin941/jarvis-os.git", "https://github.com/akin941/jarvis-os"),
    ("https://github.com/akin941/jarvis-os.git", "https://github.com/akin941/jarvis-os/"),
    ("ssh://git@github.com/akin941/jarvis-os.git", "git@github.com:akin941/jarvis-os"),
    ("GIT@GITHUB.COM:akin941/Jarvis-OS.git", "https://github.com/akin941/jarvis-os"),
    # Codex code-review P1, branch 2 round 1: a trailing slash AFTER
    # `.git` used to survive normalization untouched.
    ("https://github.com/akin941/jarvis-os.git/", "https://github.com/akin941/jarvis-os"),
    ("git@github.com:akin941/jarvis-os.git/", "https://github.com/akin941/jarvis-os"),
]


@pytest.mark.parametrize("a,b", _EQUIVALENT_PAIRS)
def test_known_equivalent_urls_normalize_identically(a, b):
    assert canonicalize_git_url(a) == canonicalize_git_url(b)


def test_different_repos_do_not_normalize_identically():
    assert canonicalize_git_url("git@github.com:akin941/jarvis-os.git") != canonicalize_git_url(
        "git@github.com:akin941/some-other-repo.git"
    )


class TestIsSelfRepo:
    def test_matches_a_configured_identifier_in_a_different_url_form(self):
        assert is_self_repo(
            "https://github.com/akin941/jarvis-os",
            self_repo_identifiers=["git@github.com:akin941/jarvis-os.git"],
        )

    def test_does_not_match_an_unrelated_repo(self):
        assert not is_self_repo(
            "git@github.com:someone-else/other-repo.git",
            self_repo_identifiers=["git@github.com:akin941/jarvis-os.git"],
        )

    def test_empty_identifier_list_never_matches(self):
        assert not is_self_repo("git@github.com:akin941/jarvis-os.git", self_repo_identifiers=[])
