"""Vault frontmatter (MVP 5 branch 3, brief §3; format v2 since Phase 8B
branch 7, brief D12) -- a minimal, hand-rolled flat `key: value` format,
not a full YAML parser: every field this export writes is a single-line
scalar (UUID, enum value, ISO timestamp, hex digest), so pulling in a YAML
dependency for a handful of flat fields would be exactly the kind of
speculative generality CLAUDE.md's simplicity rule warns against.
Matches `docs/memory/obsidian-vault-strategy.md`'s frontmatter standard.

Format v2 adds `friday_format: 2` and `content_sha256` -- the sha256 of the
rendered body below the frontmatter -- so the next run can tell an
owner-edited file (its body no longer hashes to its own `content_sha256`)
from an untouched one, and save the edit to `_conflicts/` instead of
silently overwriting it. A v1 file (no `content_sha256`) is never a
conflict; it is simply rewritten as v2.
"""

import hashlib

_DELIMITER = "---"
FORMAT_VERSION = "2"
EXPORT_SOURCE = "friday_export"


def body_sha256(body: str) -> str:
    """The v2 `content_sha256` of a body. Line endings are normalised first
    (a sync client turning `\\n` into `\\r\\n` is not an owner edit)."""
    normalised = body.replace("\r\n", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def render_note(fields: dict[str, str], body: str) -> str:
    """A full v2 file: `fields` (in order) plus `friday_format` and
    `content_sha256` computed over `body`, then the body itself. The body
    is everything after the closing delimiter's newline, byte for byte."""
    all_fields = {**fields, "friday_format": FORMAT_VERSION, "content_sha256": body_sha256(body)}
    lines = [_DELIMITER]
    lines += [f"{key}: {value}" for key, value in all_fields.items()]
    lines.append(_DELIMITER)
    return "\n".join(lines) + "\n" + body


def render_frontmatter(memory) -> str:
    """Full file text for `memory`: the v2 frontmatter block + a heading +
    content. Regenerated wholesale on every export run (never patched), so
    an edited memory's file always reflects its current row. `project` is
    written empty -- reserved per the vault-strategy doc, not invented here
    (no project concept exists in this codebase yet)."""
    fields = {
        "friday_id": str(memory.id),
        "type": memory.memory_type,
        "workspace": str(memory.workspace_id),
        "project": "",
        "sensitivity": memory.sensitivity,
        "source": EXPORT_SOURCE,
        "updated_at": memory.updated_at.isoformat(),
    }
    body = "\n".join(["", f"# {memory.title}", "", memory.content, ""])
    return render_note(fields, body)


def split_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """`(fields, body)` for a file in the format this job writes, or `None`
    when the frontmatter block does not cleanly match it (see
    `parse_frontmatter`). `body` is everything after the closing
    delimiter's line."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _DELIMITER:
        return None
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == _DELIMITER:
            return fields, "".join(lines[index + 1 :])
        if ":" not in stripped:
            return None
        key, _, value = stripped.partition(":")
        key = key.strip()
        if not key:
            return None
        fields[key] = value.strip()
    return None  # closing delimiter never found


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse a vault file's frontmatter block. Returns `None` -- not a
    partial/best-effort dict -- for anything that doesn't cleanly match the
    format this job itself writes: a missing opening/closing delimiter, or
    a line between them that isn't a `key: value` pair. The prune pass
    (brief §3) treats `None` as "not recognized," which means "never touch
    this file" -- conservatism lives here as much as in the caller's own
    field checks.
    """
    split = split_frontmatter(text)
    return None if split is None else split[0]


def is_owner_edited(text: str) -> bool:
    """Whether an existing export-owned file was edited by the owner (D12):
    its body no longer hashes to its own `content_sha256`.

    - A v1 file (no `content_sha256`) is not a conflict: it is rewritten
      as v2.
    - A file whose frontmatter no longer parses at all is treated as
      edited -- the owner reshaped it, and only a copy keeps their text
      safe (conservative: a needless copy costs nothing, a lost edit
      cannot be undone).
    """
    split = split_frontmatter(text)
    if split is None:
        return True
    fields, body = split
    recorded = fields.get("content_sha256")
    if not recorded:
        return False
    return recorded != body_sha256(body)
