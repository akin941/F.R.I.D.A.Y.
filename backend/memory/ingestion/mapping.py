"""Workspace mapping: drop, never default (ADR-012 decision 2, brief D4).

A producer hands the port a `mapping_key` naming *where the item came from*:

- `repo:<HarnessRepo id>` -- matched exactly against `repo` rules;
- `path:<absolute or ~ path>` -- the longest normalised `path_prefix` rule
  wins;
- `calendar:<binding id>` -- matched exactly against `calendar_binding`
  rules.

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
}
MAX_KEY_CHARS = 1000
UNMAPPED_SHORT_CHARS = 80


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


def normalise_pattern(rule_type, pattern):
    pattern = (pattern or "").strip()
    if not pattern:
        raise InvalidMappingKey("empty pattern")
    if rule_type == WorkspaceMappingRule.RuleType.PATH_PREFIX:
        return normalise_path(pattern)
    return pattern


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


def resolve_workspace(source: IngestionSource, mapping_key) -> Workspace | None:
    """The target workspace for `mapping_key`, or None (drop). Never raises
    for an unknown or malformed key: that is simply unmapped."""
    try:
        rule_type, value = parse_mapping_key(mapping_key)
    except InvalidMappingKey:
        return None
    rules = WorkspaceMappingRule.objects.filter(
        source=source, rule_type=rule_type, enabled=True
    ).select_related("target_workspace")
    winner = None
    if rule_type == WorkspaceMappingRule.RuleType.PATH_PREFIX:
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


def unmapped_entry(mapping_key):
    """What the audit view keeps of an unmapped key (D4): a hash plus a
    scrubbed short form -- never the item."""
    raw = str(mapping_key)[:MAX_KEY_CHARS]
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    short = scrub_text(raw).text[:UNMAPPED_SHORT_CHARS]
    return {"hash": digest, "short": short}
