# Friday landscape research (2026-11-20)

Scope: what exists on GitHub and in products (as of 2026-11-20) for each capability the owner asked for: a voice-first personal assistant, PC/terminal control, driving Claude Code / Codex / Antigravity, continuously-learning memory, multi-provider (incl. OpenRouter voice), and the repos the owner named (Ruflo, Headroom, Graphify, OpenJarvis, n8n Nod).

Verdict legend:
- **Adopt**: take as a pinned dependency.
- **Integrate**: call it as an external tool or service through Friday's governed boundary (MCP, CLI or harness).
- **Copy pattern**: re-implement the idea inside Friday's own Django/Next.js code.
- **Avoid**: neither a dependency nor a reference worth time.

The bar is the same everywhere: anything that makes its own LLM calls, keeps its own memory as the source of truth, or carries its own approval system collides with ADR-003 (gateway single door + ModelUsage), ADR-004/008 (approval gate), ADR-001 (workspace scoping) and ADR-005 (Postgres is truth). So most of the big projects below are **copy pattern**, not adopt.

---

## 1. Personal-agent runtimes (the "OpenClaw / Hermes" class)

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw) | Node/TS self-hosted assistant. One always-on Gateway (WS, 127.0.0.1:18789) plus paired "nodes" (macOS/iOS/Android/Win/Linux apps) that declare capabilities and run `system.run`, screen and camera. 20+ chat channels, ClawHub skills, a global wake-word list routed to agents, a Talk loop (700–900 ms silence, barge-in), Canvas/A2UI, cron/heartbeat, markdown memory with "dreaming". | ~395k stars, MIT | **Copy pattern** (node protocol, wake routing, Talk params, A2UI-to-HUD). **Avoid** ClawHub and embedding: ClawHavoc malicious skills (counts vary by source: 341/2,857 per Koi; 335, 824+ and 1,184 elsewhere), a one-click RCE through cross-site WebSocket hijacking reported as CVE-2026-25253 (CVE labelling is inconsistent across sources; the `exec.approvals.set` detail is unverified), many exposed gateways. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research) | Python agent-first runtime. Tiny capped core memory (MEMORY.md ~2.2k chars, USER.md ~1.4k chars) plus unlimited FTS5 transcript search; self-written SKILL.md skills with staged write-approval; curator (stale, then archive, never delete, audit ledger); cron with `no_agent`/`wakeAgent:false` script gates; 25+ platform gateway; voice with openWakeWord/sherpa/Porcupine, streaming TTS, barge-in; bundled skills that drive `claude -p --output-format stream-json`, `--resume`, tmux `capture-pane`. | ~265k stars, MIT | **Copy pattern**: core-memory caps, skill write-approval + curator, cron safety, the Claude/Codex driving recipe. Do not adopt its "smart" approval mode (an LLM auto-approves). |
| [NanoClaw](https://github.com/nanocoai/nanoclaw) | Minimal: a Node router plus one container per agent group; credential proxy outside the container; harness selectable per agent (Claude Code, Codex, OpenCode, Cursor, Ollama); single-writer SQLite inbox/outbox. | ~31k, MIT | **Copy pattern** for the harness: credential proxy, mount allowlist, harness-adapter abstraction. |
| [IronClaw](https://github.com/nearai/ironclaw) | Rust "Agent OS": WASM tools with capability permissions, host-boundary secret injection with leak scanning, Block/Warn/Review/Sanitize injection policies, RRF hybrid memory. | ~12.7k, Apache-2.0 | **Copy pattern**: RRF hybrid retrieval, untrusted-output wrapper, secret-leak scanner. |
| [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) | Rust single binary; "supervised" autonomy by default (medium risk approved, high risk blocked); a cryptographic receipt per tool call; SOP engine. | ~33.5k, Apache-2.0 | **Copy pattern**: hash-chained audit receipts, risk-tiered defaults. |
| [Moltis](https://github.com/moltis-org/moltis) | Rust agent server with a multi-provider STT/TTS layer, Apple Container sandbox, BeforeToolCall guard. | ~3k, MIT | Copy pattern (voice provider port). |
| [nanobot](https://github.com/HKUDS/nanobot), PicoClaw, NullClaw | Small reimplementations (Python / boards / Zig). | 50k / 30k / 8k, MIT | Reference reading only. |
| [Agent Zero](https://github.com/agent0ai/agent-zero) | An agent that owns a whole Linux desktop inside a container. | MIT | Copy the isolation idea for open-ended GUI tasks. |
| JARVIS front-ends for OpenClaw: JarvisClaw, [openclaw-jarvis-ui](https://github.com/jincocodev/openclaw-jarvis-ui), Jarvis Horizon, Jarvice, clawd-voice | Three.js orb HUDs plus wake word, usually openWakeWord + faster-whisper. | tens of stars, mixed / no licence | Copy the UX only; do not copy unlicensed code. |

**Key ideas**: a node/capability protocol with pairing is the right shape for PC control. Server-owned wake words can be routed per agent. Memory = small pinned core + searchable episodes + scheduled consolidation. Self-written skills are fine *only* behind staged approval. Cron jobs should be script-gated so they wake the LLM only when there is work. OpenClaw's CVE history is the strongest argument for Friday's existing rules.

## 2. n8n and Nod

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [Nod](https://github.com/n8n-io/nod) ("n8n nod") | Standalone personal agent from n8n Labs, launched 2026-11-10 as a research preview (v0.9.0 on 11-19). **Not** the n8n AI Assistant and does not need n8n. Node 24 + Fastify + Postgres/pg-boss, a sandbox container and a Chromium container, Pi agent loop, Composio/MCP/HTTP/CLI apps, Electron/Telegram/WhatsApp clients, 20+ providers including OpenRouter. A server-side **trust check** (a separate model sees only the owner's words and the action arguments, never tool output). Memory notes are written only from the owner's own messages, with provenance, and archived rather than deleted. Proactivity observer with cheap triage; cost cap. Voice is voice notes only: **no wake word, no live duplex, no HUD**. | ~2.7k, Apache-2.0 | **Copy pattern**: trust check, memory provenance, vault/key isolation, change-only watches, opt-in computer use. Optionally run it side by side; it exposes no API for Friday to drive. |
| [n8n instance-level MCP](https://docs.n8n.io/connect/connect-to-n8n-mcp-server) | `<n8n>/mcp-server/http`, OAuth; ~60 tools (search/get/execute/publish workflows, executions, builder, agents incl. `list_agent_sessions`). Workflows are opted in one by one, but not per client. | n8n ~211k, Sustainable Use (fair-code) | **Integrate** via Friday's MCP framework: reads ungated; execute/publish/create/update/call_agent gated; a Friday-side workflow-ID allowlist. |
| n8n MCP Server Trigger node | One workflow becomes a narrow MCP server. | n8n core | **Integrate**: preferred least-privilege route. |
| n8n Agents / Agent Harness | First-class agents inside n8n (preview). | n8n core | Integrate reads ("what are my automations doing"). |
| [czlonkowski/n8n-mcp](https://github.com/czlonkowski/n8n-mcp) | Community n8n MCP. | ~23k, MIT | Avoid; native MCP is preferred. Fallback only. |
| [Pi](https://github.com/earendil-works/pi) | TS agent toolkit under Nod. | ~123k, MIT | Avoid as dependency; copy tool-search meta-tool and "codemode". |
| [agent-browser](https://github.com/vercel-labs/agent-browser) / agent-desktop | Browser CLI / accessibility-tree desktop control. | ~53k Apache-2.0 / unverified | Integrate later as a governed tool (desktop: on the Mac node). |
| [n8n-io/skills](https://github.com/n8n-io/skills) | SKILL.md guides for building n8n workflows. | ~0.6k, Apache-2.0 | Mount into harness sessions when building workflows. |
| ElevenLabs Agents / Vapi + n8n webhooks | Hosted voice agent calling n8n. | commercial | Avoid as core loop; lesson: slow tools must be async in voice turns. |

**Key ideas**: n8n is a *tool family* over MCP, not a brain. Workflows take seconds, so in a voice turn Friday should say "started, I'll tell you" and announce the result later. Nod's trust check is a cheap second safety layer *before* the human approval.

## 3. Voice pipeline and HUD

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [Pipecat](https://github.com/pipecat-ai/pipecat) + [smart-turn v3](https://github.com/pipecat-ai/smart-turn) + [voice-ui-kit](https://github.com/pipecat-ai/voice-ui-kit) | Python realtime voice framework (frame pipelines, SmallWebRTC P2P, Silero VAD, interruption/barge-in, local MLX Whisper/Kokoro/Piper, cloud S2S services). smart-turn v3: ~8 MB audio end-of-turn model, 23 languages incl. Turkish, ~12 ms on CPU. | 17.3k / 1.7k / 0.4k, BSD-2 | **Adopt** as the host-side voice runtime, with its LLM step as a Friday-gateway adapter. Pin the version (0.0.x churn). |
| [kwindla/macos-local-voice-agents](https://github.com/kwindla/macos-local-voice-agents) | Fully local Mac reference: Silero + smart-turn + MLX Whisper + local LLM + Kokoro, <800 ms claimed. | ~350, BSD | Reference build to start from. |
| [LiveKit Agents](https://github.com/livekit/agents) | Voice agent framework on a LiveKit SFU; native MCP; judge-based tests; turn detector incl. Turkish. | 15k (server 22k), Apache-2.0 (model: custom) | Phase-2 option for multi-device/phone; copy judge tests now. |
| [openWakeWord](https://github.com/dscripka/openWakeWord) | Wake-word framework; ships "hey jarvis"; custom "hey friday" needs training (community Colabs). | ~3k, Apache-2.0 code (pretrained models reportedly non-commercial: confirm from LICENSE/model cards before P6 branch 4) | **Adopt** (already chosen in Phase 6 D6); train our own "hey friday" model. Porcupine only as an optional owner-keyed fallback. |
| [bertrandmbanwi/Jarvis](https://github.com/bertrandmbanwi/Jarvis) | Closest end-to-end match: macOS, "Hey JARVIS", Next.js + Three.js particle orb, Swift WKWebView overlay, delegates coding to Claude Code/Codex, approval for high-risk tools. | ~45, MIT | Copy pattern (orb state protocol, native overlay hosting a web orb). |
| [isair/jarvis](https://github.com/isair/jarvis) | Local assistant addressed by name mid-sentence over a rolling buffer; Memory Viewer; redaction. | ~2.4k, non-OSI | Ideas only. |
| [Kyutai Moshi/Unmute](https://github.com/kyutai-labs/unmute) | Full-duplex S2S; streaming TTS. | 11.6k / 1.6k | Avoid: CUDA only, no Mac, no Turkish. |
| [RealtimeVoiceChat](https://github.com/KoljaB/RealtimeVoiceChat) / RealtimeSTT / RealtimeTTS | Readable barge-in and sentence-chunked TTS reference. | ~3.9k, Apache-2.0 | Copy pattern; RealtimeSTT is a prototyping fallback. |
| [huggingface/speech-to-speech](https://github.com/huggingface/speech-to-speech) | Cascaded pipeline with MLX paths. | ~13.8k, Apache-2.0 | Use as a benchmark harness for STT/TTS on the Mac. |
| STT: MLX Whisper large-v3-turbo, [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), WhisperLiveKit, parakeet-mlx, Moonshine, FluidAudio | Turkish + English: Whisper family is the safe choice. Parakeet and probably Moonshine lack Turkish. | MIT / Apache mostly | **Adopt** MLX Whisper (Mac GPU) as primary; whisper.cpp fallback; faster-whisper in Docker/CI. |
| TTS: [Piper](https://github.com/OHF-Voice/piper1-gpl) (tr_TR voices), [Kokoro](https://github.com/hexgrad/kokoro) (no Turkish), [Chatterbox Multilingual](https://github.com/resemble-ai/chatterbox) (Turkish, cloning), mlx-audio, XTTS | | Piper now GPL-3.0; Kokoro Apache; Chatterbox MIT; XTTS weights non-commercial | **Adopt** behind a TTS port: Kokoro for EN, Piper for TR (separate executable over stdio/HTTP, never imported or linked, not bundled in the signed node, because of GPL-3.0), benchmark Chatterbox for TR quality. Avoid XTTS. |
| OpenAI Realtime / Gemini Live | Cloud full-duplex S2S with function calling, Turkish. | proprietary | **Integrate** later as an optional "live mode", sessions minted and metered by the gateway. |
| [elevenlabs/ui](https://github.com/elevenlabs/ui) Orb, LiveKit components | shadcn-style React voice components (audio-reactive orb). | 2.5k MIT | Copy component (MIT) as the seed for Friday's R3F/GLSL orb. |

**Key ideas**: the voice runtime must run **natively on the Mac host** (mic, Metal/MLX, voice-processing echo cancellation), not in Docker. Barge-in needs acoustic echo cancellation; plan it as its own task. Orb = state machine (idle / armed / listening / thinking / tool-running / awaiting-approval / speaking / error) + audio amplitude. Turkish is what rules out most options. JARVIS/Iron Man names are Marvel IP: keep Friday's look original.

## 4. PC and terminal control

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| OpenClaw node host + macOS companion (exec approvals) | Signed menu-bar app holds the TCC grants; node-local allowlist that the server cannot widen; fail-closed if no UI to ask. | (see §1) | **Copy pattern**: the shape of `friday-node`. |
| [Desktop Commander MCP](https://github.com/wonderwhy-er/DesktopCommanderMCP) | Terminal/file MCP with process sessions. | ~6k, MIT | Avoid as the boundary (the agent can `set_config_value` to loosen itself; `allowedDirectories` does not cover shell). Copy the start/interact/read/kill vocabulary. |
| [Anthropic sandbox-runtime (srt)](https://github.com/anthropic-experimental/sandbox-runtime) | Seatbelt (macOS) / bubblewrap sandbox with a domain-allowlist proxy, no container. | Anthropic, Apache-2.0 (unverified) | **Adopt** on the node for terminal commands (second layer behind the gate). |
| [Peekaboo](https://github.com/openclaw/Peekaboo) | macOS screenshots, AX tree, click/type, CLI + MCP. | ~5.2k, MIT | **Integrate** primitives only; never its agent mode (own LLM calls). |
| [macos-automator-mcp](https://github.com/steipete/macos-automator-mcp) | AppleScript/JXA runner with a recipe library. | ~0.9k, MIT | Copy pattern: a fixed catalogue of named, typed AppleScript actions; no free-form "run AppleScript" outside gate-class. |
| [Cua + Lume](https://github.com/trycua/cua) | Computer-use sandboxes, macOS VMs on Apple Silicon. | ~10k+, MIT (unconfirmed) | Integrate later for open-ended GUI tasks inside a VM. |
| Anthropic computer-use tool | Screenshot/mouse/keyboard through the API. | API | Only via the gateway, only in a VM. |
| [browser-use](https://github.com/browser-use/browser-use), UI-TARS-desktop, Agent S3, Open Interpreter | LLM-driven browser/GUI/code agents. | 70k+ / 20k / 9k / 60k | Avoid as dependencies (own LLM loops; Open Interpreter AGPL). Copy planner/grounder split and the lesson "chat confirmation is not a sandbox". |
| [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | UIAutomation desktop control. | ~6k, MIT | Not needed (owner is on a Mac). |

**Key ideas**: an outbound-only authenticated WebSocket from a signed Mac app; a typed capability catalogue (never a raw shell endpoint); a node-local policy agents cannot edit; three layers (server gate → node confirmation for gate-class → OS sandbox); structured control (AppleScript recipes → AX tree → pixels in a VM, in that order); everything the node returns is untrusted data.

## 5. Coding-agent harnesses ("what are my Claude Code sessions doing")

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/sessions) + hooks + `~/.claude/projects/*.jsonl` | `query()` with resume/fork; `listSessions()/getSessionMessages()`; HTTP hooks on SessionStart, Notification (permission_prompt, idle_prompt), Stop, SubagentStop. | official | **Adopt/Integrate**: cold path (index JSONL) + hot path (HTTP hook to Friday). |
| Claude Code Remote Control / Channels | Steer local sessions from claude.ai/mobile. | proprietary | Do not integrate (no public API; subscription only). Tell the owner it already covers "from my phone". |
| [Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) | JSON-RPC over stdio: thread/list, thread/read, thread/start, turn/start, server-sent `requestApproval`. | Apache-2.0 | **Integrate**: long-lived client per workspace; route approvals into Friday's gate. Pin the version. |
| Google Antigravity `agy -p` | Headless CLI (JSON output); Agent API via Gemini. | proprietary | **Integrate behind a flag** after the owner checks the ToS (a report says consumer plans forbid third-party wrapping). |
| [Agent Client Protocol (ACP)](https://zed.dev/acp) | JSON-RPC "LSP for agents": Gemini CLI `--acp`, claude-agent-acp, codex-acp, goose, Copilot... | Apache-2.0 | **Adopt** as the generic driver for agents beyond Claude/Codex. |
| [Happy](https://github.com/slopus/happy) | Mobile/web client for Claude Code/Codex with encrypted relay, push alerts, a voice agent that delegates to coding sessions. | ~24.7k, MIT | Copy pattern ("voice agent delegates to sessions as tools"). |
| [VoiceMode](https://github.com/mbailey/voicemode) | Voice as an MCP `converse` tool inside Claude Code. | MIT | Not the core loop (mic per session); optional MCP later. |
| [claude-squad](https://github.com/smtg-ai/claude-squad), [opcode](https://github.com/winfunc/opcode), [claudecodeui](https://github.com/siteboon/claudecodeui) | tmux+worktree per agent; session browsers. | 8.8k / 22k / 14.7k, AGPL | Patterns only (AGPL). tmux `capture-pane` as fallback visibility. |
| [ccusage](https://github.com/ryoppippi/ccusage) | Cost per session / 5-hour block from local logs. | ~19.5k, MIT | **Adopt** (shell out from node) or port parsing. |
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban), Nimbalyst, Conductor, Sculptor | Task card → isolated workspace → diff → merge. | 28.8k Apache (sunsetting) / mixed | Copy the flow (Friday already has Gate A/B). |
| [OpenHands agent-server](https://github.com/OpenHands/software-agent-sdk), [goose](https://github.com/aaif-goose/goose) | Full agent stacks; goose speaks ACP. | MIT / Apache-2.0 | Optional extra backends; copy event-sourcing; "recipes are reviewed code" (goose red-team). |
| Omnara | Pivoted; lesson: voice is good for status and short steering, not dictating code. | Apache-2.0 | Avoid. |

## 6. Memory and continuous learning

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [mem0](https://github.com/mem0ai/mem0) | Extract → reconcile (ADD/UPDATE/DELETE/NOOP); since Apr 2026 ADD-only single pass + entity linking. | ~65–69k, Apache-2.0 | Copy pattern into `memory/extraction.py` (all decisions become pending proposals). |
| [Graphiti](https://github.com/getzep/graphiti) | Bi-temporal knowledge graph: facts with validity windows, invalidated not deleted, episode provenance; hybrid search. Neo4j/FalkorDB/Neptune only. | ~33k, Apache-2.0 | Copy the data model into Postgres (no graph DB). |
| [Letta](https://github.com/letta-ai/letta) / Letta Code | Core memory blocks always in prompt; sleep-time consolidation; git-versioned MemFS. File+grep baseline beat Mem0 on LoCoMo (74 vs 68.5). | ~24k, Apache-2.0 | Copy: core blocks (self / owner / focus) and nightly "sleep-time" job. |
| [cognee](https://github.com/topoteretes/cognee) | remember/recall/improve/forget; graph + pgvector. Postgres graph is open-core. | ~28–33k, Apache-2.0 | Study; borrow API shape incl. a real `forget`. |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Graph+vector RAG, incremental, real deletion, all-Postgres backend. | ~41k, MIT | Optional prototype for the `knowledge` app with gateway-wrapped LLM/embedding functions. |
| [Basic Memory](https://github.com/basicmachines-co/basic-memory) | Markdown observations/relations, Obsidian-compatible. | ~4.6k, AGPL | Copy the note format for the one-way Obsidian mirror. |
| Obsidian MCP ([mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian), Local REST API w/ MCP, Smart Connections) | Vault read/write over MCP. | 4.5k MIT | Optional read-only tool; delete/patch gated. Friday writes the vault directly (mirror). |
| [Khoj](https://github.com/khoj-ai/khoj) | Self-hosted "second brain". | ~38k, AGPL | UX reference only. |
| OpenClaw dreaming, Hermes curator | Promotion with thresholds; untrusted content excluded. | | Copy pattern. |
| [MemOS](https://github.com/MemTensor/MemOS), [A-MEM](https://github.com/agiresearch/A-mem), [Memori](https://github.com/GibsonAI/Memori) | Research/SQL-native memory. | Apache / MIT | Ideas only: write-time linking (A-MEM), lifecycle vocabulary. |

**Key ideas**: Friday's existing memory app (types incl. procedural and work_log; pending/approved/archived; private sensitivity; pgvector) is a stronger governed base than any engine above. The upgrade is four tiers: pinned core blocks → episodes (raw activity) → curated memories → temporal fact graph in Postgres, plus nightly consolidation into an approval inbox, hybrid retrieval (pgvector + tsvector + RRF + 1–2 hop CTE), and a private eval set. Public LoCoMo/LongMemEval numbers are vendor-disputed (same system reported 38%–92%).

## 7. Owner-named repos

| Repo | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [Ruflo](https://github.com/ruvnet/ruflo) (ex Claude Flow) | npm meta-harness: ~300 MCP tools, swarms, AgentDB, ReasoningBank trajectory learning, provider routing. | ~82k stars, MIT; npm 3.79.0 | **Avoid** install/MCP: audits report stub tools, unauthenticated MCP, alleged hidden instructions in tool descriptions, a removed obfuscated preinstall, AgentSeal 40/100, open security alerts. **Copy** only "trajectory memory" (procedural lessons from harness runs) and session hooks. |
| [Headroom](https://github.com/headroomlabs-ai/headroom) | Context compression (JSON/code/prose routers), reversible CCR store, cache alignment, MCP/proxy/wrap, `headroom learn`. | ~80k (unverified), Apache-2.0, PyPI 0.44.0 | **Adopt** as a library stage *inside* the InferenceGateway behind a flag (pre/post tokens in ModelUsage); CCR originals in a workspace-scoped table; telemetry off. Not as a proxy or `wrap`. |
| [Graphify](https://github.com/Graphify-Labs/graphify) | Repo → knowledge graph via tree-sitter (no LLM for code), EXTRACTED/INFERRED edges, ADR/WHY nodes, Obsidian export, Claude Code skill. | ~151k (anomalous), Apache-2.0/MIT; PyPI `graphifyy` 0.9.113, single maintainer | **Integrate** in AST-only mode as a sandboxed harness job over Friday's own repo and the owner's repos; import graph.json into Postgres. Semantic pass off (or via gateway). Pin versions. |
| [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) (Stanford) | Local-first agent framework; agent-as-typed-spec over five primitives; energy/latency/cost evals; DSPy/GEPA learning from traces; spoken morning digest; agentskills.io. | ~17.4k, Apache-2.0 | **Copy pattern**: versioned agent spec (read-only to agents), energy/latency in ModelUsage, trace-driven prompt optimisation as approval-gated proposals, morning digest. |
| [Nod](https://github.com/n8n-io/nod) | See §2. | ~2.7k, Apache-2.0 | Copy pattern. |

## 8. Providers and gateway

| Project | What it is | Popularity / licence | Verdict |
|---|---|---|---|
| [OpenRouter](https://openrouter.ai/docs) | One OpenAI-compatible key for hundreds of models; `models[]` fallback; `provider` routing; `usage.cost` per response; BYOK. **Audio**: `/api/v1/audio/transcriptions` (Whisper, GPT Transcribe, Gemini 3.5 Transcribe, Voxtral, ElevenLabs Scribe v2), `/api/v1/audio/speech` (GPT-4o mini TTS, Gemini Flash TTS, Voxtral TTS, ElevenLabs v4/v4-turbo), `input_audio` in chat, stream-only audio-out. **No realtime/duplex endpoint found in search summaries (unverified; re-check the docs before P6 branch 1).** | SaaS | **Integrate** as a provider row on the existing `openai_compat` adapter; fill `transcribe()/synthesize()`; turn-based voice only. |
| [LiteLLM](https://github.com/BerriAI/litellm) | SDK/proxy, cost map, realtime proxy. | very popular, MIT | Copy pattern (tier aliases, cost-map JSON). **Supply-chain**: PyPI 1.82.7/1.82.8 backdoored on 2026-03-24. Never a second door. |
| [Portkey Gateway](https://github.com/Portkey-AI/gateway) | Declarative routing configs; MCP gateway; acquired by Palo Alto Networks. | ~12k, MIT/Apache conflicting | Copy config shape. |
| [Bifrost](https://github.com/maximhq/bifrost) | Go gateway. | ~6k | Avoid. |
| Vercel AI SDK + OpenRouter provider | TS provider registry. | Apache-2.0 | Avoid client-side LLM calls; copy `provider:model` alias convention. |
| Ollama / LM Studio / mlx-lm | Local OpenAI- and Anthropic-compatible serving. | MIT / proprietary / MIT | Already integrated (Ollama); LM Studio optional MLX provider row. |
| OpenAI Realtime / Gemini Live | Only practical cloud duplex. | proprietary | Integrate later, gateway-minted ephemeral sessions, usage events → ModelUsage. |

---

## Honest uncertainties

- **Network limits during research.** The GitHub API was blocked (403) in every research session, and many doc sites (docs.openclaw.ai, openrouter.ai, antigravity.google, docs.n8n.io, docs.letta.com, hermes docs) were unreachable. Facts come from GitHub-rendered pages, raw files, package registries, search snippets and secondary blogs. Star counts are rounded, and some look inflated: Headroom ~80k and Graphify ~151k are anomalous. Licences marked "from memory" or "unverified" in the source research should be checked against LICENSE files before any adoption.
- **OpenRouter voice**: endpoint paths, model names, billing units, and the absence of a realtime endpoint come from search summaries of OpenRouter's docs, not a direct read. Whether streamed responses carry `usage.cost` is unverified. Verify against https://openrouter.ai/docs before branch work.
- **Antigravity**: `agy -p` flags, auth, the reported ToS ban on third-party wrapping, and the Agent API's availability are all secondary-source. Treat Antigravity support as conditional.
- **Codex app-server** method names drift between versions (`turn/steer`, `turn/interrupt` unconfirmed). Pin a version and generate types from the installed binary.
- **Turkish**: no Turkish-specific accuracy figures for smart-turn v3 or LiveKit's turn detector; Moonshine and Parakeet Turkish support unconfirmed; Chatterbox Multilingual real-time factor on Apple MPS unknown; how well a custom "hey friday" openWakeWord model works for a Turkish-accented speaker needs hands-on testing.
- **OpenClaw** security numbers (CVE counts, malicious-skill totals, exposed instances) conflict across vendors. The malicious-skill count is reported as 341, 335, 824+ or 1,184 depending on source and date. The one-click RCE via cross-site WebSocket hijacking is consistently reported, but its CVE labelling is inconsistent, and the `exec.approvals.set` mechanism is unverified.
- **Nod**: launch facts (n8n Labs research preview 2026-11-10, Apache-2.0, standalone, Mac and Windows) are confirmed; its 98% / 226-errand benchmark is self-reported and unverified.
- **OpenRouter audio**: `/api/v1/audio/speech` and `/api/v1/audio/transcriptions` are confirmed. Multipart upload stability and `usage.cost` on streamed calls are unverified.
- **Licensing for adoption**: the Jarvis repo has no LICENSE file (private, single-owner, not distributed). GPL/AGPL code must stay out of process (Piper) or out entirely; non-commercial model licences (openWakeWord pretrained models, XTTS) are acceptable only for personal use and must be recorded in the dependency manifest.
- **Ruflo** audit findings are partly from weak sources (the CVE-2026-59726 claim has no primary source); which findings are fixed in 3.79.0 is unknown. The "avoid" verdict rests on the pattern of findings, not any single one.
- **Self-reported benchmarks** everywhere: Nod (98% errands, 0 unsafe, 93.2% LongMemEval-S), Headroom savings, mem0/Zep LoCoMo, Pipecat <800 ms, Graphify LOCOMO. None independently reproduced.
- **No source code was cloned and run** for any project except a shallow clone of Nod and the n8n docs; all assessments are from READMEs, docs and metadata.
