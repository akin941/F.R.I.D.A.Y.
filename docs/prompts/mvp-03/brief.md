# MVP 3 — Knowledge: Document Ingestion + Embeddings + RAG Context Injection

> Status: **done (2026-07-08)** — all 6 branches merged to main (scope
> approved by the user 2026-07-07:
> manual document/text/URL upload only — Obsidian vault ingestion explicitly
> deferred beyond this MVP; embedding layer is provider-agnostic, defaulting
> to OpenAI `text-embedding-3-small`. Roadmap slot: personal kernel step 3
> (shell/chat → memory → **knowledge/RAG** → tasks → tools/approvals → router
> → agents → skills → integrations). Binding constraints: ADR-001 (workspace
> scoping), ADR-002 (modular monolith, one-way deps), ADR-003 (AI Gateway
> single door + ModelUsage on every call, embeddings explicitly named),
> ADR-004 (approval gate rules), CLAUDE.md rules 1-9. Codex CLI plan review:
> draft v1 (CHANGES REQUESTED, 9 findings), draft v2 (CHANGES REQUESTED, 4
> findings — all 9 round-1 fixes confirmed sound), draft v3 (CHANGES
> REQUESTED, 2 findings — the other 4 confirmed sound, incl. the Django
> partial-unique-index, urllib3 pinned-IP/SNI approach, and pgvector
> variable-dimension column), draft v4 (**APPROVE**, one non-blocking wording
> nit — folded into this document — on the adapter-contract paragraph in §1:
> a chat-capability row and an embedding-capability row are distinct
> `ModelProvider` rows since `capability` is single-valued per row; it is the
> table and adapter class/mapping that is shared, not literally one row).
> Round-3 findings folded into v4: retrieval assumed `embed()` returns a
> result with a null provider when none is available, but the gateway
> contract is to *raise* `NoProviderAvailable` (mirroring `complete()`) —
> retrieval must catch it; and the fake-embedding spec said `hash(token)`
> without ruling out Python's salted, per-process built-in `hash()`, with no
> fixture-level rank assertion proving the exact e2e corpus actually ranks
> correctly.

## Goal and user value

Jarvis can answer questions grounded in documents the user gives it, not just
what it remembers from conversation. Users upload text, paste content, or
point at a URL; Jarvis chunks and embeds it, then retrieves the most relevant
chunks into chat context — scoped, auditable, deletable, and never leaking
company data into personal scope, mirroring the trust model MVP 2 built for
memory.

## Scope

### 1. Embedding capability on the existing AI Gateway (`ai_core`) — no parallel tables

ADR-003 is explicit: *every* LLM call — "chat, extraction, embeddings, agent
steps..." — goes through `InferenceGateway` and writes **one `ModelUsage`
row**. Draft v1 proposed a separate `EmbeddingProvider`/`EmbeddingUsage` pair;
Codex round 1 correctly called this an ADR-003 violation. v2 instead extends
the existing `ModelProvider`/`ModelUsage` models — embeddings are a second
*capability* of the same gateway, not a second gateway.

- `ModelProvider` gains a `capability` field: `CharField` choices `chat` /
  `embedding`, default `chat` (migration backfills all existing rows to
  `chat`, matching current behavior exactly — no behavior change for MVP 1/2
  providers). `dimension` field added too (`PositiveIntegerField`, default
  0, meaningful only when `capability="embedding"`). No new columns needed
  for pricing: embeddings have no output tokens, so they use the existing
  `input_price_per_mtok` and leave `output_price_per_mtok=0`.
- `InferenceGateway._select_providers()` gains a `capability` parameter
  (default `"chat"`, preserving every existing caller's behavior unchanged);
  it filters `ModelProvider.objects.filter(is_active=True,
  capability=capability)` before the existing priority/fake-flag/env-key
  selection logic — unchanged otherwise.
- `InferenceGateway.embed(purpose, texts, *, actor, workspace,
  prompt_version=None, on_usage=None)`: same structural shape as
  `complete()` — selects providers with `capability="embedding"`, same
  retry-then-fallback-to-second-provider policy, same `GatewayError`/
  `retryable` contract, same `on_usage(usage)` callback invoked exactly once
  per call. Writes a `ModelUsage` row via the *same* `_record()` method
  `complete()` already uses (`output_tokens=0`, `purpose` as given, e.g.
  `"knowledge.embed"` / `"knowledge.retrieval"`). The number of texts in the
  batch goes into `context_report["item_count"]` — an existing JSONField,
  not a new column. Returns `EmbeddingGatewayResult(vectors:
  list[list[float]], usage: ModelUsage)`. No streaming variant.
- Adapter contract: `BaseAdapter` gains `embed(self, texts, *, purpose="")`
  (default raises `AdapterError("not_supported", retryable=False)` — the
  existing `AnthropicAdapter` does not implement it, since Anthropic has no
  embeddings endpoint, and inherits this default). `OpenAICompatibleAdapter.
  embed()` calls `client.embeddings.create(model=provider.model_id,
  input=texts)`, reusing the adapter's existing `_wrap_error()`
  classification (timeouts/5xx/rate-limit retryable, auth/permanent-4xx
  not) — no new adapter class hierarchy, no new `get_adapter()` mapping;
  the same `ModelProvider` table and adapter class/mapping answer both
  `complete()`/`stream()` and `embed()` depending on which the caller
  invokes (a chat-capability row and an embedding-capability row are still
  distinct rows, since `capability` is single-valued per row — it is the
  table and adapter machinery that is shared, not literally one row).
  `FakeAdapter.embed()` (same class, `ai_core/adapters/fake.py`),
  purpose-aware the same way `_text_for()` already branches on `purpose` for
  chat fixtures, at its own fixed 32-dimension (round 2 finding: draft v2
  contradicted itself, elsewhere saying fake could stay small while also
  saying it must match the real provider's dimension — it does not need to,
  per the dimension policy below, so 32 it is, consistently). **Not an
  arbitrary hash** (round 2 finding: an arbitrary hash cannot satisfy branch
  6's e2e requirement that similarity genuinely ranks the seeded chunk
  first): a deterministic bag-of-words embedding — lowercase, split on
  whitespace/punctuation, and for each distinct token derive a per-token
  unit vector via `hashlib.sha256(token.encode()).digest()` used to seed
  `random.Random(...)` (**not Python's built-in `hash()`**, which is
  per-process salted by default and is not stable across runs or processes
  — round 3 finding: the draft's earlier wording didn't rule this out), sum
  the token vectors for a text and L2-normalize the result. Two texts
  sharing more vocabulary land closer together under cosine similarity by
  construction, which is exactly the property a real embedding model
  provides and the e2e test needs — not a coincidence of a good hash, an
  actual (if crude) semantic-similarity signal. **A dedicated pytest in
  branch 1, not just the branch-6 e2e test, must assert this concretely**
  (round 3 finding: a bag-of-words scheme's signal-to-noise ratio is a real
  risk for short, low-overlap text, so it needs to be proven against the
  *exact* fixture corpus branch 6 will use, not assumed to work from the
  algorithm's shape alone) — e.g. `cosine(embed(seed_document_text),
  embed(query_text)) > cosine(embed(noise_document_text), embed(query_text))`
  for the literal strings branch 6's Playwright test seeds and queries with,
  chosen with enough distinctive shared vocabulary between the seed document
  and the query (and little to none in the noise text) that the margin is
  not marginal.
- **Vector dimension policy (resolves draft v1's unresolved conflict)**:
  `KnowledgeChunk.embedding` (branch 2) is a `pgvector.django.VectorField`
  declared **without a fixed `dimensions` kwarg** (pgvector's
  variable-length `vector` column type — comparison operators only require
  the two operand vectors to match each other's dimension at query time, not
  the column's declared dimension). Consistency is enforced at the
  application layer, not the schema layer: retrieval (§4) always embeds the
  query with one specific, single active embedding provider and filters
  candidate chunks to rows produced by that exact same provider before ever
  invoking the `<=>` operator — so a cross-dimension comparison can never
  reach Postgres in the first place. This also means the fake provider does
  not need to match the real provider's dimension; each provider's chunks
  simply live in their own comparable subset. (The seed data still keeps the
  fake provider's dimension small — e.g. 32 — purely for fast test fixtures;
  draft v1's claim that this required a fixed-column dimension was the
  actual conflict, not the small dimension choice itself.)
- `seed_ai_defaults` gains a fake embedding-capability provider (always
  seeded, `capability="embedding"`, dimension 32, selectable only behind
  `AI_FAKE_PROVIDER`) and a real OpenAI embedding-capability provider
  (`text-embedding-3-small`, dimension 1536) seeded only when
  `OPENAI_API_KEY` is present — same conditional pattern as the existing
  chat providers, same function, new rows.
- New dependency: `pgvector` (Python package; the Postgres image is already
  `pgvector/pgvector:pg16` per `deploy/docker/compose.yml`, unused until
  now). The extension-enabling migration (`CREATE EXTENSION IF NOT EXISTS
  vector`) lives in the `knowledge` app's first migration (branch 2), the
  first and only place a vector column is created; `ai_core` itself stores
  no vectors and needs no migration for this.
- Static guard test (mirrors the existing Anthropic/OpenAI SDK guard):
  `openai` usage (chat and embeddings both) stays inside
  `ai_core/adapters/`.

### 2. `knowledge` kernel app — model + governance + synchronous chunking

- `KnowledgeDocument` (extends `WorkspaceScopedModel`): `source_type`
  (`pasted_text` / `file_upload` / `url`), `title`, `status` (`pending` /
  `processing` / `ready` / `failed`), `sensitivity` (`normal` / `private` —
  same two-value enum as `Memory`, for the same reason: a uniform
  cloud-eligibility story), `mime_type`, `byte_size`, `source_url` (nullable,
  `url` only), `error_message`, `created_by`, lease fields (`claim_token` +
  `lease_expires_at` — one embedding attempt per document, no separate job
  model needed, unlike memory's per-message-range extraction which genuinely
  needs range bookkeeping).
  **`raw_text` and `content_hash` are both nullable** (round 2 finding:
  a `url` document is created *before* fetch/chunk succeeds, so neither
  value exists yet at creation time — `pasted_text`/`file_upload` documents
  populate both immediately since their content is already in hand;
  `url` documents populate both only once branch 3's worker fetches
  successfully). The dedup constraint is therefore a **partial unique
  index**, not a blanket one: `UniqueConstraint(fields=["workspace",
  "content_hash"], condition=Q(content_hash__isnull=False), name=
  "unique_knowledge_content_hash")` — Postgres unique indexes ignore NULL
  by this condition, so any number of pending `url` documents (still
  `content_hash=null`) can coexist in one workspace without colliding, and
  the uniqueness guarantee only kicks in once a hash actually exists.
- **Chunking happens synchronously in this branch, for every source type
  this branch supports (`pasted_text`, `file_upload`)** — resolving draft
  v1's branch-2/branch-3 contradiction in favor of "chunk immediately, at
  create time, in the request/response path" (deterministic, fast,
  no-network, so there is no reason to defer it to a background task).
  `KnowledgeChunk` rows are created in the same transaction as the document,
  with `embedding=null` — the document is created and chunked in `pending`
  status; only the *embedding* step is asynchronous (branch 3). `url` source
  type is deferred to branch 3 in full (not just its embedding step) because
  fetching it is itself the thing that needs to run in a background worker,
  not the request/response path (see §3).
- `KnowledgeChunk` (extends `WorkspaceScopedModel`, denormalizes `workspace`
  from its document — same pattern `MemoryExtractionJob` uses): `document`
  FK, `ordinal` (0-based position), `content`, `token_count`, `embedding`
  (nullable-by-construction `VectorField`, see §1's dimension policy),
  `embedding_provider` FK to `ai_core.ModelProvider`, **nullable** (a chunk
  is created with `embedding_provider=null, embedding=null` and stays that
  way permanently if its document is `private`, or until the branch-3
  worker successfully embeds it if `normal`). Unique constraint on
  `(document, ordinal)`.
- **Sensitivity drives a hard cloud-eligibility rule, enforced at the point
  embedding is *requested*, not only at the point it is *read* back**:
  embedding a private document's chunks would itself be the ADR-003-covered
  cloud call that leaks private content, so the branch-3 worker's very
  first step, before constructing any `texts` list or calling
  `gateway.embed()` at all, is `if document.sensitivity ==
  KnowledgeDocument.Sensitivity.PRIVATE: mark ready, chunks stay
  unembedded, return` — a private document never reaches the point where an
  embed call is even attempted. This closes draft v1's gap, where the
  guarantee only lived in the *retrieval*-side `cloud_eligible()` filter and
  said nothing about what the *ingestion* worker was allowed to send.
  `KnowledgeChunk.objects.cloud_eligible()` (mirrors `Memory.objects.
  cloud_eligible()`) remains the sanctioned read-side filter for retrieval:
  `document__sensitivity=normal, document__status=ready,
  embedding__isnull=False` — now a second, independent gate behind the
  ingestion-side one, not the only one.
- **Role policy matrix** (mirrors Memory's, simpler since there is no
  AI-suggested/approval axis):

  | action | owner/admin | member | viewer |
  |---|---|---|---|
  | read/list | ✓ | ✓ | ✓ |
  | upload | ✓ | ✓ | – |
  | delete | ✓ | own uploads | – |

  "Own uploads" is the exact predicate `created_by=actor`, same shape as
  Memory's member-authorization predicate.
- **Approval gate: explicitly out of scope here, by the same precedent MVP 2
  already established for user-created memories.** CLAUDE.md rule 4 ("no
  write/delete/deploy/external-send without the approval gate... agents can
  never modify their own definitions or permissions") targets *agent-
  initiated* actions; a human directly uploading a document through the API
  is the same category of action as a human directly creating a
  `user_created` memory in MVP 2, which is likewise born `approved` with no
  gate. Nothing in this MVP has an agent autonomously deciding to create or
  send a `KnowledgeDocument` — if a later MVP's agent runtime gains that
  ability, the gate applies there, not here.
- **Access auditing, via `record_audit()` service calls at each mutation
  site** — the same mechanism `memory`/`audit` already use; there is no
  Django model mixin for this in the codebase (`core.models.
  TimestampedModel` and `core.db.WorkspaceScopedModel` are the only base
  classes, and neither does auditing), so `knowledge`'s models follow the
  identical pattern rather than inventing one: mutation events
  (`knowledge.uploaded/deleted`, `knowledge.embedding_failed`) plus
  `knowledge.read` (list/detail: ids + count, no content) and
  `knowledge.context_accessed` (per gateway call: injected chunk ids,
  recorded even for empty retrieval results — MVP 2 branch 2's Codex review
  taught this lesson the hard way for memory; apply it here from the
  start).
- CRUD API for `pasted_text` and plain-text/markdown `file_upload` only in
  this branch (max file size enforced, e.g. 1 MB; MIME/extension allowlist
  `text/plain`, `text/markdown`, `.txt`, `.md`). No embedding dispatch yet —
  chunks are created unembedded; branch 3 wires the worker that embeds them,
  mirroring the MVP 2 branch-3/4 split (model + synchronous plumbing first,
  async dispatch second, so merged `main` never references an unregistered
  Celery task).

### 3. Ingestion worker — URL fetch + SSRF hardening, embedding dispatch

- `url` source type lands here, in full (fetch + chunk + embed), because the
  fetch itself must not run in the request/response path (a slow or hostile
  URL must not tie up a web worker) — chunking for `url`-sourced documents
  therefore also happens inside this Celery task, reusing the exact same
  chunking function branch 2 built for `pasted_text`/`file_upload` (one
  chunking implementation, called from two places: synchronously in the
  branch-2 view, and from this task after a successful fetch).
- Chunking (shared function, first used in branch 2): fixed-size character
  chunking with overlap (default 1000 chars / 150 overlap — settings-driven,
  not hardcoded) on paragraph/sentence boundaries where possible, hard
  character cap as a fallback. Deterministic and unit-testable without a
  model.
- Celery task, dispatch shape mirrors `memory`'s extraction worker exactly:
  `transaction.on_commit` after the creating transaction, non-propagating on
  broker-publication failure (document stays `pending`, recovery scan drains
  it), `CELERY_TASK_ALWAYS_EAGER` reused as-is (already environment-backed
  from MVP 2). Lease-fenced claim on `KnowledgeDocument.claim_token`/
  `lease_expires_at` (same `select_for_update(skip_locked=True)` + status/
  lease guard as `memory.jobs.claim_extraction_job`).
- **Fencing boundary, stated precisely (draft v1 overstated the memory
  precedent here — `memory/jobs.py` re-locks once per finalize call and
  writes its whole batch in one fenced transaction, not once per candidate;
  knowledge follows that same actual shape, not a per-chunk one)**: the
  worker (1) claims the document (lock, set token+lease, commit), (2) for
  `url` documents, fetches + chunks (network I/O, outside any transaction),
  (3) if `private`, jumps straight to the fenced finalize with no embed call
  (§2's ingestion-side gate); otherwise calls `gateway.embed()` once per
  chunk-batch (network I/O, outside any transaction, e.g. 100 chunks per
  call, provider request-size limits permitting), (4) re-locks the document
  inside one transaction, verifies `status=processing AND
  claim_token=<mine>` (mirrors `memory/jobs.py::_fenced`), and in that same
  transaction bulk-writes every chunk's `embedding`/`embedding_provider` and
  marks the document `ready`/`failed`. A stale worker that outlived its
  lease discards the whole batch at step 4, exactly like memory's fencing —
  `ModelUsage` rows from the wasted `embed()` calls stand as-is (the one
  intentional exception, same as memory).
- Redelivery of an already-`ready` document is a no-op (claim fails: status
  is not `processing`/expired-`processing`). Recovery scan
  (`recover_stale_knowledge_documents`, same periodic-task mechanism, added
  to the existing embedded-beat schedule alongside
  `memory-extraction-recovery-scan` — same single-worker-`-B` constraint
  comment applies) re-enqueues stale `pending` documents and abandoned
  `processing` documents whose lease expired.
- **`url` fetch SSRF hardening (hard requirement, not deferrable)** —
  fetching a user-supplied URL server-side is a classic SSRF vector.
  Mitigations, all mandatory, and specifically closing the TOCTOU gap Codex
  round 1 flagged in draft v1 ("resolve DNS, check the range, then let the
  HTTP client re-resolve and connect wherever" is not sufficient — the
  connection must be pinned to the exact address that was validated):
  - Scheme allowlist: `http`/`https` only.
  - Resolve the hostname to its IP address(es) once, reject any resolved
    address in loopback/link-local/private (RFC 1918)/multicast/
    cloud-metadata (`169.254.169.254`) ranges, then **connect directly to
    the validated IP** (not the hostname) while still sending the original
    `Host` header and correct SNI — concretely, via `urllib3`'s documented
    support for exactly this (`HTTPSConnectionPool`/`HTTPConnectionPool`
    with `assert_same_host=False` and `server_hostname=<original-host>`,
    connecting to the validated IP as the pool's actual host), not a vague
    "custom transport" (round 2 finding: draft v2 named no concrete,
    dependency-backed path). `urllib3` becomes a **new direct dependency**
    in `pyproject.toml` (it is currently only a transitive dependency via
    `requests`-less `openai`/`anthropic` SDKs, which is not something to
    build a security control on top of without declaring it explicitly).
  - Disable environment-configured proxies for this specific client
    (`trust_env=False` equivalent) so a proxy env var cannot redirect the
    request internally.
  - Redirects: capped (e.g. 3), and **each hop is fetched as a brand-new
    validated request** (re-resolve, re-validate, re-pin) rather than
    letting the HTTP client follow redirects natively.
  - `Content-Type` allowlist on the response (`text/plain`, `text/markdown`,
    `text/html` — HTML is stripped to text with a minimal, dependency-light
    tag-stripper, not rendered or executed).
  - Hard byte cap on the response body (streamed with an early abort, not
    read-then-checked) and a connect/read timeout, both reusing
    `AI_TIMEOUT_CONNECT`/`AI_TIMEOUT_READ`-style settings.
  - All of this lives in the ingestion worker (background job), not the
    request/response path.
  - Lesson from two review rounds (round 1: CGNAT 100.64.0.0/10, round 3:
    NAT64 `64:ff9b::/96`): Python's `ipaddress.is_global` is not a complete
    SSRF predicate on its own — address-translation encodings can report
    `is_global=True` while embedding a blocked destination, so each such
    encoding must be identified and its embedded address validated too
    (`knowledge/url_fetch.py::_is_blocked`).

### 4. RAG context injection (assistant integration)

- `build_context` (already takes `actor` since MVP 2) gains a knowledge
  retrieval step, structurally parallel to memory injection but a separate
  step (not merged into the same call) since the selection mechanism differs
  (vector similarity vs. curated approval):
  1. Call `InferenceGateway.embed(purpose="knowledge.retrieval", texts=[latest
     user message], ...)` directly — **no separate provider-picking step**.
     Draft v2 added one (`ModelProvider.objects.filter(...).first()`) that
     duplicated, and got wrong, the gateway's own eligibility logic
     (`_select_providers()` also checks `AI_FAKE_PROVIDER` and per-provider
     env-key presence, not just `is_active`/priority — round 2's finding).
     `embed()` already resolves this correctly internally; the retrieval
     code only needs its result.
  2. **Wrap this call in `try/except GatewayError`** (round 3 finding: the
     gateway's real contract, mirrored from `complete()`, is to *raise*
     `NoProviderAvailable` — a `GatewayError` subclass — when no eligible
     provider exists, not to return a result with a null provider; the same
     applies to any other embedding failure, e.g. every provider's attempts
     exhausted). On any `GatewayError`, retrieval treats the turn as **zero
     knowledge chunks** — record `knowledge.context_accessed` with an empty
     id list (same "record even when empty" discipline as memory) and
     proceed with the chat reply using memory context alone. A knowledge-
     retrieval failure must never fail the whole chat turn, exactly as a
     broker-publication failure never fails an SSE reply in MVP 2 §4 — the
     same non-propagating philosophy, applied to a different failure mode.
  3. On success, the returned `usage.provider_id` (from the `ModelUsage` row
     `embed()` just wrote) tells us exactly which provider produced the
     query vector — read it off the gateway's own result, never re-derived.
  4. Query `KnowledgeChunk.objects.for_actor(actor, workspace=conversation.
     workspace).cloud_eligible()` — **`for_actor()` first, exactly like
     `Memory.objects.for_actor(actor, workspace=workspace).cloud_eligible()`
     in `memory/context.py`**; draft v1 omitted this and would have leaked
     the same over-broad-queryset risk MVP 1/2's workspace-scoping rule
     exists to prevent.
  5. **Filter further to `embedding_provider_id = usage.provider_id`** (from
     step 3) before ordering by `embedding <=> query_vector` — this is what
     makes the variable-dimension column from §1 safe: a cross-dimension
     comparison is filtered out before the operator ever runs, not caught by
     it.
  6. Top-K (capped count + character budget, same dual-cap discipline as
     memory). **No personal overlay in company workspaces** (identical
     reasoning to memory: company conversations are visible to all
     members).
- Same **injection-safety framing** as memory: chunks serialized as
  delimited, length-bounded data blocks; system prompt states they are
  factual reference material, not instructions; content escaped/fenced using
  the same neutralization helper memory already built (reused, not
  reimplemented — `_neutralize()` must still run last, after all other
  normalization, per MVP 2 branch 2's hard-won lesson).
- **Sanitized `chunks_used` contract** (mirrors `memories_used` exactly):
  injected chunk ids persisted on the assistant `Message` row at write time
  as an immutable, ordered UUID list (default `[]`), exposed in the message
  serializer and the SSE `done` event. Labelled in the UI as context
  supplied, not proof of use — same semantics as memory context.
- `knowledge.context_accessed` audited on every call, including empty
  retrieval (no chunks matched, no embedding-eligible chunks exist yet, or
  no active embedding provider at all).
- **Private negative contract, restated for knowledge, now watertight on
  both sides**: ingestion (§2/§3) never embeds a private document's chunks
  in the first place; retrieval (§4) only ever reads through
  `for_actor().cloud_eligible()`. A private document's content therefore
  cannot enter a gateway payload, `context_report`, `chunks_used`, or audit
  metadata via either path. Tests capture the exact gateway payload at both
  the embedding call site and the retrieval call site to prove this, same
  discipline as memory's private contract test.

### 5. Frontend Knowledge UI

- Knowledge page in the shell: upload form (paste text / plain-text or
  markdown file / URL), document list with status (pending/processing/ready/
  failed) and sensitivity, delete. BFF routes follow the established pattern
  (HttpOnly cookies, CSRF origin + content-type checks, no token in JS,
  dynamic id segments validated as UUIDs before interpolation — MVP 2 branch
  5 found this exact gap for memory; guard it from the start here).
- Chat: a "Knowledge context: N chunks" indicator alongside the existing
  memory-context indicator, driven by the sanitized `chunks_used` field.

### 6. E2E + docs

- Playwright smoke extension: upload a short pasted-text document, wait for
  `ready` status, send a chat message whose content the document actually
  answers, assert the knowledge-context indicator appears. Relies on §1's
  bag-of-words `FakeAdapter.embed()` (built in branch 1, not retrofitted
  here) genuinely ranking the seeded chunk above unrelated ones via shared
  vocabulary.
- Docs: `MASTER_PLAN.md` gets a Knowledge/RAG section (letter placement
  decided when this lands — F is "Memory & Skills", not Knowledge; likely a
  new lettered section), MVP 2 status entry (currently missing an
  `M`/`N`-style lettered section entirely), MVP 3 status entry, Open
  Decisions resolution for "exact embedding model choice" (resolved: OpenAI
  `text-embedding-3-small` default, provider-agnostic via `ModelProvider.
  capability`).

## Out of scope (explicit)

- **Obsidian vault ingestion** (vault → Jarvis) — explicitly deferred past
  this MVP per the 2026-07-07 scope decision. When it lands, it should be a
  *source adapter* onto this same ingestion pipeline (chunking/embedding/
  retrieval unchanged), plus the echo-chamber guard (`source: jarvis_export`
  files never re-ingested) from `docs/memory/obsidian-vault-strategy.md`.
- **Rich document parsing** (PDF, docx, pptx, etc.) — this MVP accepts plain
  text/markdown and text-like URL responses only. Binary document parsing is
  a real dependency + complexity addition (a PDF text-extraction library at
  minimum) and is deferred to a later MVP.
- **Local/self-hosted embedding models** — the architecture is
  provider-agnostic (any `ModelProvider` row with `capability="embedding"`
  works), but no local model service ships in this MVP. This is also why
  private documents are simply never embedded rather than embedded locally:
  there is no local path yet.
- **Semantic memory dedup** (upgrading `Memory.fingerprint` from textual to
  embedding-based) — MVP 2 flagged this as MVP 3 work in its "out of scope"
  list, but it is not required for the knowledge/RAG goal itself and would
  entangle two apps' migrations in one branch. Tracked as a fast-follow, not
  bundled here.
- **Cross-document / cross-chunk deduplication** beyond the document-level
  `content_hash` guard — no chunk-level near-duplicate detection.
- **Re-embedding on provider change** — if the active embedding provider
  changes, existing chunks keep their original provider's vectors
  (`embedding_provider` FK lets retrieval filter to one dimension/provider
  family, per §4); a bulk re-embed migration path is not built in this MVP.
  A provider swap simply makes older chunks invisible to retrieval until
  re-embedded by some future tool — acceptable for this MVP, called out
  here so it is not mistaken for a bug later.
- **Company-workspace knowledge overlay nuances beyond what memory already
  established** — same "no personal overlay in company workspaces" rule,
  no new cross-workspace sharing model.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp3-embedding-gateway` — **done (2026-07-07)**. Codex review: round 1 CHANGES REQUESTED (stale embedding dimension metadata), round 2 APPROVE. | `ModelProvider.capability`/`dimension` fields (migration backfills existing rows to `chat`), `InferenceGateway.embed()` (writes `ModelUsage`, no new usage table), `BaseAdapter.embed()` + OpenAI-compatible + Fake implementations, `seed_ai_defaults` extension, `pgvector` dependency, SDK guard test |
| 2 | `feat/mvp3-knowledge-foundation` — **done (2026-07-07)**. Codex review: 8 rounds (upload cap, chunk-boundary, MIME spoofing, chunked-transfer, JSON-escaping and NUL-byte bugs — all fixed), round 8 APPROVE. | `knowledge` kernel app, `KnowledgeDocument`/`KnowledgeChunk` models + pgvector extension migration, synchronous chunking for `pasted_text`/`file_upload`, role matrix, access-audit events, approval-gate exemption documented, permission tests (no embedding dispatch yet — chunks created unembedded) |
| 3 | `feat/mvp3-ingestion-worker` — **done (2026-07-08)**. Codex review: 10 rounds. Round 3: **P1 security finding** — NAT64 (`64:ff9b::/96`) addresses return `is_global=True` in Python regardless of the embedded IPv4 (including 169.254.169.254 cloud metadata); `_is_blocked` now unpacks and separately validates the embedded address (same root cause as round 1's CGNAT bypass — `is_global` alone is not sufficient, address-translation encodings need their own check). Rounds 6+9 repeated a credential-leak pattern (URL-embedded `user:token@host` and `#fragment` — the fetcher never reads either, but the serializer stored them, leaking to workspace readers; both rejected). Rounds 3/5/7/8 all touched the same time-budget arithmetic (`_SOFT_TIME_LIMIT`/`_GATEWAY_WORST_CASE_SECONDS`/`_FETCH_WORST_CASE_SECONDS`) as each fix exposed a missing component (backoff sleep, streaming deadline); round 2's fail-fast `assert` caught every regression in that chain. Round 10 APPROVE. | Shared chunking service reused for `url`, Celery dispatch (`on_commit`, non-propagating), lease-fenced claim/finalize embedding task (ingestion-side private-document gate before any `embed()` call), recovery scan, `url` source type + mandatory SSRF hardening (`urllib3`-based pinned-IP connect, redirect re-validation, content-type/size limits) |
| 4 | `feat/mvp3-knowledge-context` — **done (2026-07-08)**. Codex CLI hit its usage-limit mid-round-3; while waiting for the reset, 5 independent Sonnet review passes ran instead (each a different, non-overlapping angle) and found 2 real bugs: a vector-dimension crash (`embedding_provider_id` is not a safe dimension proxy — a `ModelProvider` row's `dimension`/`model_id` are ordinary editable fields; fixed with a `vector_dims()` WHERE-clause guard) and a zero-width-Unicode bypass of the shared fence neutralizer (pre-existing in memory's `_neutralize`, newly shared via this branch's `neutralize_fence()` extraction; fixed with a `strip_invisible_formatting()` pass run before the neutralizer). Once Codex's limit reset, round 3 found one more gap (`KnowledgeChunk.workspace` is denormalized from `document.workspace` with no DB constraint keeping them in sync; fixed with a `document__workspace=` guard), round 4 APPROVE. | RAG retrieval (`for_actor().cloud_eligible()`, provider-matched dimension filtering, pgvector cosine similarity, injection-safe framing reusing memory's neutralizer), `chunks_used` persistence + SSE/serializer contract, `knowledge.context_accessed` audit, private negative contract tests (both ingestion-side and retrieval-side) |
| 5 | `feat/mvp3-knowledge-ui` — **done (2026-07-08)**. Codex review: round 1 CHANGES REQUESTED (the file-upload BFF proxy called `request.formData()` with no size check, so an oversized multipart body was fully parsed/buffered before the backend's own upload-size guard ever ran — fixed by counting real bytes off a `request.clone().body` reader first), round 2 CHANGES REQUESTED (that cap was hardcoded, so raising the backend's configurable limit would start rejecting valid uploads — moved into `env.ts` as `KNOWLEDGE_MAX_UPLOAD_BYTES`), round 3 APPROVE. | Next.js Knowledge page, BFF routes (UUID-validated dynamic segments from the start), upload/list/delete, chat knowledge-context indicator, Vitest coverage |
| 6 | `feat/mvp3-e2e-docs` — **done (2026-07-08)**. Codex review: round 1 CHANGES REQUESTED (the reload-poll loop for `ready` status started before the create POST had resolved/rendered, so a slow create response could have its in-flight fetch cancelled by the reload; the added knowledge-path waits could also push the whole test past the pre-existing 60s `test.setTimeout` once stacked after the memory path's own waits — fixed by waiting for the created card to render before the first reload, and raising the budget to 120s), round 2 APPROVE. | Playwright smoke extension (upload → ready → retrieved in chat, with a similarity-verified fake-embedding fixture), MASTER_PLAN Knowledge/RAG section + MVP 2/3 status entries, Open Decisions resolution, brief status updates |

Config enumerations to touch: branch 1 — `pyproject.toml` dependency
(`pgvector`), `ModelProvider` admin fields; branch 2 — installed apps, URLs,
pytest paths, import-linter roots/contracts (`knowledge` is a kernel app: may
import `core`/`audit`/`ai_core`, never `apps.modules.*`; one-way `assistant ->
knowledge`, mirroring the existing `assistant -> memory` direction); branch 3
— `pyproject.toml` dependency (`urllib3`, direct — see §3's SSRF design),
chunking-size settings, embedded-beat schedule entry, compose worker command
comment update, any new SSRF-related settings in `.env.example`.

Per-branch flow unchanged from MVP 1/2: implement → tests+ruff+CI green →
Codex CLI review (`--base main`) → fix → APPROVE → merge to main (no extra
user confirmation per the 2026-07-05 protocol).

## Test strategy

- pytest: `for_actor` leakage matrix, `KnowledgeDocument` status-transition
  tests (`pending -> processing -> ready | failed`, redelivery no-op), role ×
  endpoint permission matrix incl. the member `created_by=actor` predicate,
  server-controlled serializer fields, access-audit events (read/context/
  upload/delete, no content in metadata), embedding gateway retry/fallback
  tests mirroring the existing chat gateway suite (timeout/5xx/rate-limit ->
  retryable; auth/permanent-4xx -> non-retryable; `capability="embedding"`
  filtering never selects a chat-only provider or vice versa), `ModelUsage`
  written on every gateway-invoking attempt including embedding calls
  (`output_tokens=0`, `item_count` in `context_report`), **ingestion-side
  private gate test**: a private document's worker run makes zero
  `gateway.embed()` calls and leaves every chunk `embedding=null` — captured
  via a mock/spy on the adapter, not just an assertion on the end state,
  **retrieval-side private gate test**: even if a private chunk somehow had
  a non-null embedding (defense in depth), `for_actor().cloud_eligible()`
  still excludes it, dimension-mismatch guard (a query must filter to one
  `embedding_provider` before ordering, never compare mismatched-dimension
  vectors), two-connection concurrency test for lease-fenced claim/takeover
  (mirroring `MemoryExtractionJob`'s pattern — a stale worker finishing last
  discards its whole batch), SSRF negative tests (loopback/private-IP/
  link-local/metadata-IP rejected at initial resolution AND at each redirect
  hop; a redirect to a private IP after an initially-valid public IP is
  rejected; oversized body rejected; disallowed content-type rejected) —
  these are security tests, not incidental coverage, and are
  review-blocking if missing, chunking boundary tests (empty document,
  single-chunk document, exact-multiple-of-chunk-size document),
  content_hash dedup, `chunks_used` persistence + SSE `done` field,
  eager-mode negative pair (arbitrary task exception propagates; broker
  publication error is swallowed) reused from the existing Celery test
  pattern, **fake-embedding rank assertion** (branch 1): the exact seed/
  query/noise strings branch 6's e2e uses genuinely rank the seed document
  above noise under cosine similarity — proven here at unit-test speed, not
  only implicitly by the e2e test passing, **retrieval no-provider
  resilience test**: `GatewayError`/`NoProviderAvailable` from the
  embedding call is caught, `knowledge.context_accessed` is still recorded
  (empty), and the chat turn completes normally using memory context alone.
- Vitest/RTL: knowledge page states, upload (all three source types) +
  delete flows, BFF CSRF/cookie/UUID-validation contracts, knowledge-context
  indicator (stream + reload).
- Playwright: the curated happy path (scope 6) — upload, wait for ready,
  retrieval appears in the next chat turn, with a fake-embedding fixture
  verified to actually rank the seeded chunk first. No real-LLM/real-
  embedding tests outside nightly.

## Risks / notes

- Branches 2 and 3 split the same way MVP 2 split branches 3/4 (memory
  extraction jobs vs. worker): land the model + synchronous chunking first,
  wire the async embedding dispatch second, so `main` never sits with an
  unregistered Celery task at any point. Unlike MVP 2, chunking itself is
  synchronous from branch 2 onward (for the source types that don't need a
  worker) — only *embedding* and *URL fetching* are asynchronous.
- The SSRF hardening in branch 3 is the highest-risk piece of this MVP by a
  wide margin — it is the first place Jarvis makes a server-side network
  request to a user-supplied, non-provider destination. Treat it with the
  same scrutiny as an auth boundary, not as an implementation detail; the
  pinned-IP-connect + per-redirect-hop-revalidation design in §3 exists
  specifically because "validate then let the client re-resolve" is a real
  bypass (Codex round 1 caught this in draft v1).
- Reusing `ModelProvider`/`ModelUsage` for embeddings (§1) is a schema change
  to two MVP 1/2 models, not a purely additive one. The `capability`
  backfill migration must default every existing row to `"chat"` and this
  needs an explicit test asserting existing chat call behavior is
  byte-for-byte unchanged after the migration (provider selection, pricing,
  admin listing) — this is the one place in this MVP where a mistake could
  regress an already-shipped MVP 2 feature, not just fail to add a new one.
- `KnowledgeChunk.embedding`'s unconstrained-dimension column (§1) trades a
  schema-level guarantee for an application-level one (the
  `embedding_provider`-filter-before-comparison rule in §4). This is a
  deliberate, documented trade-off, not an oversight — but it means every
  new retrieval code path added in future MVPs must remember the filter.
  Worth a code comment at the exact query site pointing back here, not just
  this brief.
