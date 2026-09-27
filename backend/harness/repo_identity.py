"""Self-modification hard-block (Phase 3 branch 2, brief §boundary; ADR-004:
"agents can never modify their own definitions or permissions").

A `HarnessRepo` whose git remote resolves to this Jarvis installation's own
repo can still be registered and used for Phase 2 read-only discovery
(unchanged, no risk there), but `create_harness_implementation_job`/
`create_harness_review` refuse outright -- `HarnessInvalidTransition`,
before Gate A is ever requested, not a review-time or Gate-B-time check
that could be argued around case by case.

This is a dedicated canonical normalizer, not `harness.serializers`'s
existing `_reject_embedded_credentials` -- that function only rejects
embedded credentials, it does not reduce two different-looking URLs that
name the same remote to a comparable form. Without it,
`git@github.com:owner/repo.git` and `https://github.com/owner/repo` would
compare unequal despite being the same repo (round-2 MEDIUM finding, O-5).
"""

import re
from urllib.parse import urlsplit

_SCP_LIKE_RE = re.compile(r"^[^@/:]+@([^/:]+):(.+)$")


def canonicalize_git_url(git_url: str) -> str:
    """Reduces a git remote URL to a comparable `host/owner/repo` form:
    lowercased, `.git` suffix and trailing slashes stripped, userinfo and
    scheme differences ignored, scp-like (`git@host:owner/repo`) and
    `https://host/owner/repo` forms both resolved to the same string.
    Local/`file://` paths (not explicitly forbidden by
    `harness.serializers`) normalize to their own lowercased, stripped
    form -- there is no host/owner/repo structure to extract from those.

    Not itself a validity check -- an unparseable or malformed URL still
    produces *some* normalized string here (worst case, its own lowercased
    input), which simply will not happen to match any configured self-repo
    identifier. Rejecting malformed `git_url`s is `harness.serializers`'s
    job at registration time, not this comparison's.
    """
    # Codex code-review P1, branch 2 round 1: an earlier version stripped
    # the `.git` suffix BEFORE the trailing slash, so a URL registered
    # with a trailing slash after `.git` (e.g. `https://host/org/repo.git/`
    # -- a real, valid form some hosts/tools produce) never matched
    # `.endswith(".git")` at all, leaving the un-stripped `repo.git` in
    # the canonical form and silently bypassing the self-repo match for
    # that spelling. Trailing slashes are stripped first, then `.git`,
    # then trailing slashes again (defensive, in case `.git` removal ever
    # exposes another one).
    value = git_url.strip().lower().rstrip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")]
    value = value.rstrip("/")

    scp_match = _SCP_LIKE_RE.match(value)
    if scp_match:
        return _join(scp_match.group(1), scp_match.group(2))

    if "://" in value:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        return _join(host, parsed.path) if host else _collapse_path(parsed.path)

    return value


# Phase 3 branch 5 (2026-09-27 audit): alternate spellings of the same
# remote that GitHub accepts must not bypass the hard block --
# `git@github.com:/owner/repo`, repeated slashes, a trailing-dot FQDN
# (`github.com.`) and GitHub's documented SSH-over-443 endpoint
# (`ssh.github.com`) all name the same repo.
_HOST_ALIASES = {"ssh.github.com": "github.com", "altssh.gitlab.com": "gitlab.com"}


def _collapse_path(path):
    parts = [part for part in path.split("/") if part]
    joined = "/".join(parts)
    return joined[: -len(".git")] if joined.endswith(".git") else joined


def _join(host, path):
    host = host.rstrip(".")
    host = _HOST_ALIASES.get(host, host)
    return f"{host}/{_collapse_path(path)}"


def is_self_repo(git_url: str, *, self_repo_identifiers) -> bool:
    """`True` if `git_url` canonically matches any configured
    `HARNESS_SELF_REPO_IDENTIFIERS` entry. Both sides are normalized the
    same way, so a `HarnessRepo.git_url` registered as
    `https://github.com/akin941/jarvis-os` correctly matches a configured
    identifier of `git@github.com:akin941/jarvis-os.git`, and vice versa."""
    canonical = canonicalize_git_url(git_url)
    return any(
        canonical == canonicalize_git_url(identifier) for identifier in self_repo_identifiers
    )
