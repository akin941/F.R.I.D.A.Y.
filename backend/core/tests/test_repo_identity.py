import pytest

from core.repo_identity import CANONICAL_SELF_REPO, canonicalize_git_url, is_self_repo

# Known-equivalent pairs (brief §boundary, round-2 MEDIUM finding, O-5): a
# direct test table drives this so the hard-block fires on any of them.
_EQUIVALENT_PAIRS = [
    ("git@github.com:akin941/F.R.I.D.A.Y..git", "https://github.com/akin941/F.R.I.D.A.Y."),
    ("https://github.com/akin941/F.R.I.D.A.Y..git", "https://github.com/akin941/F.R.I.D.A.Y./"),
    ("ssh://git@github.com/akin941/F.R.I.D.A.Y..git", "git@github.com:akin941/F.R.I.D.A.Y."),
    ("GIT@GITHUB.COM:akin941/F.R.I.D.A.Y..git", "https://github.com/akin941/F.R.I.D.A.Y."),
    # Codex code-review P1, branch 2 round 1: a trailing slash AFTER
    # `.git` used to survive normalization untouched.
    ("https://github.com/akin941/F.R.I.D.A.Y..git/", "https://github.com/akin941/F.R.I.D.A.Y."),
    ("git@github.com:akin941/F.R.I.D.A.Y..git/", "https://github.com/akin941/F.R.I.D.A.Y."),
]


@pytest.mark.parametrize("a,b", _EQUIVALENT_PAIRS)
def test_known_equivalent_urls_normalize_identically(a, b):
    assert canonicalize_git_url(a) == canonicalize_git_url(b)


def test_different_repos_do_not_normalize_identically():
    assert canonicalize_git_url("git@github.com:akin941/F.R.I.D.A.Y..git") != canonicalize_git_url(
        "git@github.com:akin941/some-other-repo.git"
    )


class TestIsSelfRepo:
    def test_matches_a_configured_identifier_in_a_different_url_form(self):
        assert is_self_repo(
            "https://github.com/akin941/F.R.I.D.A.Y.",
            self_repo_identifiers=["git@github.com:akin941/F.R.I.D.A.Y..git"],
        )

    def test_does_not_match_an_unrelated_repo(self):
        assert not is_self_repo(
            "git@github.com:someone-else/other-repo.git",
            self_repo_identifiers=["git@github.com:akin941/F.R.I.D.A.Y..git"],
        )

    def test_empty_identifier_list_matches_no_unrelated_repo(self):
        assert not is_self_repo("git@github.com:acme/other.git", self_repo_identifiers=[])

    @pytest.mark.parametrize(
        "spelling",
        [
            "https://github.com/akin941/F.R.I.D.A.Y..git",
            "git@github.com:akin941/F.R.I.D.A.Y..git",
            "https://github.com/akin941/F.R.I.D.A.Y.",
            "git@github.com:akin941/jarvis-os.git",
            "https://github.com/akin941/jarvis-os",
        ],
    )
    def test_builtin_self_repos_match_even_with_only_a_legacy_identifier(self, spelling):
        """Rename review MEDIUM: an owner `.env` still listing only the
        pre-rename remote must not let the renamed repo through."""
        assert is_self_repo(
            spelling, self_repo_identifiers=["git@github.com:akin941/jarvis-os.git"]
        )


@pytest.mark.parametrize(
    "spelling",
    [
        "https://github.com/akin941/F.R.I.D.A.Y..git",
        "git@github.com:akin941/F.R.I.D.A.Y..git",
        "https://github.com/akin941/F.R.I.D.A.Y.",
    ],
)
def test_trailing_dot_repo_name_canonicalizes_to_the_canonical_self_repo(spelling):
    assert canonicalize_git_url(spelling) == "github.com/akin941/f.r.i.d.a.y."
    assert canonicalize_git_url(spelling) == canonicalize_git_url(CANONICAL_SELF_REPO)


@pytest.mark.parametrize(
    "spelling",
    [
        "git@github.com:/akin941/F.R.I.D.A.Y..git",
        "ssh://git@ssh.github.com:443/akin941/F.R.I.D.A.Y..git",
        "git@github.com.:akin941/F.R.I.D.A.Y..git",
        "ssh://git@github.com/akin941//F.R.I.D.A.Y..git",
        "https://github.com//akin941/F.R.I.D.A.Y..git/",
    ],
)
def test_alternate_spellings_of_the_same_remote_still_match(spelling):
    """Phase 3 branch 5 (2026-09-27 audit): leading/repeated path slashes, a
    trailing-dot host and GitHub's SSH-over-443 endpoint all name the same
    repo and must not bypass the self-modification hard block."""
    assert is_self_repo(spelling, self_repo_identifiers=["git@github.com:akin941/F.R.I.D.A.Y..git"])
