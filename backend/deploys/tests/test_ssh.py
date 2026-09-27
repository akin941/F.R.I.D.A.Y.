"""The SSH client's argv/env discipline and the verb grammar (brief D10).
`_run_subprocess` is replaced by a recorder -- no process is spawned."""

import subprocess
from types import SimpleNamespace

import pytest

from deploys import ssh, validation
from deploys.tests.conftest import HOST_KEY, SHA_B

TARGET = {
    "ssh_host": "203.0.113.7",
    "ssh_port": 2222,
    "ssh_user": "deploy",
    "host_public_key": HOST_KEY,
    "remote_app": "adia-web",
}


class Recorder:
    def __init__(self, returncode=0, stdout=b"ok\n", stderr=b"", raise_timeout=False):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raise_timeout = raise_timeout

    def __call__(self, argv, *, env, timeout):
        known_hosts = next(a.split("=", 1)[1] for a in argv if a.startswith("UserKnownHostsFile="))
        with open(known_hosts) as handle:
            self.calls.append(
                {"argv": argv, "env": env, "timeout": timeout, "known_hosts": handle.read()}
            )
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, timeout, output=b"partial", stderr=b"")
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr("deploys.ssh._run_subprocess", rec)
    return rec


def _option(argv, name):
    values = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-o"]
    return [v.split("=", 1)[1] for v in values if v.startswith(name + "=")]


def test_argv_isolates_ssh_from_the_host_configuration(recorder, settings):
    result = ssh.run_verb(TARGET, "deploy", SHA_B, timeout=30)
    assert result.ok
    (call,) = recorder.calls
    argv = call["argv"]
    assert argv[0] == "ssh"
    assert argv[1:3] == ["-F", "/dev/null"]
    assert argv[argv.index("-i") + 1] == settings.DEPLOY_SSH_KEY_PATH
    assert "-a" in argv and "-x" in argv and "-T" in argv
    assert _option(argv, "IdentitiesOnly") == ["yes"]
    assert _option(argv, "IdentityAgent") == ["none"]
    assert _option(argv, "BatchMode") == ["yes"]
    assert _option(argv, "PasswordAuthentication") == ["no"]
    assert _option(argv, "StrictHostKeyChecking") == ["yes"]
    assert _option(argv, "GlobalKnownHostsFile") == ["/dev/null"]
    assert _option(argv, "HostKeyAlias") == [ssh.HOST_KEY_ALIAS]
    assert _option(argv, "UpdateHostKeys") == ["no"]
    assert _option(argv, "ForwardAgent") == ["no"]
    assert _option(argv, "ControlMaster") == ["no"]
    assert argv[argv.index("-p") + 1] == "2222"
    assert argv[argv.index("-l") + 1] == "deploy"
    # The destination and the one remote command come after `--`.
    assert argv[-3:] == ["--", "203.0.113.7", f"deploy adia-web {SHA_B}"]
    assert call["timeout"] == 30


def test_known_hosts_pins_exactly_the_target_key(recorder):
    ssh.run_verb(TARGET, "status", timeout=5)
    assert recorder.calls[0]["known_hosts"] == f"{ssh.HOST_KEY_ALIAS} {HOST_KEY}\n"


def test_env_is_sanitized_no_agent_socket(recorder, monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("SSH_ASKPASS", "/usr/bin/askpass")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    ssh.run_verb(TARGET, "status", timeout=5)
    env = recorder.calls[0]["env"]
    assert set(env) == {"PATH", "HOME", "LANG", "LC_ALL"}
    assert "SSH_AUTH_SOCK" not in env


@pytest.mark.parametrize("key_path", ["", "/nonexistent/jarvis/deploy_key"])
def test_missing_key_fails_closed_before_spawning(recorder, settings, key_path):
    settings.DEPLOY_SSH_KEY_PATH = key_path
    with pytest.raises(ssh.SSHConfigError):
        ssh.run_verb(TARGET, "status", timeout=5)
    assert recorder.calls == []


@pytest.mark.parametrize(
    "verb, app, sha",
    [
        ("shell", "adia-web", None),
        ("status", "adia-web", SHA_B),
        ("deploy", "adia-web", None),
        ("deploy", "adia-web", SHA_B[:12]),
        ("deploy", "adia-web", SHA_B.upper()),
        ("status", "Adia", None),
        ("status", "adia;id", None),
        ("status", "-oProxyCommand=x", None),
        ("rollback", "adia-web", "g" * 40),
    ],
)
def test_grammar_violations_never_spawn(recorder, verb, app, sha):
    target = {**TARGET, "remote_app": app}
    with pytest.raises(validation.TargetFieldError):
        ssh.run_verb(target, verb, sha, timeout=5)
    assert recorder.calls == []


def test_timeout_is_a_transport_failure(monkeypatch):
    monkeypatch.setattr("deploys.ssh._run_subprocess", Recorder(raise_timeout=True))
    result = ssh.run_verb(TARGET, "health", timeout=1)
    assert result.timed_out and result.transport_failed and not result.ok
    assert result.output == "partial"


def test_exit_255_is_a_transport_failure(monkeypatch):
    monkeypatch.setattr("deploys.ssh._run_subprocess", Recorder(returncode=255))
    result = ssh.run_verb(TARGET, "status", timeout=1)
    assert result.transport_failed and not result.ok


def test_output_is_bounded_and_redacted(monkeypatch):
    noisy = (
        b"\x1b[31mred\x1b[0m\x07 password=hunter2 token: abc123\n"
        b"https://user:s3cret@example.com/x Authorization: Bearer abcdefghijklmnop\n"
        b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
        b"ghp_" + b"x" * 36 + b" jpat_abcdefghijkl\n" + b"y" * 40000
    )
    monkeypatch.setattr("deploys.ssh._run_subprocess", Recorder(stdout=noisy))
    result = ssh.run_verb(TARGET, "status", timeout=1)
    assert len(result.output) <= ssh.OUTPUT_MAX_CHARS
    assert result.output.startswith("[... truncated ...]")
    assert result.output.endswith("y")
    head = ssh.bound_output(noisy[:400].decode())
    for secret in ("hunter2", "abc123", "s3cret", "abcdefghijklmnop", "AAAA", "ghp_x", "jpat_a"):
        assert secret not in head, secret
    assert "\x1b" not in head and "\x07" not in head
    assert "red" in head


def test_remote_command_grammar():
    assert validation.build_remote_command("status", "adia-web") == "status adia-web"
    assert validation.build_remote_command("rollback", "a", SHA_B) == f"rollback a {SHA_B}"
    assert validation.VERBS == {"status", "deploy", "health", "rollback"}


@pytest.mark.parametrize(
    "value",
    [
        "ssh-ed25519",
        "ssh-dss AAAAB3NzaC1kc3M=",
        "ssh-ed25519 not-base64!",
        # A real ed25519 blob labelled as rsa.
        "ssh-rsa " + HOST_KEY.split()[1],
    ],
)
def test_host_key_must_be_a_consistent_openssh_line(value):
    with pytest.raises(validation.TargetFieldError):
        validation.normalize_host_public_key(value)


def test_host_key_comment_is_dropped():
    assert validation.normalize_host_public_key(HOST_KEY + " root@vps") == HOST_KEY


@pytest.mark.parametrize("value", ["-oProxyCommand=x", "a b", "", "x" * 300, "host;id"])
def test_bad_hosts_are_refused(value):
    with pytest.raises(validation.TargetFieldError):
        validation.validate_ssh_host(value)


@pytest.mark.parametrize("value", ["-x", "a..b", "/main", "main/", "a b", "x.lock", "a@{1}"])
def test_bad_branches_are_refused(value):
    with pytest.raises(validation.TargetFieldError):
        validation.validate_branch(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://token@github.com/acme/app.git",
        "https://u:p@github.com/acme/app.git",
        "https://github.com/acme/app.git?token=x",
        "deploy@github.com:acme/app.git",
        "file:///srv/app",
        "http://github.com/acme/app.git",
    ],
)
def test_credential_carrying_or_odd_repo_urls_are_refused(value):
    with pytest.raises(validation.TargetFieldError):
        validation.validate_repo_git_url(value)


@pytest.mark.parametrize(
    "value", ["https://u:p@adia.example/health", "ftp://adia.example/", "https://x/#frag"]
)
def test_bad_health_urls_are_refused(value):
    with pytest.raises(validation.TargetFieldError):
        validation.validate_health_url(value)
