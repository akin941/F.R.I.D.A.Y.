"""The local policy file (D10): 0600, owner-only, strict document, atomic
writes, read-only to the daemon."""

import json
import os
import stat

import pytest

from friday_node_linux import paths
from friday_node_linux.policy import LinuxPolicy, PolicyError


def _write(path, body, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    os.chmod(path, mode)


GOOD = {"v": 1, "grants": {"claude": True, "codex": False}, "repos": []}


def test_a_missing_file_is_the_default_nothing_granted(tmp_path):
    policy = LinuxPolicy.load(tmp_path / "policy.json")
    assert dict(policy.grants) == {"claude": False, "codex": False}
    assert policy.repos == ()


def test_round_trip_is_atomic_and_0600(tmp_path):
    path = tmp_path / "cfg" / "policy.json"
    policy = LinuxPolicy().with_grant("claude", True).with_repo("acme", "~/work/acme")
    policy.save(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert LinuxPolicy.load(path) == policy
    assert LinuxPolicy.load(path).fingerprint() == policy.fingerprint()
    # No temporary file is left behind.
    assert [p.name for p in path.parent.iterdir()] == ["policy.json"]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666])
def test_a_mode_wider_than_0600_is_refused_at_load(tmp_path, mode):
    path = tmp_path / "policy.json"
    _write(path, GOOD, mode)
    with pytest.raises(PolicyError, match="0600"):
        LinuxPolicy.load(path)


def test_another_owner_is_refused_at_load(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    _write(path, GOOD)
    real = os.getuid()
    monkeypatch.setattr(paths.os, "getuid", lambda: real + 1)
    with pytest.raises(PolicyError, match="another user"):
        LinuxPolicy.load(path)


def test_a_symlinked_policy_file_is_refused(tmp_path):
    target = tmp_path / "elsewhere.json"
    _write(target, GOOD)
    link = tmp_path / "policy.json"
    os.symlink(target, link)
    with pytest.raises(PolicyError, match="regular file"):
        LinuxPolicy.load(link)


@pytest.mark.parametrize(
    "body",
    [
        {"v": 1, "grants": {}, "repos": [], "extra": 1},
        {"v": 2, "grants": {}, "repos": []},
        {"v": 1, "grants": {"claude": "yes"}, "repos": []},
        {"v": 1, "grants": {"desktop": True}, "repos": []},
        {"v": 1, "grants": {}, "repos": [{"name": "a", "root": "relative"}]},
        {"v": 1, "grants": {}, "repos": [{"name": "../x", "root": "~/x"}]},
        {"v": 1, "grants": {}, "repos": [{"name": "a", "root": "~/a", "commands": []}]},
        {
            "v": 1,
            "grants": {},
            "repos": [{"name": "a", "root": "~/a"}, {"name": "a", "root": "~/b"}],
        },
        [],
    ],
)
def test_malformed_documents_are_refused(tmp_path, body):
    path = tmp_path / "policy.json"
    _write(path, body)
    with pytest.raises(PolicyError):
        LinuxPolicy.load(path)


def test_invalid_json_is_refused(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(PolicyError, match="JSON"):
        LinuxPolicy.load(path)


def test_the_local_policy_maps_repos_only(home):
    policy = LinuxPolicy().with_repo("acme", "~/work/acme")
    local = policy.local_policy(home)
    assert [repo.name for repo in local.repos] == ["acme"]
    assert local.repos[0].commands == {}  # no command mapping: the node runs nothing
    assert local.repo_for_path("~/work/acme").name == "acme"


def test_policy_objects_are_immutable():
    policy = LinuxPolicy()
    with pytest.raises(AttributeError):
        policy.repos = ()
    with pytest.raises(TypeError):
        policy.grants["claude"] = True
