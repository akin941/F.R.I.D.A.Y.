"""Entity notes for the Obsidian mirror (Phase 8B branch 7, brief D12).

One note per learned graph entity that has at least one approved (or
`system`) fact, so Obsidian's graph view shows the temporal graph:

- an entity that is **private** by D4's derived rule (no live non-`private`
  fact, `MemoryEntity.objects.with_privacy()`) gets its only note under
  `_private/<ws>/Entities/<entity-id>.md`;
- any other entity gets an open note under
  `03_Workspaces/<ws>/Entities/<entity-id>.md` holding only its
  non-`private` facts whose other endpoint is not a private entity, plus,
  if it has any other facts, a `_private/` companion with exactly those;
- an open note never names a private entity: a fact touching one is moved
  to the companion, so neither the private entity's name (in a statement
  or a wiki-link) nor a private fact's text reaches the open tree;
- Graphify `code_*` entities are not mirrored (volume); a fact pointing at
  one names it without a wiki-link.

Each note lists current facts and superseded history (validity windows)
and `[[<entity-id>|<name>]]` wiki-links to related entities of the same
workspace -- a fact's edges never cross workspaces (`MemoryFact.save`, the
migration-0010 trigger), so neither can a link.

System/worker code, like `tools.export`: the workspace comes from the
approved run, not from a request, so `for_actor()` does not apply.
"""

from dataclasses import dataclass, field

from memory.models import MemoryEntity, MemoryFact
from tools.vault_frontmatter import EXPORT_SOURCE, render_note

ENTITY_NOTE_TYPE = "entity"
_UNSAFE_LINK_CHARS = str.maketrans({"[": "(", "]": ")", "|": "/", "\n": " ", "\r": " "})


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _link(entity) -> str:
    alias = _one_line(entity.name).translate(_UNSAFE_LINK_CHARS)
    return f"[[{entity.id}|{alias}]]"


def _day(value) -> str:
    return value.date().isoformat() if value is not None else ""


@dataclass
class _EntityFacts:
    entity: MemoryEntity
    facts: list = field(default_factory=list)


def _is_code(entity) -> bool:
    return entity.kind.startswith("code_")


def _other_endpoint(fact, entity):
    if fact.subject_id == entity.id:
        return fact.object_entity
    return fact.subject


def _render_fact(fact, entity, *, linkable) -> str:
    """One bullet: the statement, its validity window and (when the other
    endpoint is a mirrored entity this note may name) a wiki-link."""
    window = f"{_day(fact.valid_from)} → {_day(fact.valid_to) or 'now'}"
    line = f"- {_one_line(fact.statement)} _({fact.predicate}; {window})_"
    other = _other_endpoint(fact, entity)
    if other is not None and linkable(other):
        line += f" · {_link(other)}"
    return line


def _render_entity_note(entity, facts, *, sensitivity, linkable) -> str:
    current = [f for f in facts if f.valid_to is None]
    history = [f for f in facts if f.valid_to is not None]
    lines = ["", f"# {_one_line(entity.name)}", "", f"Kind: {entity.kind}"]
    aliases = [_one_line(a) for a in (entity.aliases or []) if _one_line(a)]
    if aliases:
        lines.append(f"Aliases: {', '.join(aliases)}")
    lines += ["", "## Current facts", ""]
    lines += [_render_fact(f, entity, linkable=linkable) for f in current] or ["- (none)"]
    lines += ["", "## History", ""]
    lines += [_render_fact(f, entity, linkable=linkable) for f in history] or ["- (none)"]
    related = {}
    for fact in facts:
        other = _other_endpoint(fact, entity)
        if other is not None and other.id != entity.id and linkable(other):
            related[other.id] = other
    if related:
        lines += ["", "## Related", ""]
        ordered = sorted(related.values(), key=lambda e: str(e.id))
        lines += [f"- {_link(other)}" for other in ordered]
    lines.append("")
    body = "\n".join(lines)
    updated = max([entity.updated_at] + [f.updated_at for f in facts])
    fields = {
        "friday_id": str(entity.id),
        "type": ENTITY_NOTE_TYPE,
        "entity_kind": entity.kind,
        "workspace": str(entity.workspace_id),
        "project": "",
        "sensitivity": sensitivity,
        "source": EXPORT_SOURCE,
        "updated_at": updated.isoformat(),
    }
    return render_note(fields, body)


def plan_entity_notes(workspace) -> tuple[dict[str, str], dict[str, str]]:
    """`(open_files, private_files)`: `{"<entity-id>.md": text}` for the
    open `Entities/` folder and the `_private/` one."""
    privacy = dict(
        MemoryEntity.objects.filter(workspace=workspace)
        .with_privacy()
        .values_list("id", "is_private")
    )
    facts = list(
        MemoryFact.objects.filter(
            workspace=workspace,
            status__in=[MemoryFact.Status.APPROVED, MemoryFact.Status.SYSTEM],
        )
        .select_related("subject", "object_entity")
        .order_by("valid_from", "created_at", "id")
    )
    by_entity: dict = {}
    for fact in facts:
        endpoints = {fact.subject_id: fact.subject}
        if fact.object_entity is not None:
            endpoints.setdefault(fact.object_entity_id, fact.object_entity)
        for endpoint in endpoints.values():
            if endpoint.deleted_at is not None or _is_code(endpoint):
                continue
            by_entity.setdefault(endpoint.id, _EntityFacts(endpoint)).facts.append(fact)

    def is_private(entity) -> bool:
        # Unknown (trashed meanwhile) counts as private: never named openly.
        return privacy.get(entity.id, True)

    def open_linkable(entity) -> bool:
        return not _is_code(entity) and not is_private(entity)

    def private_linkable(entity) -> bool:
        return not _is_code(entity) and entity.id in privacy

    open_files: dict[str, str] = {}
    private_files: dict[str, str] = {}
    for entry in by_entity.values():
        entity = entry.entity
        filename = f"{entity.id}.md"
        if is_private(entity):
            private_files[filename] = _render_entity_note(
                entity, entry.facts, sensitivity="private", linkable=private_linkable
            )
            continue
        open_facts, private_facts = [], []
        for fact in entry.facts:
            other = _other_endpoint(fact, entity)
            if fact.sensitivity == MemoryFact.Sensitivity.NORMAL and (
                other is None or not is_private(other)
            ):
                open_facts.append(fact)
            else:
                private_facts.append(fact)
        if open_facts:
            open_files[filename] = _render_entity_note(
                entity, open_facts, sensitivity="normal", linkable=open_linkable
            )
        if private_facts:
            private_files[filename] = _render_entity_note(
                entity, private_facts, sensitivity="private", linkable=private_linkable
            )
    return open_files, private_files
