# CODEX.md — Jarvis OS (short guide)

**Read `CLAUDE.md` first** — it is the authoritative development guide; this file
is only a pointer.

Essentials: personal-first AI OS; Workspace is the data axis (`for_actor()`
scoping everywhere); all LLM calls via the `ai_core` gateway with ModelUsage
logging; write/delete/deploy/external-send only through the approval gate; kernel
apps never import module packs; tests + ruff + CI green before done; Conventional
Commits on `feat/mvpX-*` (legacy MVP track) / `feat/pN-*` (Jarvis 2.0 phases)
branches, never directly on `main`.

Commands: `make up` (web on :8001) · `make test` · `make lint` · `make fmt`.
