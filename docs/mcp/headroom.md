# Headroom (`compressor` sidecar) — governance manifest

Phase 8B branch 6 (`feat/p8-headroom-stage`, brief
`docs/prompts/phase-8b-transcripts-graph/brief.md` D11, R4). Headroom is not
an MCP connector and has no `backend/connectors/manifests/` entry: it is a
Python library that runs **only inside its own sidecar container**, behind a
one-endpoint stdlib HTTP server. Friday's gateway calls it as a local,
model-free transform. This file follows `_template.md` where its sections
apply.

## Summary
- **What:** `headroom.compress()` over prior **assistant** messages of a chat
  payload, before the provider call (`backend/ai_core/compression.py`, called
  in `InferenceGateway.complete`, sync and stream paths).
- **Where it runs:** the `compressor` compose service (profile
  `compression`), image `deploy/compressor/Dockerfile`, server
  `deploy/compressor/server.py`. Never in web or worker: `backend/` never
  imports `headroom` (`ai_core/tests/test_no_headroom_import.py`).
- **Default:** off. `AI_COMPRESSION_ENABLED=false`,
  `AI_COMPRESSION_PURPOSES` empty. A purpose is enabled only with a recorded
  `memory_eval compare --compression <purpose>` result that is not
  `inconclusive`, with at most 1 point accuracy loss and zero extra leaks.
- **Hard bypass:** `COMPRESSION_BYPASS` (code constant) — approval and gate
  rendering (`harness.briefing`, `harness.diff_summary`), tool-call and
  argument prompts (`router.*`, `agent.plan`, `agent.skill_match`), reviews
  (`harness.review_summary`), the learning prompts (`memory.extraction`,
  `memory.episode_extraction`, `memory.consolidation`,
  `memory.fact_reconciliation`, `memory.reflection`), `memory.eval_answer`
  and `ops.preflight`. Bypass wins over the setting. Eligible today: `chat`,
  `facade.ephemeral`, `agent.read_step`; only `chat` carries multi-turn
  assistant history.
- **Status:** allowed (sidecar only), see `docs/mcp/allowlist.md`.

## Source and pin
- Upstream: `github.com/headroomlabs-ai/headroom` (the old
  `chopratejas/headroom` link redirects there), Apache-2.0, Python ≥ 3.11.
- **Pin: `headroom-ai==0.43.1`**, uploaded 2026-11-05, wheel
  `headroom_ai-0.43.1-cp310-abi3-manylinux_2_28_x86_64.whl`, sha256
  `2e9cd1a9aab42f60b00a9f7c8a85cf82815b51b1989179e5fdd40ddd09915d6e`
  (re-verified 2026-11-21 by downloading the wheel). 0.44.0 (2026-11-10) was
  under 14 days old.
- `deploy/compressor/requirements.lock.txt` hash-locks the complete install
  set (CPython 3.12, manylinux x86_64): `headroom-ai`, `tiktoken`, `regex`,
  `requests`, `urllib3`, `idna`, `certifi`, `charset-normalizer`, `pydantic`,
  `pydantic-core`, `annotated-types`, `typing-extensions`,
  `typing-inspection`, `opentelemetry-api` — every release uploaded before
  2026-11-07. Installed `--require-hashes --no-deps --only-binary=:all:`.
- **Excluded on purpose:** `litellm` (a second-door LLM library, ADR-003),
  and the other declared base requirements `compress()` does not import
  (`click`, `rich`, `ast-grep-cli`, `pyyaml`, `tomlkit`, `truststore`,
  `hf-xet`, `filelock`). `deploy/compressor/test_no_litellm.py` runs on every
  image build with an import blocker for `litellm`, `openai` and `anthropic`
  and compresses a fixture; the build fails otherwise. Verified 2026-11-21 in
  a scratch venv holding exactly the lock: both sidecar test files pass.
- If a future version cannot compress without `litellm`, Headroom is not
  upgraded (or not adopted): the flag stays off. `litellm` is never added.
- Upgrades only in a dedicated, reviewed branch: re-resolve, re-hash, re-run
  both sidecar test files, update this file.

## Tools
- One HTTP endpoint, `POST /compress`, body
  `{"model", "messages": [{"role": "assistant", "content"}]}`. Any other
  path (404), method (405), a body over 1 MB (413), extra keys, another role
  or a non-string content (400) is refused (`deploy/compressor/test_server.py`).
- `GET /healthz` (Phase 8B closeout, 2026-11-20): `{"status": "ok",
  "telemetry": "off"|"on", "offline": bool}`, computed from the process
  environment only (no Headroom call, no input). `manage.py preflight`
  (`compressor`) reads it while `AI_COMPRESSION_ENABLED` is on and fails on
  telemetry on or not offline.
- No proxy mode, no `wrap`, no MCP server, no CLI, no Kompress model
  (`kompress_model="disabled"`).

## Credentials
- None. The service gets no env file and no provider key; it makes no model
  call.

## Sandbox and network
- Compose service `compressor`: `read_only: true`, `cap_drop: [ALL]`,
  `no-new-privileges`, `/tmp` tmpfs, non-root user 10002, **no published
  port**, only on the `compression` network (`internal: true`, shared with
  web and worker only). No route out.
- Telemetry off and offline: `HEADROOM_OFFLINE=1`, `HEADROOM_TELEMETRY=off`,
  `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`, `HF_HUB_OFFLINE=1` (image env and
  re-set by `server.py` before the import). Upstream's anonymous beacon is on
  by default; these disable it, and the network has no egress anyway.
- `tiktoken` encodings (`o200k_base`, `cl100k_base`) are baked in at build
  (`TIKTOKEN_CACHE_DIR`), so no download is attempted at run time.
- The backend client (`CompressorClient`) disables environment proxies and
  has a 300 ms default timeout (`AI_COMPRESSION_TIMEOUT_MS`).

## Outbound data
- What reaches the sidecar: only prior `assistant` messages of the payload —
  never the system message (core blocks, `self`, the memory block, the
  knowledge block, fenced `<connector_result>` reads), never a `user`
  message, never the last message
  (`backend/assistant/tests/test_compression_payload.py` asserts it on a real
  `build_context` output). Friday's payload has no tool-role message, so
  tool output is out of reach by construction.
- `personal_data` calls are compressed too (the sidecar is local, with no
  egress); the context report records `compression.personal_data`.
- Nothing leaves the sidecar except the HTTP answer to its caller.

## Prompt-injection surface and output handling
- Input is Friday's own earlier assistant text. Output is validated by the
  gateway stage: the same number of messages, role `assistant`, string
  content, never longer than the original (longer → original kept); counts
  must be non-negative integers. Anything else is an `invalid_response`
  error and the original messages are sent.
- Fail open: on timeout, connection error, 4xx/5xx or a malformed answer the
  original messages are sent and the error is recorded. The stage changes
  cost, never correctness.

## Logging
- The server logs method and path only, never a body. The gateway logs the
  error code only. The call's single `ModelUsage` row carries
  `context_report.compression = {applied, pre_tokens, post_tokens,
  transforms, lossless, error, sent_messages?, skipped?, personal_data?}` —
  counts and transform names, never content. The sidecar writes no
  `ModelUsage` of its own (no model call).

## Observed behaviour (2026-11-21, scratch venv, the locked set)
- With assistant-only input and `protect_recent=0`, `compress()` 0.43.1
  routed the fixture as `router:noop` (no token change): upstream keeps
  assistant text uncompressed by default (`compress_assistant_text_blocks`
  is off for provider prefix-cache safety). So the owner's
  `memory_eval compare --compression chat` may well come out
  `inconclusive`, which keeps `chat` off — the non-vacuity rule exists for
  exactly this case. Enabling assistant-text compression would be an
  upstream-config change in a reviewed branch, re-measured by the eval.

## Review
- Phase 8B branch 6, 2026-11-21: pin and hashes verified, lock installed
  and both sidecar test files run, gateway stage default off with bypass set
  and purpose-classification test, payload test on a real `build_context`
  output, eval arm with an `inconclusive` rule. Awaits the Sonnet 5 review.
