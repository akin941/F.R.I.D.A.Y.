"""Canonical git-remote identity -- pure functions, no Django, no models.

Moved here from `harness/repo_identity.py` in Phase 5 branch 1 (ADR-010,
brief D7): the harness self-modification hard-block is no longer the only
consumer. The `connectors` GitHub scope check (every `owner/repo` argument
must canonicalize to a `HarnessRepo` of the calling workspace) and the
`deploys` self-repo guard compare remotes the same way, and neither of
those apps may import `harness` (it stays a leaf, ADR-010). `core` is the
one place every kernel app may import.

Why a dedicated canonical normalizer rather than string equality:
`git@github.com:owner/repo.git` and `https://github.com/owner/repo` name
the same remote, and two different-looking spellings must never compare
unequal where a guard depends on it (Phase 3 branch 2, round-2 MEDIUM
finding, O-5). The first consumer remains the harness hard-block (ADR-004:
"agents can never modify their own definitions or permissions"): a
`HarnessRepo` whose remote resolves to this Friday installation's own repo
is refused for implementation and review jobs -- see
`harness/repo_identity.py`, which re-exports these functions.
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


# The canonical remote of this installation's own source, plus its
# pre-rename name (the Jarvis -> Friday rename moved the repo from
# `akin941/jarvis-os` to `akin941/F.R.I.D.A.Y.` -- the trailing dot is part
# of the real GitHub name). Always treated as self-repo identifiers IN
# ADDITION to the configured `HARNESS_SELF_REPO_IDENTIFIERS`, so an owner
# `.env` that still lists only the old remote cannot weaken the ADR-004
# self-modification guard. This only ever widens what is refused: the
# fail-closed "empty configured list refuses every write job" rule stays
# with the callers, which check the configured setting itself, never this.
CANONICAL_SELF_REPO = "https://github.com/akin941/F.R.I.D.A.Y."
LEGACY_SELF_REPO = "https://github.com/akin941/jarvis-os"
BUILTIN_SELF_REPO_IDENTIFIERS = (CANONICAL_SELF_REPO, LEGACY_SELF_REPO)


def is_self_repo(git_url: str, *, self_repo_identifiers) -> bool:
    """`True` if `git_url` canonically matches any configured
    `HARNESS_SELF_REPO_IDENTIFIERS` entry or one of the built-in
    `BUILTIN_SELF_REPO_IDENTIFIERS`. Both sides are normalized the same
    way, so a `HarnessRepo.git_url` registered as
    `https://github.com/akin941/F.R.I.D.A.Y.` correctly matches a configured
    identifier of `git@github.com:akin941/F.R.I.D.A.Y..git`, and vice versa."""
    canonical = canonicalize_git_url(git_url)
    return any(
        canonical == canonicalize_git_url(identifier)
        for identifier in (*self_repo_identifiers, *BUILTIN_SELF_REPO_IDENTIFIERS)
    )
