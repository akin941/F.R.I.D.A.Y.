"""The deploy runner's SSH client (brief D10: system OpenSSH, argv only --
no paramiko/asyncssh).

Discipline, mirroring the harness push path (`harness/runner.py`
`_resolve_git_push_env` / `_ISOLATED_GIT_ENV`, restated here because
`deploys` may not import `harness`):

- **argv only**, never a shell string; the remote command is built by
  `deploys.validation.build_remote_command` from the four-verb grammar and
  sent as one argument after `--`.
- **No ambient configuration or credentials:** `-F /dev/null` (no
  ~/.ssh/config, no ProxyCommand/Include from the host), `-i
  $DEPLOY_SSH_KEY_PATH` with `IdentitiesOnly=yes`, `BatchMode=yes` (no
  password/passphrase prompt), agent and X11 forwarding off, and a
  sanitized environment without `SSH_AUTH_SOCK` -- the host's ssh-agent is
  never consulted. A missing key fails closed (`SSHConfigError`), it never
  falls back to a default key.
- **Pinned host key:** a per-invocation known_hosts file holding exactly the
  target's `host_public_key` under a fixed `HostKeyAlias`, with
  `StrictHostKeyChecking=yes`, the global known_hosts disabled and
  `UpdateHostKeys=no`. No trust on first use.
- **Bounded, redacted output:** remote output is low-trust text; it is
  stripped of control characters, credential-shaped strings are redacted,
  and it is cut to `OUTPUT_MAX_CHARS`.

Tests replace `_run_subprocess` (the one call that spawns a process), so no
test ever opens a real SSH connection; `make deploy-smoke` does, against a
container.
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from django.conf import settings

from deploys.validation import build_remote_command

# Brief D10: remote output is bounded to 16 KB.
OUTPUT_MAX_CHARS = 16 * 1024
# The known_hosts entry name; `HostKeyAlias` makes ssh look the key up under
# it whatever host/port is dialled, so the file never needs `[host]:port`.
HOST_KEY_ALIAS = "friday-deploy-target"
# ssh's own exit status for a connection-level failure (auth, host key,
# network). The remote script never exits 255.
SSH_TRANSPORT_EXIT = 255
_SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class SSHConfigError(Exception):
    """The runner itself is not configured to deploy (no key). Fail closed;
    nothing was contacted."""

    code = "ssh_key_missing"


@dataclass(frozen=True)
class SSHResult:
    exit_code: int | None
    output: str
    timed_out: bool = False

    @property
    def transport_failed(self):
        """True when ssh could not (or not provably) run the remote command
        to completion: a timeout, or ssh's own 255."""
        return self.timed_out or self.exit_code == SSH_TRANSPORT_EXIT

    @property
    def ok(self):
        return not self.timed_out and self.exit_code == 0


def resolve_key_path():
    key_path = settings.DEPLOY_SSH_KEY_PATH
    if not key_path or not os.path.isfile(key_path):
        raise SSHConfigError(
            "DEPLOY_SSH_KEY_PATH is not configured or does not exist; deploys are refused "
            "(no ambient SSH credentials are ever used)."
        )
    return key_path


def build_argv(*, host, port, user, key_path, known_hosts_path, remote_command):
    return [
        "ssh",
        "-F",
        "/dev/null",
        "-T",
        "-x",
        "-a",
        "-i",
        key_path,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts_path}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        f"HostKeyAlias={HOST_KEY_ALIAS}",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        f"ConnectTimeout={settings.DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "LogLevel=ERROR",
        "-p",
        str(port),
        "-l",
        user,
        "--",
        host,
        remote_command,
    ]


def build_env(home):
    """A fresh environment, never the worker's: no SSH_AUTH_SOCK, no
    SSH_ASKPASS, no proxy variables. HOME points at the per-run temp dir so
    nothing under the runner user's real ~/.ssh is read."""
    return {"PATH": _SAFE_PATH, "HOME": home, "LANG": "C", "LC_ALL": "C"}


def _run_subprocess(argv, *, env, timeout):
    """The single process spawn -- tests monkeypatch this."""
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def run_verb(target, verb, sha=None, *, timeout):
    """Runs one verb against `target` (a mapping or object with ssh_host,
    ssh_port, ssh_user, host_public_key, remote_app). Returns an
    `SSHResult`; raises `SSHConfigError` (no key) or
    `validation.TargetFieldError` (a verb/app/sha outside the grammar)
    before anything is spawned."""
    fields = target if isinstance(target, dict) else vars(target)
    remote_command = build_remote_command(verb, fields["remote_app"], sha)
    key_path = resolve_key_path()
    with tempfile.TemporaryDirectory(prefix="friday-deploy-ssh-") as home:
        known_hosts = os.path.join(home, "known_hosts")
        fd = os.open(known_hosts, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(f"{HOST_KEY_ALIAS} {fields['host_public_key']}\n")
        argv = build_argv(
            host=fields["ssh_host"],
            port=fields["ssh_port"],
            user=fields["ssh_user"],
            key_path=key_path,
            known_hosts_path=known_hosts,
            remote_command=remote_command,
        )
        try:
            proc = _run_subprocess(argv, env=build_env(home), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return SSHResult(
                exit_code=None, output=bound_output(_joined(exc.stdout, exc.stderr)), timed_out=True
            )
    return SSHResult(
        exit_code=proc.returncode, output=bound_output(_joined(proc.stdout, proc.stderr))
    )


def _joined(stdout, stderr):
    parts = []
    for chunk in (stdout, stderr):
        if not chunk:
            continue
        parts.append(chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk)
    return "\n".join(parts)


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_REDACTIONS = [
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.S,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (
        re.compile(
            r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
            r"[a-z0-9_]*)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[REDACTED]"),
    (re.compile(r"\bjpat_[A-Za-z0-9_-]{8,}"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"\b(AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})"), "[REDACTED]"),
]


def redact(text):
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def bound_output(text, limit=OUTPUT_MAX_CHARS):
    """Redacted, and at most `limit` characters: the tail is kept (the end
    of a failed build is where the error is)."""
    text = redact(text or "")
    if len(text) <= limit:
        return text
    marker = "[... truncated ...]\n"
    return marker + text[-(limit - len(marker)) :]
