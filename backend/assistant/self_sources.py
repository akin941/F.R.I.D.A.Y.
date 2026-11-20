"""The `self` block's source snapshot (Phase 8A D8).

`build_snapshot(repo_root)` reads three documents and returns quoted lines
with `file:line` anchors:

- `CLAUDE.md`: the identity sentence (first sentence of the paragraph under
  the title) and the headline of each "Non-negotiable architecture rules"
  item;
- `docs/adr/ADR-*.md`: for each ADR whose status line starts with
  "Accepted", its title, status line and the first sentence of its
  decision;
- `docs/MASTER_PLAN.md` §AA "Friday 3.0 phases" table: the row whose status
  says "in progress", else the first "planned" row.

Pure text processing, no Django model access, no network. The snapshot is
committed as `assistant/self_sources.json` (so the image need not contain
`docs/`), and `manage.py build_self_sources --check` fails when it drifts
from the documents (CI runs it, like `makemigrations --check`).
"""

import hashlib
import json
import re
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent / "self_sources.json"
SNAPSHOT_VERSION = 1

CLAUDE_MD = "CLAUDE.md"
ADR_GLOB = "docs/adr/ADR-*.md"
MASTER_PLAN = "docs/MASTER_PLAN.md"
PHASES_HEADING = "### Friday 3.0 phases"
RULES_HEADING = "## Non-negotiable architecture rules"

MAX_QUOTE_CHARS = 240

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LIST_PREFIX = re.compile(r"^\s*(?:\d+\.|[-*])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\"'(])")
_ADR_TITLE = re.compile(r"^#\s+(ADR-\d+):\s*(.+?)\s*$")


class SnapshotError(Exception):
    """A source document is missing or no longer has the expected shape."""


def _clean(text):
    text = _BOLD.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_QUOTE_CHARS:
        text = text[: MAX_QUOTE_CHARS - 1].rstrip() + "…"
    return text


def _first_sentence(text):
    text = re.sub(r"\s+", " ", _BOLD.sub(r"\1", text)).strip()
    return _SENTENCE_END.split(text, maxsplit=1)[0]


def _read_lines(root, rel):
    path = Path(root) / rel
    if not path.is_file():
        raise SnapshotError(f"{rel} not found under {root}")
    return path.read_text(encoding="utf-8").splitlines()


def _paragraph(lines, start):
    """Lines from `start` up to the next blank line (or a heading)."""
    out = []
    for line in lines[start:]:
        if not line.strip() or line.startswith("#"):
            break
        if out and _LIST_PREFIX.match(line):
            break  # the next list item starts a new paragraph
        out.append(line.strip())
    return " ".join(out)


def _claude_md(root):
    lines = _read_lines(root, CLAUDE_MD)
    identity = None
    for index, line in enumerate(lines):
        if index == 0 or line.startswith("#") or not line.strip():
            continue
        identity = {
            "file": CLAUDE_MD,
            "line": index + 1,
            "text": _clean(_first_sentence(_paragraph(lines, index))),
        }
        break
    if identity is None:
        raise SnapshotError("CLAUDE.md has no identity paragraph")

    rules = []
    in_rules = False
    for index, line in enumerate(lines):
        if line.strip() == RULES_HEADING:
            in_rules = True
            continue
        if in_rules and line.startswith("#"):
            break
        if in_rules and re.match(r"^\d+\.\s", line):
            match = _BOLD.search(line)
            text = match.group(1) if match else _first_sentence(_LIST_PREFIX.sub("", line))
            rules.append({"file": CLAUDE_MD, "line": index + 1, "text": _clean(text)})
    if not rules:
        raise SnapshotError("CLAUDE.md has no architecture rules")
    return identity, rules


def _section_start(lines, heading):
    for index, line in enumerate(lines):
        if line.strip() == heading:
            return index
    return None


def _adr(root, path):
    rel = path.relative_to(root).as_posix()
    lines = path.read_text(encoding="utf-8").splitlines()
    title_match = _ADR_TITLE.match(lines[0]) if lines else None
    if title_match is None:
        raise SnapshotError(f"{rel} has no '# ADR-NNN: Title' line")
    status_at = _section_start(lines, "## Status")
    if status_at is None:
        raise SnapshotError(f"{rel} has no Status section")
    status_index = next((i for i in range(status_at + 1, len(lines)) if lines[i].strip()), None)
    if status_index is None or not lines[status_index].strip().startswith("Accepted"):
        return None
    decision_at = _section_start(lines, "## Decision")
    if decision_at is None:
        raise SnapshotError(f"{rel} has no Decision section")
    decision = None
    index = decision_at + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("#"):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith(("|", ">")):
            index += 1
            continue
        paragraph = _LIST_PREFIX.sub("", _paragraph(lines, index))
        decision = {"line": index + 1, "text": _clean(_first_sentence(paragraph))}
        break
    if decision is None:
        raise SnapshotError(f"{rel} has an empty Decision section")
    return {
        "id": title_match.group(1),
        "title": _clean(title_match.group(2)),
        "file": rel,
        "status": {"line": status_index + 1, "text": _clean(lines[status_index])},
        "decision": decision,
    }


def _adrs(root):
    paths = sorted(Path(root).glob(ADR_GLOB))
    if not paths:
        raise SnapshotError("no ADRs found")
    return [adr for adr in (_adr(Path(root), p) for p in paths) if adr is not None]


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _phase(root):
    lines = _read_lines(root, MASTER_PLAN)
    start = next((i for i, line in enumerate(lines) if line.startswith(PHASES_HEADING)), None)
    if start is None:
        raise SnapshotError("MASTER_PLAN has no 'Friday 3.0 phases' section")
    header = None
    rows = []
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("#"):
            break
        if not line.startswith("|"):
            if header is not None and rows:
                break
            continue
        cells = _cells(line)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append((index, cells))
    if header is None or not rows:
        raise SnapshotError("the Friday 3.0 phases table is missing")
    try:
        phase_col = header.index("phase")
        outcome_col = header.index("usable outcome")
        status_col = header.index("status")
    except ValueError as exc:
        raise SnapshotError("the phases table lost a Phase/Usable outcome/Status column") from exc

    def status_of(cells):
        return cells[status_col].lower() if len(cells) > status_col else ""

    chosen = next((r for r in rows if "in progress" in status_of(r[1])), None)
    if chosen is None:
        chosen = next((r for r in rows if status_of(r[1]).startswith("planned")), None)
    if chosen is None:
        raise SnapshotError("no phase is in progress or planned")
    index, cells = chosen
    return {
        "file": MASTER_PLAN,
        "line": index + 1,
        "phase": _clean(cells[phase_col]),
        "outcome": _clean(cells[outcome_col]),
        "status": _clean(cells[status_col]),
    }


def content_hash(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_snapshot(repo_root):
    identity, rules = _claude_md(repo_root)
    body = {
        "version": SNAPSHOT_VERSION,
        "identity": identity,
        "rules": rules,
        "adrs": _adrs(repo_root),
        "phase": _phase(repo_root),
    }
    return {**body, "content_hash": content_hash(body)}


def render_snapshot(snapshot):
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


def load_snapshot(path=None):
    """The committed snapshot, or None when it is missing or unreadable."""
    path = Path(path) if path is not None else SNAPSHOT_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
