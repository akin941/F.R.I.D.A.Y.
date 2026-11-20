"""The closed grammar of what Friday may ever send to a deploy target
(ADR-010 decision 7, brief D10) and the field-level checks of a
`DeployTarget`. Pure functions, no Django models: the serializers, the
services, the SSH client and the tests all share one definition, and the
remote forced-command script (`deploy/remote/friday-deploy.sh`) enforces the
same grammar on the other end -- a mismatch between the two ends is a bug
the remote-script tests catch.

Allowlist on both ends: Friday sends only `<verb> <remote_app> [<sha>]`,
verbs `status|deploy|health|rollback`, the SHA 40 lowercase hex characters,
the app name `[a-z0-9][a-z0-9-]{0,62}`.
"""

import base64
import binascii
import re
from urllib.parse import urlsplit

VERB_STATUS = "status"
VERB_DEPLOY = "deploy"
VERB_HEALTH = "health"
VERB_ROLLBACK = "rollback"
VERBS = frozenset({VERB_STATUS, VERB_DEPLOY, VERB_HEALTH, VERB_ROLLBACK})
# Verbs that take a SHA argument; the other two take none.
SHA_VERBS = frozenset({VERB_DEPLOY, VERB_ROLLBACK})

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# A git branch name Friday will ever store or show: conservative subset of
# git-check-ref-format (no `..`, no leading `-` or `/`, no `@{`, no spaces).
BRANCH_RE = re.compile(r"^(?![-/.])(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,200}(?<![/.])$")
# A POSIX-ish account name (the dedicated deploy user on the VPS).
SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# A DNS name or an IPv4 literal (no leading `-`, so it can never be read as
# an ssh option), or a bare IPv6 literal.
_HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?:\.(?!-)[A-Za-z0-9-]{1,63})*\.?$")
_IPV6_RE = re.compile(r"^[0-9A-Fa-f:]{2,39}$")
# Host key types OpenSSH pins in a known_hosts line.
HOST_KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "ssh-rsa",
    }
)
# Free-text labels (target name, environment): printable, no control chars.
_LABEL_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,100}$")

HEALTH_TIMEOUT_MIN_SECONDS = 30
HEALTH_TIMEOUT_MAX_SECONDS = 900


class TargetFieldError(ValueError):
    """A `DeployTarget` field (or a verb argument) is outside the grammar."""


def is_sha(value) -> bool:
    return isinstance(value, str) and bool(SHA_RE.match(value))


def normalize_sha(value) -> str:
    """Lowercases and validates a full commit SHA. Abbreviated SHAs are
    refused: the deploy gate names exactly one commit."""
    if not isinstance(value, str):
        raise TargetFieldError("sha must be a 40-character hex commit id.")
    value = value.strip().lower()
    if not SHA_RE.match(value):
        raise TargetFieldError("sha must be a full 40-character hex commit id.")
    return value


def validate_label(value, field):
    if not isinstance(value, str) or not _LABEL_RE.match(value.strip()):
        raise TargetFieldError(f"{field} must be 1-100 printable characters.")
    return value.strip()


def validate_remote_app(value):
    if not isinstance(value, str) or not APP_RE.match(value):
        raise TargetFieldError("remote_app must match [a-z0-9][a-z0-9-]{0,62}.")
    return value


def validate_branch(value):
    if not isinstance(value, str) or not BRANCH_RE.match(value) or value.endswith(".lock"):
        raise TargetFieldError("tracked_branch is not a valid branch name.")
    return value


def validate_ssh_user(value):
    if not isinstance(value, str) or not SSH_USER_RE.match(value):
        raise TargetFieldError("ssh_user must be a plain account name.")
    return value


def validate_ssh_host(value):
    if not isinstance(value, str):
        raise TargetFieldError("ssh_host must be a host name or IP address.")
    value = value.strip()
    if len(value) > 253 or not (_HOSTNAME_RE.match(value) or _IPV6_RE.match(value)):
        raise TargetFieldError("ssh_host must be a host name or IP address.")
    return value


def validate_ssh_port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise TargetFieldError("ssh_port must be 1-65535.")
    return value


def normalize_host_public_key(value):
    """`<type> <base64>` exactly (a trailing comment is dropped): the one
    key the per-run known_hosts pins. No trust on first use -- the owner
    captures it out of band (runbook) and pastes it here."""
    if not isinstance(value, str):
        raise TargetFieldError("host_public_key must be an OpenSSH public key line.")
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] not in HOST_KEY_TYPES:
        raise TargetFieldError(
            "host_public_key must be '<type> <base64>' with type one of "
            + ", ".join(sorted(HOST_KEY_TYPES))
            + "."
        )
    key_type, blob = parts[0], parts[1]
    try:
        decoded = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TargetFieldError("host_public_key is not valid base64.") from exc
    # The blob embeds its own key type as a length-prefixed string; a key
    # pasted with the wrong type label would never verify.
    if len(decoded) < 4:
        raise TargetFieldError("host_public_key is truncated.")
    length = int.from_bytes(decoded[:4], "big")
    if decoded[4 : 4 + length].decode("ascii", "replace") != key_type:
        raise TargetFieldError("host_public_key type does not match its key data.")
    return f"{key_type} {blob}"


def validate_repo_git_url(value):
    """A credential-free remote (the rule `harness.serializers` applies to
    `HarnessRepo.git_url`, restated here because `deploys` may not import
    `harness`): no query/fragment, no userinfo except the conventional
    `git@` SSH user."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise TargetFieldError("repo_git_url is required.")
    value = value.strip()
    if "?" in value or "#" in value or any(ch.isspace() for ch in value):
        raise TargetFieldError("repo_git_url must be a plain repo URL.")
    scp = re.match(r"^([^@/:]+)@[^/:]+:.+$", value)
    if scp:
        if scp.group(1) != "git":
            raise TargetFieldError("repo_git_url must not embed credentials.")
        return value
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise TargetFieldError("repo_git_url is not a valid URL.") from exc
    if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
        raise TargetFieldError("repo_git_url must be an https://, ssh:// or git@host: remote.")
    if parsed.password or (parsed.username and parsed.username != "git"):
        raise TargetFieldError("repo_git_url must not embed credentials.")
    return value


def validate_health_url(value):
    """Optional. http(s) only, no userinfo (a credential), no fragment."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 2000:
        raise TargetFieldError("health_url must be an http(s) URL.")
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise TargetFieldError("health_url is not a valid URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetFieldError("health_url must be an http(s) URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise TargetFieldError("health_url must not carry credentials or a fragment.")
    return value


def validate_health_timeout(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not HEALTH_TIMEOUT_MIN_SECONDS <= value <= HEALTH_TIMEOUT_MAX_SECONDS
    ):
        raise TargetFieldError(
            f"health_timeout_seconds must be {HEALTH_TIMEOUT_MIN_SECONDS}-"
            f"{HEALTH_TIMEOUT_MAX_SECONDS}."
        )
    return value


def build_remote_command(verb, remote_app, sha=None) -> str:
    """The exact string sent as the SSH command -- and the only one.
    Raises `TargetFieldError` for anything outside the grammar."""
    if verb not in VERBS:
        raise TargetFieldError(f"unknown deploy verb {verb!r}.")
    validate_remote_app(remote_app)
    if verb in SHA_VERBS:
        if not is_sha(sha):
            raise TargetFieldError(f"{verb} needs a full 40-character lowercase sha.")
        return f"{verb} {remote_app} {sha}"
    if sha is not None:
        raise TargetFieldError(f"{verb} takes no sha.")
    return f"{verb} {remote_app}"
