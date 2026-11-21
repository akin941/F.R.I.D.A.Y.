"""The `srt` sandbox policy generator and the node-local policy (Phase 10
brief D4, D5 check 22)."""

import json

import pytest

from nodes.conformance import build
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy, policy_document
from nodes.reference.local_policy import (
    CommandMapping,
    LocalPolicy,
    NotConfigured,
    PolicyError,
)
from nodes.reference.sandbox import (
    DEFAULT_NODE_DIRS,
    DENY_READ_HOME,
    SandboxError,
    build_sandbox,
)

HOME = "/Users/owner"
REPO = "/Users/owner/work/acme"
TEMP = "/private/var/folders/x/T/run-1"
WORKTREE = "/Users/owner/Library/Caches/ai.friday.node/worktrees/wt-1"
NODE_ENV = {
    "GITHUB_TOKEN": "ghp_planted",
    "AWS_SECRET_ACCESS_KEY": "planted",
    "OPENAI_API_KEY": "planted",
    "MY_PASSWORD": "planted",
    "PYTHONHASHSEED": "0",
    "EXTRA_VISIBLE": "yes",
}


def _mapping(**overrides):
    fields = {"kind": "test", "argv": ("python3", "-m", "pytest"), "mode": "in_place"}
    fields.update(overrides)
    return CommandMapping(**fields)


def _spec(mapping=None, **overrides):
    mapping = mapping or _mapping()
    fields = {
        "home": HOME,
        "repo_root": REPO,
        "write_root": REPO if mapping.mode == "in_place" else WORKTREE,
        "temp_dir": TEMP,
        "node_env": NODE_ENV,
    }
    fields.update(overrides)
    return build_sandbox(mapping, **fields)


def test_environment_is_an_allowlist_and_never_carries_a_secret_name():
    spec = _spec(_mapping(env_names=("PYTHONHASHSEED", "EXTRA_VISIBLE")))
    assert spec.env == {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": HOME,
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TERM": "dumb",
        "CI": "1",
        "NO_COLOR": "1",
        "TMPDIR": TEMP,
        "PYTHONHASHSEED": "0",
        "EXTRA_VISIBLE": "yes",
    }
    assert "GITHUB_TOKEN" not in spec.env
    assert not any(value == "planted" or value == "ghp_planted" for value in spec.env.values())


def test_a_listed_secret_name_is_dropped_by_the_generator_and_refused_by_the_policy():
    spec = _spec(_mapping(env_names=("GITHUB_TOKEN", "MY_PASSWORD")))
    assert "GITHUB_TOKEN" not in spec.env and "MY_PASSWORD" not in spec.env
    document = policy_document()
    document["repos"][0]["commands"][0]["env_names"] = ["GITHUB_TOKEN"]
    with pytest.raises(PolicyError):
        LocalPolicy.from_document(document, home=HOME)


def test_every_deny_read_path_is_present():
    deny = _spec().settings["filesystem"]["denyRead"]
    for rel in DENY_READ_HOME + DEFAULT_NODE_DIRS:
        assert f"{HOME}/{rel}" in deny
    for required in (
        ".ssh",
        ".aws",
        ".config/gh",
        ".claude",
        ".codex",
        ".gnupg",
        ".netrc",
        ".docker",
        "Library/Keychains",
        "Library/Safari",
    ):
        assert f"{HOME}/{required}" in deny


def test_only_the_write_root_and_the_private_temp_dir_are_writable():
    spec = _spec()
    assert spec.settings["filesystem"]["allowWrite"] == [REPO, TEMP]
    assert spec.settings["filesystem"]["denyWrite"] == [f"{REPO}/.git"]
    assert "/tmp" not in spec.settings["filesystem"]["allowWrite"]
    worktree = _spec(_mapping(mode="worktree"))
    assert worktree.settings["filesystem"]["allowWrite"] == [WORKTREE, TEMP]
    assert worktree.cwd == WORKTREE


def test_network_is_denied_by_default_and_mapped_domains_are_shown():
    spec = _spec()
    assert spec.settings["network"] == {
        "allowedDomains": [],
        "deniedDomains": [],
        "allowUnixSockets": [],
        "allowLocalBinding": False,
    }
    assert not spec.network_enabled
    with_net = _spec(_mapping(network_domains=("pypi.org", "files.pythonhosted.org")))
    assert with_net.settings["network"]["allowedDomains"] == ["files.pythonhosted.org", "pypi.org"]
    assert with_net.network_enabled


def test_limits_follow_the_mapping_and_the_contract():
    spec = _spec(_mapping(max_runtime_seconds=300), contract_max_runtime=900)
    assert spec.limits == {
        "wall_seconds": 300,
        "cpu_seconds": 300,
        "max_output_bytes": 1024 * 1024,
        "max_processes": 256,
        "kill_grace_seconds": 5,
    }
    assert (
        _spec(_mapping(max_runtime_seconds=900), contract_max_runtime=60).limits["wall_seconds"]
        == 60
    )


def test_argv_is_a_list_never_a_shell_line():
    spec = _spec(_mapping(argv=("npm", "test", "--", "; rm -rf ~")))
    assert spec.argv == ("npm", "test", "--", "; rm -rf ~")
    assert spec.document()["process"]["argv"] == ["npm", "test", "--", "; rm -rf ~"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"write_root": "/Users/owner/elsewhere"},  # in_place writes the repo only
        {"temp_dir": "/tmp"},
        {"temp_dir": "relative/tmp"},
        {"temp_dir": "/Users/owner/.ssh/tmp"},  # overlaps a denied directory
        {"home": "relative"},
    ],
)
def test_unsafe_inputs_are_refused(overrides):
    with pytest.raises(SandboxError):
        _spec(**overrides)


def test_a_worktree_inside_the_source_repo_is_refused():
    with pytest.raises(SandboxError):
        _spec(_mapping(mode="worktree"), write_root=f"{REPO}/.wt")


def test_the_settings_document_is_canonical_and_byte_stable():
    first = _spec().settings_bytes()
    assert first == _spec().settings_bytes()
    assert b"\n" not in first and b": " not in first
    assert json.loads(first) == _spec().settings


def test_sandbox_vectors_are_current():
    path = build.ROOT / "vectors" / "sandbox.json"
    assert path.read_text("utf-8") == build.rendered_files()["vectors/sandbox.json"]
    document = json.loads(path.read_text("utf-8"))
    for vector in document["vectors"]:
        env = vector["expected"]["process"]["env"]
        assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
        assert "SSH_AUTH_SOCK" not in env
        assert (
            vector["settings_canonical"].encode()
            == json.dumps(
                vector["expected"]["settings"],
                separators=(",", ":"),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        )


# --- the local policy (check 22) ----------------------------------------


@pytest.fixture
def home(tmp_path):
    return build_fixture_home(tmp_path)


def test_resolve_maps_repo_and_kind(home):
    policy = fixture_policy(home)
    resolved = policy.resolve(
        "repo.command.run", {"repo_path": "~/work/acme", "command_kind": "test"}
    )
    assert resolved.repo.name == "acme"
    assert resolved.command.argv == ("python3", "-c", "print('acme tests ok')")


@pytest.mark.parametrize(
    ("capability", "args"),
    [
        ("repo.command.run", {"repo_path": "~/work/other", "command_kind": "test"}),
        ("repo.command.run", {"repo_path": "~/work/acme", "command_kind": "build"}),
        ("repo.command.run", {"repo_path": "~/work/link", "command_kind": "test"}),
        ("repo.command.run", {"repo_path": "~/work/acme/", "command_kind": "nope"}),
        ("applescript.recipe.run", {"recipe": "music.next_track", "params": {}}),
        (
            "applescript.recipe.run",
            {"recipe": "finder.reveal_repo", "params": {"repo_path": "~/work/other"}},
        ),
        ("apps.open", {"bundle_id": "com.example.Other"}),
        ("ax.press", {"bundle_id": "com.example.Other", "role": "button", "title": "OK"}),
        ("shell.exec", {}),
    ],
)
def test_resolve_refuses_unmapped_intents(home, capability, args):
    with pytest.raises(NotConfigured):
        fixture_policy(home).resolve(capability, args)


def test_a_symlink_to_a_mapped_root_resolves_to_that_repo(home):
    resolved = fixture_policy(home).resolve(
        "repo.command.run", {"repo_path": "~/work/acme-link", "command_kind": "test"}
    )
    assert resolved.repo.name == "acme"


def test_the_policy_is_read_only(home):
    policy = fixture_policy(home)
    with pytest.raises(AttributeError):
        policy._bundle_ids = frozenset({"com.evil.App"})
    with pytest.raises(AttributeError):
        policy.home = "/"
    assert policy.fingerprint() == fixture_policy(home).fingerprint()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d["repos"][0].update(root="relative/path"),
        lambda d: d["repos"][0]["commands"][0].update(kind="deploy"),
        lambda d: d["repos"][0]["commands"][0].update(argv=[]),
        lambda d: d["repos"][0]["commands"][0].update(argv="python3 -c x"),
        lambda d: d["repos"][0]["commands"][0].update(max_runtime_seconds=901),
        lambda d: d["repos"][0]["commands"][0].update(mode="host"),
        lambda d: d["repos"][0]["commands"][0].update(network_domains=["*.evil.com"]),
        lambda d: d["repos"][0]["commands"][0].update(shell=True),
        lambda d: d["repos"].append(dict(d["repos"][0])),
    ],
)
def test_malformed_policy_documents_are_refused(mutate):
    document = policy_document()
    mutate(document)
    with pytest.raises(PolicyError):
        LocalPolicy.from_document(document, home=HOME)
