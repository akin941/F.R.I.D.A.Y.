# Obsidian Memory Vault Strategy

Obsidian is Jarvis OS's **local-first, human-readable memory mirror** — never the
primary database (ADR-005). Postgres remains the source of truth.

## Ownership model (no true two-way sync)

| File class | Source of truth | Direction |
|---|---|---|
| Exported mirrors (memories, decisions, agent-run summaries, skill cards) | PostgreSQL | Jarvis → vault (idempotent export job, MVP 2+) |
| Human-authored notes (daily, learning, research, prompts, CLI notes) | Vault | vault → Jarvis ingestion (RAG), MVP 3+ |

## Frontmatter standard (required on synced files)

`jarvis_id`, `type`, `workspace`, `project`, `sensitivity` (normal/private),
`source` (jarvis_export/human), `updated_at`.

Rules: files with `source: jarvis_export` are **never re-ingested** (echo-chamber
guard). `sensitivity: private` content never reaches cloud models. Company
workspace notes live under their workspace folder and never enter personal scope.

## Vault layout

`00_Inbox · 01_Daily · 02_Projects · 03_Workspaces · 04_Agents · 05_Skills ·
06_Decisions · 07_Roadmap · 08_Prompts · 09_Learning · 10_Research ·
12_System_Logs · 99_Archive · _private (sync-ignored) · _templates`

## Security

Agents may only touch the vault path (filesystem adapter path-jail). Deletions and
bulk changes require approval. Export/import operations are audited
(`vault_export` / `vault_ingest`).

## Relationship to repo docs (decision: hybrid)

Repo `docs/` = official engineering truth (agents read this). Vault = thinking and
memory space. Matured vault notes get *moved* into the repo; Jarvis exports flow
only into the vault.
