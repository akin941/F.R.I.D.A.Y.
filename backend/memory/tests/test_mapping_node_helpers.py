"""The Phase 9 D9 path helpers in `memory.ingestion.mapping` (branch 5):
`prefixes_for_workspace` (the `cwd_prefixes` of every node list call),
`workspace_for_path` and `path_resolver` (the read tools' per-row filter,
rules loaded once). One path registry: the owner's `claude_code_hooks`
`path_prefix` rules. Drop, never default."""

import pytest

from accounts.services import register_user
from memory.ingestion.mapping import (
    path_resolver,
    prefixes_for_workspace,
    workspace_for_path,
)
from memory.models import WorkspaceMappingRule
from memory.tests.ingestion_helpers import PASSWORD
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PATH = WorkspaceMappingRule.RuleType.PATH_PREFIX


@pytest.fixture
def secret(owner):
    workspace = Workspace.objects.create(name="Secret", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, user=owner, role=WorkspaceMember.Role.OWNER)
    return workspace


def rule(hooks, pattern, target, *, enabled=True, rule_type=PATH):
    return WorkspaceMappingRule.objects.create(
        workspace=hooks.workspace,
        source=hooks,
        rule_type=rule_type,
        pattern=pattern,
        target_workspace=target,
        created_by=hooks.workspace.owner,
        enabled=enabled,
    )


@pytest.fixture
def rules(hooks, personal, firm, secret):
    return [
        rule(hooks, "~/work", firm),
        rule(hooks, "~/work/secret", secret),
        rule(hooks, "~/code", personal),
        rule(hooks, "~/off", personal, enabled=False),
        rule(hooks, "/Users/owner/abs", firm),
    ]


PATHS = [
    "~/work",
    "~/work/acme",
    "~/work/secret",
    "~/work/secret/deep/x",
    "~/work/secret-other",
    "~/work/./secret/../acme",
    "~/code/friday",
    "~/codex",
    "~/off/x",
    "~/../work",
    "/Users/owner/abs/repo",
    "/Users/owner/work/acme",
    "/tmp/scratch",
    "relative/x",
    "",
    "~/work\x00/acme",
    "~/" + "a" * 1200,
    None,
    42,
]


def test_the_resolver_agrees_with_workspace_for_path_on_every_path(owner, rules):
    resolve = path_resolver(owner)
    for path in PATHS:
        expected = workspace_for_path(owner, path) if isinstance(path, str) else None
        assert resolve(path) == expected, path


def test_longest_prefix_wins_on_segment_boundaries(owner, rules, personal, firm, secret):
    resolve = path_resolver(owner)
    assert resolve("~/work/acme") == firm
    assert resolve("~/work/secret/deep") == secret
    assert resolve("~/work/secret-other") == firm  # not under ~/work/secret
    assert resolve("~/codex") is None  # not under ~/code
    assert resolve("~/code") == personal
    # Disabled rules, unmapped and unanchored paths: dropped.
    assert resolve("~/off/x") is None
    assert resolve("/tmp/scratch") is None
    assert resolve("relative/x") is None
    # `~` and the absolute home are different keys (the server cannot know
    # the Mac's home): a `~/` rule never matches the absolute form.
    assert resolve("/Users/owner/work/acme") is None


def test_prefixes_are_the_enabled_rules_targeting_the_workspace(owner, rules, firm, secret):
    assert prefixes_for_workspace(owner, firm) == ["/Users/owner/abs", "~/work"]
    assert prefixes_for_workspace(owner, secret) == ["~/work/secret"]


def test_a_workspace_the_owner_no_longer_owns_resolves_to_nothing(owner, rules, secret):
    WorkspaceMember.objects.filter(workspace=secret, user=owner).update(
        role=WorkspaceMember.Role.MEMBER
    )
    assert path_resolver(owner)("~/work/secret/x") is None
    assert workspace_for_path(owner, "~/work/secret/x") is None
    assert prefixes_for_workspace(owner, secret) == []


def test_another_user_has_no_rules(owner, rules, firm):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    assert path_resolver(stranger)("~/work/acme") is None
    assert prefixes_for_workspace(stranger, firm) == []


def test_no_hooks_source_resolves_nothing(owner, firm):
    assert path_resolver(owner)("~/work/acme") is None
    assert prefixes_for_workspace(owner, firm) == []
