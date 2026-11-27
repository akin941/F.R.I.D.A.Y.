"""The policy CLI (D10): only an interactive TTY with the typed basename
changes `policy.json`; it shows the exact change first."""

import os
import stat

import pytest

from friday_node_linux import paths
from friday_node_linux.cli import main
from friday_node_linux.policy import LinuxPolicy


def _policy(env):
    return LinuxPolicy.load(paths.policy_path(env))


def _snapshot(env):
    path = paths.policy_path(env)
    if not path.exists():
        return None
    return path.read_bytes(), os.stat(path).st_mtime_ns


def test_grant_without_a_tty_changes_nothing(make_io, env):
    term = make_io(".claude\n", tty=False)
    assert main(["grant", "claude"], term.io) == 1
    assert "interactive terminal" in term.stderr.getvalue()
    assert not paths.policy_path(env).exists()


def test_grant_with_a_wrong_word_changes_nothing(make_io, env):
    LinuxPolicy().save(paths.policy_path(env))
    before = _snapshot(env)
    term = make_io("claude\n")  # the basename is ".claude"
    assert main(["grant", "claude"], term.io) == 1
    assert "not confirmed" in term.stderr.getvalue()
    assert _snapshot(env) == before


def test_grant_shows_the_exact_change_and_saves_0600(make_io, env, home):
    term = make_io(".claude\n")
    assert main(["grant", "claude"], term.io) == 0
    out = term.stdout.getvalue()
    assert f"GRANT read access to {home / '.claude'}" in out
    assert "Type '.claude' to confirm" in out
    assert _policy(env).grants["claude"] is True
    assert _policy(env).grants["codex"] is False
    assert stat.S_IMODE(os.stat(paths.policy_path(env)).st_mode) == 0o600


def test_revoke_needs_the_same_confirmation(make_io, env):
    LinuxPolicy().with_grant("codex", True).save(paths.policy_path(env))
    assert main(["revoke", "codex"], make_io("nope\n").io) == 1
    assert _policy(env).grants["codex"] is True
    assert main(["revoke", "codex"], make_io(".codex\n").io) == 0
    assert _policy(env).grants["codex"] is False


def test_repo_add_and_remove(make_io, env, home):
    repo = home / "work" / "acme"
    term = make_io("acme\n")
    assert main(["repo", "add", str(repo)], term.io) == 0
    assert "REGISTER repo 'acme' at ~/work/acme" in term.stdout.getvalue()
    assert [(r.name, r.root) for r in _policy(env).repos] == [("acme", "~/work/acme")]
    listing = make_io()
    assert main(["repo", "list"], listing.io) == 0
    assert "acme\t~/work/acme" in listing.stdout.getvalue()
    assert main(["repo", "remove", "acme"], make_io("wrong\n").io) == 1
    assert len(_policy(env).repos) == 1
    assert main(["repo", "remove", "acme"], make_io("acme\n").io) == 0
    assert _policy(env).repos == ()


def test_repo_add_refuses_a_directory_that_is_not_a_repo(make_io, env, home):
    (home / "work" / "plain").mkdir(parents=True)
    term = make_io("plain\n")
    assert main(["repo", "add", str(home / "work" / "plain")], term.io) == 1
    assert "not a git repository" in term.stderr.getvalue()
    assert not paths.policy_path(env).exists()


def test_repo_add_without_a_tty_changes_nothing(make_io, env, home):
    assert main(["repo", "add", str(home / "work" / "acme")], make_io("acme\n", tty=False).io) == 1
    assert not paths.policy_path(env).exists()


def test_an_unsafe_policy_file_is_refused_by_the_cli(make_io, env):
    LinuxPolicy().save(paths.policy_path(env))
    os.chmod(paths.policy_path(env), 0o644)
    term = make_io(".claude\n")
    assert main(["grant", "claude"], term.io) == 1
    assert "refusing the local policy" in term.stderr.getvalue()


@pytest.mark.parametrize("argv", [["policy", "show"], ["status"]])
def test_show_prints_grants_and_repos(make_io, env, argv):
    LinuxPolicy().with_grant("claude", True).with_repo("acme", "~/work/acme").save(
        paths.policy_path(env)
    )
    term = make_io()
    assert main(argv, term.io) == 0
    out = term.stdout.getvalue()
    assert "claude: granted" in out and "codex: not granted" in out
    assert "acme\t~/work/acme" in out
