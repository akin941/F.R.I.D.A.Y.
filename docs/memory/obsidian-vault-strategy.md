# Obsidian Memory Vault Strategy

Obsidian is Friday OS's **local-first, human-readable memory mirror** — never the
primary database (ADR-005). Postgres remains the source of truth.

## Ownership model (no true two-way sync)

| File class | Source of truth | Direction |
|---|---|---|
| Exported mirrors (memories, decisions, agent-run summaries, skill cards) | PostgreSQL | Friday → vault (idempotent export job, MVP 2+) |
| Human-authored notes (daily, learning, research, prompts, CLI notes) | Vault | vault → Friday ingestion (RAG), MVP 3+ |

## Frontmatter standard (required on synced files)

`friday_id`, `type`, `workspace`, `project`, `sensitivity` (normal/private),
`source` (friday_export/human), `updated_at`. Format v2 (Phase 8B branch 7)
adds `friday_format: 2` and `content_sha256` -- the sha256 of the body below
the frontmatter (line endings normalised) -- on every exported file; entity
notes also carry `type: entity` and `entity_kind`.

Rules: files with `source: friday_export` are **never re-ingested** (echo-chamber
guard). `sensitivity: private` content never reaches cloud models. Company
workspace notes live under their workspace folder and never enter personal scope.

## Vault layout

`00_Inbox · 01_Daily · 02_Projects · 03_Workspaces · 04_Agents · 05_Skills ·
06_Decisions · 07_Roadmap · 08_Prompts · 09_Learning · 10_Research ·
12_System_Logs · 99_Archive · _private (sync-ignored) · _templates`

The memory export (`vault_export`, `backend/tools/export.py`) writes approved
memories to `03_Workspaces/<workspace>/Memory/`, except `sensitivity: private`
ones, which go to `_private/<workspace>/Memory/` (Phase 4 branch 1). Each
tree is reconciled separately, so a private memory's older copy in the main
tree is pruned on the next export.

### Entity notes (Phase 8B branch 7)

Every learned graph entity with an approved fact gets a note under
`03_Workspaces/<workspace>/Entities/<entity-id>.md`: its current facts, its
superseded history with validity windows, and `[[<entity-id>|<name>]]`
wiki-links to related entities of the same workspace, so Obsidian's graph
view shows the graph. Privacy follows the graph's derived rule (an entity
with no live non-private fact is private):

- a private entity's only note is `_private/<workspace>/Entities/<id>.md`;
- any other entity's open note holds only its non-private facts that do not
  touch a private entity; the rest go to a `_private/` companion with the
  same file name. An open note never names or links a private entity;
- when an entity becomes private, the next run writes its `_private/` note
  and prunes the open one;
- Graphify `code_*` entities are not mirrored (volume); pending, rejected,
  archived and trashed facts are never mirrored.

### Owner edits: conflict copies (Phase 8B branch 7)

The vault is still a mirror: the next run overwrites an exported file from
Postgres. Before it overwrites (or prunes) an export-owned file whose body
no longer matches its own `content_sha256`, it copies the file as it is to:

- `_conflicts/<YYYY-MM-DD>/<workspace-id>/<file name>` for a file in the
  open tree;
- `_private/_conflicts/<YYYY-MM-DD>/<workspace-id>/<file name>` for a file
  under `_private/` -- the tier comes from the path, never from the edited
  frontmatter, so private text never leaves `_private/`.

A second copy of the same file on the same day gets a numeric suffix and
never replaces the first. A v1 file (no `content_sha256`) is not a conflict;
it is rewritten as v2. A file whose frontmatter no longer parses counts as
edited. A symlinked `_conflicts` (or `_private/_conflicts`) is refused and
the run fails before the edited file is touched. The run counts the copies
(`VaultExportRun.conflicts_saved`, on the approval detail) and leaves a
`work_log` notice: "Obsidian'da düzenlenmiş N not `_conflicts/<tarih>/`
klasörüne kaydedildi; değişikliği web arayüzünde yapın." Conflict copies are
never read back into Postgres: to keep an edit, make it in the web UI.

### `00_Inbox/` import-once (Phase 8B branch 7, opt-in)

When the owner enables the `obsidian_inbox` ingestion source (seeded
disabled) and adds `vault_prefix` rules (for example `00_Inbox/work` -> the
firm workspace), each approved `vault_export` run of the owner's personal
workspace first scans `00_Inbox/` (top level and one subfolder level,
`.md` only, at most 200 notes per run, each at most 64 KB):

- skipped and left in place: `_imported/`, hidden folders, symlinks,
  oversized or non-UTF-8 notes, unclosed frontmatter, empty bodies, and any
  note with `source: friday_export` (the echo-chamber guard);
- an unmapped folder's notes are counted and **left in place**, so a rule
  can be added later;
- a mapped note becomes one scrubbed episode (`obsidian.inbox_note`, body
  capped at 4000 characters after scrubbing) and is then moved to
  `00_Inbox/_imported/<YYYY-MM-DD>/`. It is never read again; dedupe holds
  even if the move fails.

**Provider routing.** Inbox notes are not personal-class: a normal note may
be extracted by a cloud provider, like a git commit. Mark a sensitive note
`sensitivity: private` in its frontmatter (any value other than `normal`
is treated as private) and it is processed by local providers only, its
learned facts born private. The approval description says so.

The approval description (`display_summary`) of a pending `vault_export`
request shows the counts it
will touch: memories, entity notes, inbox notes waiting and unmapped, and
the conflicts a dry pass detects (when the vault is reachable from the
process rendering it; the web container has no vault mount, so these read
as unknown there).

## Security

Agents may only touch the vault path (filesystem adapter path-jail). Deletions and
bulk changes require approval. Export/import operations are audited
(`vault_export` / `vault_ingest`).

## Relationship to repo docs (decision: hybrid)

Repo `docs/` = official engineering truth (agents read this). Vault = thinking and
memory space. Matured vault notes get *moved* into the repo; Friday exports flow
only into the vault.
