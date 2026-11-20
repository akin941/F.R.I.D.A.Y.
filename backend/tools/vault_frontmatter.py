"""Vault frontmatter (MVP 5 branch 3, brief §3) -- a minimal, hand-rolled
flat `key: value` format, not a full YAML parser: every field this MVP
writes is a single-line scalar (UUID, enum value, ISO timestamp), so pulling
in a YAML dependency for a handful of flat fields would be exactly the kind
of speculative generality CLAUDE.md's simplicity rule warns against.
Matches `docs/memory/obsidian-vault-strategy.md`'s frontmatter standard.
"""

_DELIMITER = "---"


def render_frontmatter(memory) -> str:
    """Full file body for `memory`: frontmatter block + a heading + content.
    Regenerated wholesale on every export run (never patched), so an edited
    memory's file always reflects its current row. `project` is written
    empty -- reserved per the vault-strategy doc, not invented here (no
    project concept exists in this codebase yet)."""
    fields = {
        "friday_id": str(memory.id),
        "type": memory.memory_type,
        "workspace": str(memory.workspace_id),
        "project": "",
        "sensitivity": memory.sensitivity,
        "source": "friday_export",
        "updated_at": memory.updated_at.isoformat(),
    }
    lines = [_DELIMITER]
    lines += [f"{key}: {value}" for key, value in fields.items()]
    lines.append(_DELIMITER)
    lines.append("")
    lines.append(f"# {memory.title}")
    lines.append("")
    lines.append(memory.content)
    lines.append("")
    return "\n".join(lines)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse a vault file's frontmatter block. Returns `None` -- not a
    partial/best-effort dict -- for anything that doesn't cleanly match the
    format this job itself writes: a missing opening/closing delimiter, or
    a line between them that isn't a `key: value` pair. The prune pass
    (brief §3) treats `None` as "not recognized," which means "never touch
    this file" -- conservatism lives here as much as in the caller's own
    field checks.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIMITER:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == _DELIMITER:
            return fields
        if ":" not in stripped:
            return None
        key, _, value = stripped.partition(":")
        key = key.strip()
        if not key:
            return None
        fields[key] = value.strip()
    return None  # closing delimiter never found
