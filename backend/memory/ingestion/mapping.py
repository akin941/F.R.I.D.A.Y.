"""Workspace mapping: drop, never default (ADR-012 decision 2, brief D4).

A producer hands the port a `mapping_key` naming *where the item came from*:

- `repo:<HarnessRepo id>` -- matched exactly against `repo` rules;
- `path:<absolute or ~ path>` -- the longest normalised `path_prefix` rule
  wins;
- `calendar:<binding id>` -- matched exactly against `calendar_binding`
  rules;
- `vault:<vault-relative folder under 00_Inbox>` -- the longest normalised
  `vault_prefix` rule wins, on the same segment boundary as `path_prefix`
  (Phase 8B branch 7, brief D12).

`resolve_workspace` returns the target workspace of the winning enabled
rule, or `None` -- and `None` means the item is dropped and counted by the
port, never stored in some default workspace. A rule's target must be a
workspace the source's owner owns (`WorkspaceMember.Role.OWNER`); that is
validated when the rule is written and re-checked here, so a later change
of ownership cannot route items into a workspace the owner no longer owns.
"""

import hashlib
import posixpath

from memory.ingestion.scrubber import scrub_text
from memory.models import IngestionSource, WorkspaceMappingRule
from workspaces.models import Workspace, WorkspaceMember

KEY_PREFIXES = {
    "repo": WorkspaceMappingRule.RuleType.REPO,
    "path": WorkspaceMappingRule.RuleType.PATH_PREFIX,
    "calendar": WorkspaceMappingRule.RuleType.CALENDAR_BINDING,
    "vault": WorkspaceMappingRule.RuleType.VAULT_PREFIX,
}
# The documented vault inbox folder (docs/memory/obsidian-vault-strategy.md;
# ADR-012 says `Inbox/`, its implementation note records the mapping).
VAULT_INBOX_ROOT = "00_Inbox"
# Prefix-matched rule types (longest segment-boundary prefix wins).
PREFIX_RULE_TYPES = frozenset(
    {WorkspaceMappingRule.RuleType.PATH_PREFIX, WorkspaceMappingRule.RuleType.VAULT_PREFIX}
)
MAX_KEY_CHARS = 1000
UNMAPPED_SHORT_CHARS = 80
# Phase 8B branch 8 (brief D13): the transcript sources own no rules of
# their own -- they resolve through the same owner's `claude_code_hooks`
# source, the one path registry (Phase 9 D9). Rule writes on them are
# refused (`services.create_mapping_rule`), so no second registry appears.
SHARED_RULE_SOURCES = {
    IngestionSource.Kind.CLAUDE_CODE_TRANSCRIPTS: IngestionSource.Kind.CLAUDE_CODE_HOOKS,
    IngestionSource.Kind.CODEX_TRANSCRIPTS: IngestionSource.Kind.CLAUDE_CODE_HOOKS,
}


class InvalidMappingKey(ValueError):
    pass


def normalise_path(path):
    """Normalise a path for prefix matching (D4): `~` kept as the literal
    home marker (the server cannot know the Mac's home; rules and keys both
    use it, or both use the absolute form), `.`/`..`/duplicate slashes
    resolved, no trailing slash, case-sensitive. Relative paths are refused:
    a prefix only means something anchored."""
    path = (path or "").strip()
    if not path or "\x00" in path:
        raise InvalidMappingKey("empty path")
    if path == "~" or path.startswith("~/"):
        if len(path) == 1:
            return "~"
        relative = posixpath.normpath(path[1:].lstrip("/") or ".")
        # Normalised relative to '~', a leading '..' climbs out of the home
        # directory: '~/../x' is not '~/x' and must not match its rules.
        if relative == ".." or relative.startswith("../"):
            raise InvalidMappingKey("path climbs above ~")
        return "~" if relative == "." else "~/" + relative
    if not path.startswith("/"):
        raise InvalidMappingKey("path must be absolute or start with ~/")
    normalised = posixpath.normpath(path)
    # POSIX keeps a leading '//' as-is; collapse it.
    if normalised.startswith("//"):
        normalised = "/" + normalised.lstrip("/")
    return normalised


def normalise_vault_path(path):
    """Normalise a vault-relative inbox folder (D12): relative to the vault
    root, `00_Inbox` as its first segment, no NUL or backslash, and no `..`
    segment after `posixpath.normpath` -- so `00_Inbox/a/../../x` (which
    normalises to `x`) and `00_Inbox/../03_Workspaces` are refused (their
    first normalised segment is no longer `00_Inbox`), as are
    absolute and `~` paths. Rules and keys normalise the same way."""
    path = (path or "").strip()
    if not path:
        raise InvalidMappingKey("empty vault path")
    if "\x00" in path or "\\" in path:
        raise InvalidMappingKey("vault path contains NUL or backslash")
    if path.startswith("/") or path.startswith("~"):
        raise InvalidMappingKey("vault path must be relative to the vault root")
    normalised = posixpath.normpath(path)
    segments = normalised.split("/")
    if ".." in segments or segments[0] != VAULT_INBOX_ROOT:
        raise InvalidMappingKey(f"vault path must start with {VAULT_INBOX_ROOT}/")
    return normalised


def normalise_pattern(rule_type, pattern):
    pattern = (pattern or "").strip()
    if not pattern:
        raise InvalidMappingKey("empty pattern")
    if rule_type == WorkspaceMappingRule.RuleType.PATH_PREFIX:
        return normalise_path(pattern)
    if rule_type == WorkspaceMappingRule.RuleType.VAULT_PREFIX:
        return normalise_vault_path(pattern)
    return pattern


def allowed_rule_types(source_kind):
    """Which rule types a source of `source_kind` accepts (Phase 8B branch
    7, brief D12): an `obsidian_inbox` source takes `vault_prefix` only, and
    `vault_prefix` is refused on every other source. A shared-rules source
    (branch 8, D13) takes none: its rules are the hooks source's."""
    if source_kind in SHARED_RULE_SOURCES:
        return set()
    if source_kind == IngestionSource.Kind.OBSIDIAN_INBOX:
        return {WorkspaceMappingRule.RuleType.VAULT_PREFIX}
    return set(WorkspaceMappingRule.RuleType.values) - {WorkspaceMappingRule.RuleType.VAULT_PREFIX}


def parse_mapping_key(mapping_key):
    """`<prefix>:<value>` -> (rule_type, normalised value)."""
    if not isinstance(mapping_key, str) or len(mapping_key) > MAX_KEY_CHARS:
        raise InvalidMappingKey("mapping key must be a string of bounded length")
    prefix, sep, value = mapping_key.partition(":")
    rule_type = KEY_PREFIXES.get(prefix)
    if not sep or rule_type is None:
        raise InvalidMappingKey("unknown mapping key prefix")
    return rule_type, normalise_pattern(rule_type, value)


def _path_matches(prefix, path):
    if prefix in ("/", "~"):
        return path == prefix or path.startswith(prefix.rstrip("/") + "/")
    return path == prefix or path.startswith(prefix + "/")


def owner_of(source):
    return source.workspace.owner


def owner_owns(user, workspace):
    return WorkspaceMember.objects.filter(
        workspace=workspace, user=user, role=WorkspaceMember.Role.OWNER
    ).exists()


def rule_source_for(source: IngestionSource) -> IngestionSource | None:
    """The source whose rules route `source`'s items (D13): the same
    owner's `claude_code_hooks` source in the same (personal) workspace for
    a shared-rules kind, `source` itself for every other kind. None (every
    item unmapped) when a shared-rules source has no hooks source."""
    shared_kind = SHARED_RULE_SOURCES.get(source.kind)
    if shared_kind is None:
        return source
    return IngestionSource.objects.filter(
        workspace_id=source.workspace_id, kind=shared_kind
    ).first()


def resolve_workspace(source: IngestionSource, mapping_key) -> Workspace | None:
    """The target workspace for `mapping_key`, or None (drop). Never raises
    for an unknown or malformed key: that is simply unmapped. A shared-rules
    source resolves through `rule_source_for`; the ownership re-check stays
    on `source`'s own owner."""
    try:
        rule_type, value = parse_mapping_key(mapping_key)
    except InvalidMappingKey:
        return None
    rule_source = rule_source_for(source)
    if rule_source is None:
        return None
    rules = WorkspaceMappingRule.objects.filter(
        source=rule_source, rule_type=rule_type, enabled=True
    ).select_related("target_workspace")
    winner = None
    if rule_type in PREFIX_RULE_TYPES:
        candidates = [r for r in rules if _path_matches(r.pattern, value)]
        if candidates:
            winner = max(candidates, key=lambda r: len(r.pattern))
    else:
        winner = next((r for r in rules if r.pattern == value), None)
    if winner is None:
        return None
    target = winner.target_workspace
    if not owner_owns(owner_of(source), target):
        return None
    return target


def _hooks_source(owner):
    return (
        IngestionSource.objects.filter(
            kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS,
            workspace__owner=owner,
            workspace__type=Workspace.Type.PERSONAL,
        )
        .order_by("created_at")
        .first()
    )


def prefixes_for_workspace(owner, workspace) -> list[str]:
    """The owner's enabled `path_prefix` rules (of the `claude_code_hooks`
    source, the one path registry) that target `workspace`, sorted. The
    transcript pull sends exactly these as `cwd_prefixes` (D13). Empty when
    the owner does not own `workspace` (Phase 9 D9 helper; branch 8 lands it
    here because Phase 9 branch 5 has not merged yet)."""
    hooks = _hooks_source(owner)
    if hooks is None or not owner_owns(owner, workspace):
        return []
    return sorted(
        WorkspaceMappingRule.objects.filter(
            source=hooks,
            rule_type=WorkspaceMappingRule.RuleType.PATH_PREFIX,
            target_workspace=workspace,
            enabled=True,
        ).values_list("pattern", flat=True)
    )


def workspace_for_path(owner, path) -> Workspace | None:
    """The workspace the owner's hooks path rules route `path` to (longest
    prefix wins), or None (Phase 9 D9 helper)."""
    hooks = _hooks_source(owner)
    if hooks is None:
        return None
    return resolve_workspace(hooks, f"path:{path}")


def unmapped_entry(mapping_key):
    """What the audit view keeps of an unmapped key (D4): a hash plus a
    scrubbed short form -- never the item."""
    raw = str(mapping_key)[:MAX_KEY_CHARS]
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    short = scrub_text(raw).text[:UNMAPPED_SHORT_CHARS]
    return {"hash": digest, "short": short}
