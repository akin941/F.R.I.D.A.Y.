# Friday 3.0 roadmap proposal (revision 2)

Status: proposal for the owner. Written 2026-11-20, revised the same day after an independent review (verdict REVISE, 26 points). It does not edit any repo file. Inputs: CLAUDE.md, `docs/MASTER_PLAN.md` §Z/§AA and "Roadmap update 2026-11-20", `docs/VISION_FRIDAY_2.md`, the Phase 4/5/6 briefs, ADR-003/004/005/007/008/009/010, the `accounts` (TrustedDevice, DevicePairingCode, PersonalAccessToken.kind), `harness`, `connectors`, `memory`, `skills` and `integrations` apps, `backend/pyproject.toml` import-linter contracts, and `landscape.md` (same folder).

What changed in revision 2, in one line each: node actions are now gate-class by default under a new ADR-013 (no ADR-009 amendment); node policy is local-only with Touch ID; invocations are bound to approvals; voice turns can never execute; a privacy pipeline (scrubber, workspace mapping, TTL, audit view, forget-source) now comes before ingestion; extraction sees only owner-authored text; learning memory moves before the node; scope is cut to a minimal vertical slice with go/no-go checkpoints; cost caps, latency budgets, offline behaviour, observability, testing and credential handling are specified; phase numbers stay stable (Workshop keeps number 7, deferred).

## Yönetici özeti (Türkçe)

- **Hedef:** Friday sürekli öğrenen bir asistan olacak: kendini (kodunu, mimarisini, yeteneklerini) bilecek, seni (geliştiricisini) tanıyacak ve yaptığın işlere hakim olacak. "Hey Friday" ile uyanacak, konuşacak, ekranda animasyonlu bir HUD'u olacak, Claude Code / Codex oturumlarını izleyip yönetecek, Mac'te onayla iş yapacak. Çekirdek kurallar (tek AI kapısı, her çağrıda ModelUsage, onay kapısı, workspace izolasyonu, Postgres asıl kaynak) aynen kalıyor.
- **İlk hedef küçük ve kullanılabilir bir dilim:** tarayıcıda bas-konuş + HUD v1 + "ben kimim / geliştiricim kim / şu an ne yapıyoruz" çekirdek hafıza blokları + oturum paneli v0 (harness işleri ve Claude Code hook olayları). Mac uygulaması gerekmiyor. Her fazın sonunda devam / dur kararı var.
- **"Her zaman öğrenme" Mac uygulamasından önce geliyor (Phase 8).** Önce gizlilik hattı kuruluyor: gizli anahtar/kişisel veri temizleyici (test korpusuyla), her kaynağın hangi workspace'e ait olduğunu belirleyen kural (firma repoları asla kişisel hafızaya girmez), ham kayıtlar için saklama süresi, "ne öğrendi" denetim ekranı ve "X kaynağından öğrendiğin her şeyi unut" işlemi. Öğrenme yalnızca senin yazdığın metinlerden öneri çıkarır; araç çıktısı ve web içeriği güvenilmez veri sayılır. Gecelik öneri sayısı sınırlı; "kendim", "geliştiricim" ve "tercihler" bloklarına dokunan her öneri tek tek onaylanır.
- **Projeleri bağımlılık olarak almıyoruz:** OpenClaw, Hermes, Nod, Ruflo, OpenJarvis kendi LLM çağrılarını, hafızasını ve onay sistemini getiriyor, kurallarımızı deler. Fikirlerini kopyalıyoruz. **Ruflo'dan uzak duruyoruz** (bağımsız denetimlerde işlemeyen araçlar, kimlik doğrulamasız MCP, şüpheli talimatlar). **Headroom** gateway içinde bayrakla kapalı bir sıkıştırma aşaması olur; onay ve araç çağrısı istemlerinde hiç çalışmaz, kalite testi geçmeden açılmaz. **Graphify** Friday'in kendi kodunu ve senin repolarını tanıması için sandbox içinde, sadece AST modunda çalışır. **Obsidian** ayna olarak kalır; `Inbox/` klasörüne attığın notlar bir kez içeri alınır, sonra tekrar okunmaz.
- **Çok sağlayıcı + OpenRouter ses:** OpenRouter, mevcut `openai_compat` adaptörüne bir sağlayıcı satırı olarak eklenir; her deneme ayrı ModelUsage satırı olur. OpenRouter'da konuşmayı yazıya çevirme ve seslendirme var. Gerçek zamanlı çift yönlü ses uç noktası bulamadık, ama bu **doğrulanmadı**; Phase 6'nın ilk dalından önce resmi dokümandan kontrol edilecek. Türkçe seslendirme için büyük olasılıkla OpenRouter TTS gerekecek (Piper Türkçe kalitesi ölçülmedi), bu yüzden "ses buluta gidebilir mi?" kararı Phase 6 başında lazım. Bulut ses için aylık ve oturum başı sert limitler gateway'de uygulanır.
- **Güvenlik modeli sıkılaştı:** Mac uygulaması ("Friday Node") üzerindeki her komut çalıştırma varsayılan olarak **onay kapısı + Mac üzerinde senin tıklaman** ister. Sadece "uygulama aç/öne getir" ve salt okunur işlemler tek tıklık onay sınıfında. Node'un izin listesi yalnızca Mac'te, Touch ID / parola ile değişir; sunucu bu listeyi asla genişletemez. Her onay tek kullanımlık, süreli ve argüman özetine bağlı; sen yokken hiçbir şey kuyrukta bekleyip sonradan çalışmaz.
- **Sesle tetiklenen işlemler:** TV, YouTube ya da odadaki bir konuşma "Hey Friday, terminali aç" diyebilir. Bu yüzden sesli bir tur yalnızca okuyabilir ve öneri oluşturabilir; çalıştırma her zaman ekranda onay ister. Mikrofon durumu her zaman görünür, susturma kapalı-güvenli çalışır, Friday'in kendi sesi uyandırma kelimesini tetikleyemez (gecelik test), konuşmacı doğrulaması olana kadar bu kabul edilmiş risk olarak yazılı.
- **Faz sırası (numaralar sabit):** 5'i kapat → 6 Ses çekirdeği + web HUD (Türkçe değerlendirme seti ile) → **8A Öğrenen hafıza çekirdeği** (sunucu tarafı) → 9 Friday Node v1 salt okunur (önce cihaz güveni spike'ı ve Apple imzalama) → 8B transkript öğrenme + bilgi grafiği → 10 Kontrol (onaylı) → 11 Yerel canlı ses (her zaman açık uyandırma, söz kesme) → 12 Erişim (n8n, brifingler, skill'ler). **Phase 7 (Workshop) numarasını korur ama backlog'a alınır.** Bulut canlı ses, masaüstü overlay, Antigravity, ACP, DSPy "sonra" listesinde.
- **Gerçekçi takvim:** tek kişi + Sonnet 5 inceleme döngüsüyle 5, 6 ve 8A (ilk dilim) yaklaşık 3–4 ay, 9–10 yaklaşık 3–4 ay daha, 11–12 bunun ardından. Toplam 12 aydan uzun olabilir; bu yüzden her faz sonunda devam / dur kontrolü var.
- **Tahmini aylık maliyet (gösterge, doğrulanacak):** öğrenme hattı yaklaşık 8–15 $, Türkçe bulut seslendirme kullanılırsa yaklaşık 5–15 $; toplam yaklaşık 13–30 $. Sohbet ve harness çalışmaları hariç. Kaynak başına günlük token bütçesi ve gateway'de sert limit var.
- **Senin kararın gereken 10 konu** §g'de, her biri için önerilen cevapla birlikte (yeni: Apple Developer hesabı ve imzalama, ham kayıtların saklama süresi).

## Sözlük / Glossary

| Terim (TR) | Term (EN) | Meaning in this plan |
|---|---|---|
| Onay kapısı | Approval gate (gate-class) | An `ApprovalRequest` decided by the owner in the web UI, plus, for node actions, a local click on the Mac (ADR-004/013). |
| Tek tık onay | Confirm-class | A human click, never the model. Today it covers reversible, self-scoped, in-app rows (ADR-009). For the node it covers only open/focus app and read-only calls (ADR-013). |
| Node | Friday Node | Signed Mac menu-bar app. It holds mic/Accessibility grants and runs a fixed capability catalogue. It makes no LLM calls. |
| Bölüm / kayıt | Episode | One raw, scrubbed activity record (a commit, an owner message from a session, a calendar item), kept with a TTL. |
| Çekirdek blok | Core block | A short, always-in-prompt memory block: `self`, `developer`, `current_focus`, `preferences`, ... |
| Konsolidasyon | Consolidation | Nightly job that proposes memory changes. It never applies them. |
| İz kimliği | Trace id | One id that follows a turn from wake word to audit row. |
| Uyandırma kelimesi | Wake word | "Hey Friday". |
| Söz kesme | Barge-in | Interrupting Friday while it is speaking. |
| Yanlış kabul / ret | FAR / FRR | Wake-word false accepts per hour / false rejects in %. |

## Decision table (summary of §g)

| # | Decision | Recommended |
|---|---|---|
| d1 | Node shell technology | Swift menu-bar app; Python voice child process |
| d2 | Audio to cloud (OpenRouter) | Local by default; per-provider owner toggle; needed at P6 start for Turkish TTS |
| d3 | Always-on wake word / cloud live mode | Always-on only after FAR/FRR targets are met; cloud live mode in "later" |
| d4 | Third-party agent terms (Antigravity, Claude Code on a subscription) | Owner checks ToS before any automation driver is enabled |
| d5 | Default ingestion sources | Git, harness runs, Claude Code hook events, calendar metadata on; transcripts on from 8B; others opt-in; screen never |
| d6 | Auto-approve learned memories | No; capped nightly card; per-item for core blocks |
| d7 | Node action classes | New ADR-013: gate-class by default; confirm-class only open/focus app + read-only |
| d8 | Workshop | Keep number 7, defer to backlog |
| d9 | Apple Developer account + signing | Yes, before Phase 9 (99 USD/year) |
| d10 | Raw episode retention | 30 days (14 days for firm workspaces); derived memories keep only a provenance quote |

---

## (a) Target experience: concrete scenarios

All timings and money amounts below are **illustrative examples**, not measurements.

**1. Wake.** Push-to-talk first (Phase 6). Always-on "Hey Friday" arrives in Phase 11, and only after the false-activation budget (§c.3) is met. The mic state is always visible: a menu-bar icon, a HUD ring and the macOS orange dot. Before the wake word fires, no audio leaves the device's ring buffer. The HUD orb goes from *armed* to *listening*, and an earcon plays.

**2. Live talk.** "Bugün ne var?" Silero VAD and smart-turn v3 detect the end of the turn. Local Whisper transcribes. The turn goes through the AI Gateway (ModelUsage written, trace id attached). Pending tasks, calendar items and approvals are read. The orb shows *thinking*, then *speaking*. TTS speaks sentence by sentence. Before Phase 11 it is half-duplex with "Durdur"/Escape; barge-in comes in Phase 11.

**3. "Claude Code oturumlarım ne yapıyor?"** In the vertical slice (before the node), Claude Code command hooks (SessionStart, Notification, Stop) send scrubbed events to the server through a tiny `friday-hook` command with an append-only token, and harness jobs come from the DB. From Phase 9 the node also indexes local transcripts and Codex threads. Example answer: "Üç oturum var. *auth-refactor* testleri geçirdi ve bir süredir seni bekliyor (izin istemi). *friday-hud* hâlâ çalışıyor. Biri dün bitti, yaklaşık 2 dolar tuttu." Summaries go through the gateway (purpose `sessions.summarize`).

**4. "Repo X'te Codex ile bir çalışma başlat: login throttle testini düzelt."** Friday creates a harness implementation request (existing Phase 3 pipeline: plan, Gate A, worktree, tests, cross-vendor review, Gate B). A voice turn can only *propose* this. Friday says "Plan onayı ekranda" and the orb shows *awaiting approval*. The owner approves on screen. Voice never approves (Phase 6 D8, ADR-014).

**5. "Terminali aç ve testleri çalıştır."** "Open terminal" is confirm-class: a click on the node's own window. "Run tests" is **gate-class** until ADR-013 proves the throwaway-worktree sandbox (§b.B1). Gate-class means an ApprovalRequest in the web UI plus a click on the node window. The node window shows the arguments as the node itself renders them, not as the server describes them. Output streams into a HUD panel as fenced, untrusted data.

**6. HUD states.** idle · armed · listening · thinking · tool-running · awaiting-approval · speaking · error · muted · **disconnected** · **offline-local**. The side panels show the transcript and captions, sessions, approvals, today's tasks, live cost and a health panel (mic, node link, providers, preflight).

**7. Learning.** Overnight, Friday reads yesterday's scrubbed episodes (commits, harness outcomes, owner messages from sessions). It proposes **at most 10 items**. Each item has a provenance quote from the owner's own words. Card: "Friday 6 şey öğrendi. Örneğin: *pytest'i `-x` ile çalıştırmayı tercih ediyorsun* (kaynak: 19 Kasım, auth-refactor oturumundaki mesajın). Onayla / düzenle / reddet." Changes to `self`, `developer` or `preferences` blocks are approved one at a time, never in bulk. Asked "Sen kimsin, seni kim geliştiriyor?", Friday answers from its `self` and `developer` blocks. `self` is assembled deterministically from quoted CLAUDE.md/ADR lines and the live capability registry. `developer` is owner-edited.

---

## (b) Architecture deltas, each with its kernel-rule check

Rules: **R1** workspace scoping / `for_actor()` · **R2/R3** single gateway door + ModelUsage on every call · **R4** approval gate (ADR-004/008/009/013) · **R5** mockable LLM code · **R6** one-way imports (import-linter) · **R7** tests incl. role × endpoint · **R9** Postgres is truth, Obsidian is a mirror · plus audit and prompt injection.

### B1. Friday Node (Mac companion app)

- **Shape.** One **signed and notarized** macOS menu-bar app, `friday-node`. It holds the TCC grants (Microphone, Accessibility, later Screen Recording and Automation), the wake word and the voice child process (Phase 11). It opens an **outbound-only** WSS connection to the server. It has no TCP listeners at all. Signing and notarization are a hard prerequisite: an unsigned app cannot keep stable TCC grants (decision d9).
- **Identity (spike S9.0, before Phase 9).** The repo already has device trust: `accounts.TrustedDevice` (hashed secret, soft revoke, `revoke_all_devices`), `DevicePairingCode` (PIN/strong codes) and `PersonalAccessToken.kind`. But `TrustedDevice` is shaped for a browser: an HttpOnly cookie and idle expiry. The spike must show that a `kind=node` principal works: a long-lived node credential in the macOS Keychain, rotation without re-pairing, immediate revocation, an owner kill-switch (web UI + `manage.py`), and that `revoke_all_devices` covers nodes. If the spike fails, Phase 9 does not start.
- **Protocol (ADR-012).** On connect the node sends `hello` with its **node-owned** capability catalogue (name, version, class, JSON schema). The server can only invoke catalogue entries. It sends `invoke` with the capability id, typed args and, for gated calls, an **invocation grant**. The node sends `result` (exit, truncated output, artifacts hash) and `event`. There is no generic "run this string" capability.
- **Invocation grant (binds approval to execution).** When the owner approves, the server issues a grant signed with a server key whose public half is pinned in the node at pairing. The grant covers: capability id + version, the canonical-JSON args hash, workspace id, node id, the approval id, a random nonce, `issued_at` and `expires_at` (at most 120 s). The node checks the signature, expiry and args hash. It stores used nonces until they expire, so each grant works once and replay fails. The grant alone is never enough: the node still shows its own confirmation window, rendered by the node from the args. If the server is compromised it can sign grants, but it still cannot get past the local click or run anything outside the catalogue.
- **Node policy is local-only.** The allowlist, exec policy and capability catalogue can be changed **only in the node's own UI, behind a local user-presence check** (Touch ID or the macOS password, through LocalAuthentication). There is **no** WS message, server endpoint or web-UI path that reads or writes node policy. This is the direct OpenClaw lesson: an approval-settings endpoint reachable over a hijacked WebSocket was exploited. A test asserts that the protocol has no policy-mutation message type.
- **Owner away.** Deny by default. The local confirmation window times out after 60 s → deny. An ApprovalRequest for a node action expires after 10 min. Nothing is queued to run later: an expired grant is dead, and the server must create a new proposal. If the node goes offline, invocations in flight fail and are not retried.
- **Server side.** A new kernel app `nodes` (Node, Capability snapshot, Invocation audit rows, the WS consumer). Import-linter contracts (§c.9): `nodes` may import `approvals`, `audit`, `workspaces`, `accounts`, `core`; it must not import `ai_core` (the node path makes no LLM calls), `memory`, `harness` or `apps.modules.*`. It is added to the "kernel apps must not import config" and "ai_core must not import business apps" lists.
- **Capabilities in Phase 9 (read):** `claude.sessions.list/read`, `codex.threads.list/read`, `usage.ccusage`, `git.status` (registered repos only), `apps.frontmost`, `windows.list`. Reading `~/.claude/projects` and `~/.codex/sessions` needs an **explicit per-directory owner grant** in the node UI (Touch ID). It is off by default.
- **Claude Code hook relay.** Command hooks (not HTTP hooks) call the signed `friday-node hook` CLI. The CLI writes to a **Unix domain socket** owned by the user with mode 0600, and the node checks the peer's uid. There is no loopback HTTP port for browsers or other users to probe. Before the node exists (vertical slice), `friday-hook` posts straight to the server with an append-only `hook` PAT kind kept in the Keychain.
- **Kernel rules.**
  - R4: see ADR-013 below. Voice never decides (ADR-014).
  - R1: every Invocation and every event carries `workspace_id`, resolved from the repo registry. An unmapped path is **dropped**, never defaulted to personal.
  - R2/R3: the node makes no LLM calls and **holds no provider API keys**. Summaries run server-side through the gateway.
  - Audit: one row per invoke (capability, args hash, grant nonce, exit, duration, workspace, trace id), hash-chained.
  - Prompt injection: everything the node returns is fenced as untrusted data (Phase 5 D4).

**ADR-013, node action classification (replaces the draft's "ADR-009 amendment"; ADR-009 is not touched).**

| Class | Node actions | Who acts |
|---|---|---|
| read (ungated, audited) | the read capabilities above | none; the per-directory grant was given once with Touch ID |
| confirm-class (narrow exception) | open or focus an app (bundle id from a local list) | a human click on the node window; the model can never confirm |
| gate-class (**default**) | every command execution: test, lint, build, dev servers, AppleScript recipes, AX click/type, keystrokes into other apps, screenshots, starting a write-mode coding agent, any file write, any outbound network | ApprovalRequest in the web UI **and** a click on the node window, bound by the invocation grant |

Test and lint commands may move to confirm-class **only if** ADR-013 shows they are reversible and self-scoped. The required evidence: the command runs in a throwaway git worktree created by the node, under `srt` with write root = that worktree, network denied, environment stripped, a CPU/time limit, and the worktree deleted afterwards; tests prove that a write outside the worktree, a network call and a sandbox escape attempt all fail. Dev servers stay gate-class (long-lived, open ports). Until ADR-013 is approved with this evidence, everything except open/focus app is gate-class.

### B2. Voice pipeline

- **Phase 6 stays as approved**, with a short amendment: browser push-to-talk, a browser wake word, local faster-whisper/Piper behind `openai_compatible`, half-duplex, D8 "voice never decides". Changes: (1) TTS routing by language; (2) OpenRouter STT/TTS adapter methods, seeded disabled; (3) a **Turkish evaluation set** and latency budget as exit criteria; (4) the TTS fallback for Turkish is chosen in the branch-1 spike (OpenRouter TTS if Piper fails the owner listening test), which needs decision d2 at P6 start; (5) audio budget caps in the gateway; (6) HUD v1 in place of branch 5's identity layer.
- **Phase 11 (local live voice).** A Pipecat child process of `friday-node`, running natively: openWakeWord ("hey friday", custom-trained) → Silero VAD → smart-turn v3 → MLX Whisper (whisper.cpp fallback) → **an LLM step that calls Friday's streaming chat endpoint**, never a provider → language-routed TTS. It adds macOS voice-processing AEC and barge-in.
- **Cloud live mode** (OpenAI Realtime / Gemini Live) moves to the "later" list. When it comes back, the rule is fixed now: the session is **proxied through a gateway WebSocket relay**. The provider key and session stay server-side, the client never gets a provider token, and usage events are read by the relay. This keeps ADR-003 intact. It needs an ADR-003 amendment that makes a realtime session a metered unit, with per-session and monthly caps that close the relay. Function calls from the realtime model are treated as untrusted model output: they become proposals under ADR-014 and get the same fencing as any tool output.
- **ADR-014, voice-originated turns and the always-on mic.**
  - **Capability cap.** A turn whose input came from the mic (`channel=voice`, `speaker=unverified`) can call read tools and **create proposals** only. It can never execute a confirm-class or gate-class action. Execution always needs an on-screen action by the owner.
  - **Mic state and mute.** A persistent menu-bar indicator plus a HUD ring. Software mute (menu-bar toggle and a global hotkey) **fails closed**: if the mute state cannot be read, or the node loses its WS link, the mic is released. (Most Macs have no hardware mic switch; see rejected points.)
  - **Self-trigger rule.** While TTS plays, the wake detector is suppressed (half-duplex) or fed the AEC-cleaned signal (Phase 11). A wake hit within 1.5 s of TTS output whose transcript matches recent TTS text is discarded. **Nightly self-trigger test:** a TTS clip that says "Hey Friday, terminali aç" is played through the replay harness; the assertion is no wake and no proposal.
  - **Untrusted audio.** Remote transcripts (calls, videos) and Friday's own TTS output are data. The Nod-style trust check marks voice-originated utterances as weaker evidence than typed ones.
  - **Accepted risk.** Speaker verification is deferred. Until it exists, a person or a TV in the room can make Friday *propose* things, but never *run* them. Phase 10's "terminal by voice" therefore means "voice proposes, screen approves".
- **Kernel rules.** R2/R3: local STT/TTS go through the gateway's `transcribe()/synthesize()` (cost 0, real latency recorded). Privacy (ADR-008 d5, P6 D5): no audio leaves the device before the wake word; cloud audio needs `personal_data_allowed` per provider. R5: STT, TTS, VAD and wake word sit behind ports with fakes.

### B3. HUD

- **P6 HUD v1.** A full-screen `/hud` route in Next.js with an R3F/GLSL orb, seeded from the elevenlabs/ui Orb (MIT). It is driven by one event stream (SSE). States as in (a)6. Panels: tasks, approvals, cost, health; sessions v0 from Phase 8A.
- **Accessibility and budget.** `prefers-reduced-motion` gives a static orb with a colour state. Captions are always available, and every state is also shown as text. Frame cap: 30 fps active, 5 fps idle, 0 when the tab is hidden. GPU budget: under 5% on an Apple-silicon laptop when idle, measured in a P6 check. Battery: no always-on animation on battery unless the owner turns it on.
- **Desktop overlay (WKWebView)** moves to "later".
- **Kernel rules.** The HUD is a read client over `for_actor()` endpoints (R1). It has no decide button except inside the same authenticated web session (D8).

### B4. Continuously-learning memory (Memory v3)

Split into **8A** (server-only, right after Phase 6) and **8B** (transcripts + graph, after the node).

- **T0, core blocks (8A).** `CoreMemoryBlock` labels: `self`, `developer`, `current_focus`, `active_projects`, `preferences`, plus the existing firm dossiers. They are token-budgeted, versioned, injected first, and change only through approved diffs.
  - `self` is **assembled deterministically**: a template fills in quoted lines from CLAUDE.md and the ADRs, the enabled capability list from the tool registry, the enabled providers, and the current phase from MASTER_PLAN. No LLM writes facts into it. An LLM may only propose wording, and that proposal is marked *inferred* and approved per item.
  - `developer` is **owner-edit-only**. Agents may propose additions, each with a quote from owner-authored text, approved per item. Friday never invents facts about itself or the owner.
- **T1, episodes, behind the privacy pipeline (ADR-011).**
  1. **Source registry.** Each source declares an owner, a workspace mapping rule and a daily token budget. Sources: git commits (registered repos), harness outcomes, Claude Code hook events (8A); Claude/Codex transcripts via the node (8B); calendar/mail metadata; an Obsidian `Inbox/`; shell history (opt-in).
  2. **Workspace mapping.** repo path / calendar / account → workspace through the repo registry and firm profiles. Firm repos and firm transcripts land in the firm workspace and **never** in personal memory. Unmapped items are dropped and counted.
  3. **Scrubber before storage.** It runs on the node (8B) or the server (8A) and again server-side. It uses secret rules (gitleaks-style patterns plus entropy), PII patterns (e-mail, phone, TCKN, IBAN, card numbers) and a per-firm customer-name list. Hits are replaced with typed placeholders. The test corpus contains synthetic API keys, tokens, `.env` dumps, Claude transcript fragments, shell history and Turkish PII. CI requires 100% recall on the corpus's secrets class.
  4. **TTL.** Raw episodes expire after 30 days (14 for firm workspaces, decision d10). Derived memories keep only a provenance quote and a link to the episode id.
  5. **Ingestion audit view.** Per source: counts, last N items, scrubber hits, dropped/unmapped counts, tokens spent against budget.
  6. **Forget source X.** One owner action: episodes → facts → memories → Obsidian mirror, as a reversible trash move. Purge is gated (ADR-009). The source is paused at the same time.
  7. **Never ingested:** screen content, screenshots, AX trees, keystrokes, browser history. P10 screenshots are on-demand, gate-class invocations whose output is used for that one turn and never stored as an episode.
- **T2, memories (extraction with injection defence).**
  - The extraction prompt sees **only owner-authored turns**. For Claude Code transcripts that means `user` messages typed by the human; `tool_result` blocks, assistant text and web content are excluded. Any other context it needs is passed inside an untrusted-data fence and cannot be quoted as provenance.
  - Every proposal carries a provenance quote that must match owner-authored episode text verbatim. This is checked in code; a proposal without a match is dropped.
  - Caps: at most 10 proposals per night and at most 3 core-block diffs per week. Bulk approve is allowed only for `work_log`-class items. Anything touching `self`, `developer` or `preferences` is approved per item.
  - Content from external tools or MCP is never auto-promoted.
- **T3, temporal facts in Postgres (8B).** `MemoryEntity` / `MemoryFact` with validity windows, superseded_by, source episode and confidence. Recursive CTEs, no graph DB.
- **Loops.** Nightly consolidation (proposal-only, capped). A weekly reflection. DSPy/GEPA moves to "later".
- **Self-knowledge (8B).** A Graphify AST-only job in a harness container, run on merges to `main`. Its graph.json is imported as entities and facts with `source=graphify`. It feeds the `self` template; it does not write the block.
- **Retrieval.** pgvector HNSW + tsvector + RRF, workspace filter first, 1–2 hop expansion, recency decay.
- **Headroom (8B, flag off by default).** A pre-send gateway stage. It is **bypassed** for approval prompts, tool-call/argument prompts, trust-check prompts and core-block assembly. It is enabled per purpose only after a quality eval: the private eval set run with and without compression, with no more than 1 point accuracy loss allowed. Pre/post token counts go into the ModelUsage context report. Telemetry is off.
- **Obsidian (ADR-005 kept).** The vault stays a mirror. `Inbox/` items are **imported once** as Episodes. The file is then moved by Friday to `Inbox/_imported/` and is never read again. If the owner edits a mirrored note, the next mirror write overwrites it. The overwritten text is saved to `_conflicts/<date>/` and a notice tells the owner to make the change in the web UI. The vault never changes Postgres.
- **Cost control.** Triage (is this episode worth extracting?) runs on the local tier (Ollama, purpose `memory.triage`, cost 0). Extraction and consolidation run on a cheap cloud tier only for non-`private` rows, within per-source daily token budgets enforced by the gateway. `private` rows use local models only.
- **Kernel rules.** R1: every table is `WorkspaceScopedModel`. R2/R3: purposes `memory.triage`, `memory.extract`, `memory.reconcile`, `memory.consolidate`, `memory.reflect`, `knowledge.graph_import`, each tiered (P5 D11 coverage test). R4: core blocks and playbooks are proposals.

### B5. Multi-provider gateway (incl. OpenRouter)

- An `openrouter` ModelProvider row on `openai_compat`, with `transcribe()`/`synthesize()` against `/api/v1/audio/transcriptions` and `/audio/speech` (confirmed). Two things are still unverified and are checked in the P6 branch-1 spike: multipart upload stability, and whether streamed calls carry `usage.cost`. If they do not, a Celery task patches cost from the generation lookup.
- Tier entries become ordered target lists. Each fallback attempt is its own ModelUsage row and stores the *served* model and provider. **Offline tier:** if every cloud target fails, the tier falls back to the local Ollama model and the HUD shows *offline-local*.
- **Budget enforcement in the gateway (ADR-003 amendment, P6).** A pre-call check against a per-purpose daily budget, a per-session cap and a monthly cap for every non-zero-cost audio purpose (`voice.stt`, `voice.tts`, later `voice.realtime`). The call is refused when a cap would be exceeded, and the refusal is recorded.
- No LiteLLM proxy or second door.

### B6. Coding-agent drivers (harness v3, Phase 10)

- Keep the Docker harness for write work.
- **Claude Agent SDK** and **Codex app-server**: both pinned to an exact version, with types generated from the installed version and a contract test per pin. A Codex `requestApproval` becomes an ApprovalRequest.
- **Codex distinction.** CLAUDE.md retired Codex CLI from *how Jarvis is built* (branch reviews are Sonnet 5 subagents). The Codex app-server driver here is a *product feature*: Friday driving the owner's Codex sessions as a worker, like the harness's existing cross-vendor reviewer. It does not bring Codex back into Jarvis's own development review.
- **Trust check (Nod pattern).** A cheap gateway call compares gate-class arguments with the owner's recent typed utterances (voice utterances count as weaker evidence), never with tool output. It can only warn or block. It never approves.
- ACP generic driver and the Antigravity `agy` runner move to "later". Both require decision d4.

### B7. Integration strategy (Phase 12)

- MCP-first through the Phase 5 connector framework. n8n: narrow MCP Server Trigger workflows, a Friday-side workflow-ID allowlist, writes gated, async voice turns.
- Copy from OpenClaw/Hermes: SKILL.md skills with staged approval and a curator; script-gated heartbeat/cron (cron cannot create cron; unattended = deny by default); spoken morning digest; wake-phrase routing.
- Do not copy: public skill registries, "allow always" wildcards, LLM auto-approval, host execution by default.

---

## (c) Cross-cutting requirements

### c.1 Latency budget (voice)

The target is measured from end of owner speech to the first audio byte. Local path: p50 ≤ 1.5 s, p95 ≤ 2.5 s.

| Stage | Budget (p50) |
|---|---|
| End-of-turn detection (VAD + smart-turn / finish window) | 300 ms |
| STT (local Whisper, ≤ 8 s utterance) | 350 ms |
| Gateway LLM, time to first sentence | 550 ms |
| First-sentence TTS | 250 ms |
| Transport + playback start | 50 ms |

The stage timings are written into the ModelUsage context report of the turn's LLM row and into a `VoiceTurn` trace row. The nightly replay harness fails if p50 regresses more than 20% from the stored baseline.

### c.2 Cost caps and runaway guards

- Gateway-enforced caps (B5): per purpose per day, per session, per month. Default monthly audio cap: 20 USD, owner-editable.
- Runaway guards: a voice session closes after 30 s of silence. A realtime relay (later) closes after 30 s of silence, 15 min, or 1 USD per session. Wake-word storms: more than 3 activations in 60 s without a dispatched turn → auto-mute and a HUD notice.

### c.3 Turkish evaluation set and wake-word budget (exit criteria)

- **Set:** about 200 owner-recorded utterances. Roughly 100 Turkish, 50 code-switched ("repo'yu aç", "PR'ı merge et", "deploy'u durdur") and 50 English. They include firm and project names, recorded in a quiet room and with room noise (TV, fan). Plus 2 h of negative audio (Turkish and English TV, podcasts, calls) for wake-word false accepts.
- **Targets:** WER ≤ 12% Turkish quiet, ≤ 18% Turkish with noise, ≤ 15% code-switched. Wake word FRR ≤ 10%; FAR ≤ 0.5 per hour on the negative set. Before always-on ships (Phase 11), FAR ≤ 0.2 per hour on 24 h of household audio. Turkish TTS: the owner rates the chosen voice ≥ 4/5 for intelligibility and naturalness.
- These numbers are P6 exit criteria (push-to-talk STT, TTS choice) and P11 exit criteria (wake word, always-on). They are **initial targets** and are re-set after the first measurement if they prove unrealistic. Any change is recorded in the brief.

### c.4 Offline and degraded behaviour

| Failure | Behaviour |
|---|---|
| Internet down, server up | Local tier answers (Ollama); local STT/TTS continue; cloud providers marked down; HUD *offline-local*; ingestion keeps local work and queues cloud extraction for later (extraction only, never actions) |
| Server down / WS dropped | Node **fails closed**: no invocations, mic released for always-on, local confirm windows closed. HUD *disconnected*. Heartbeat 15 s; reconnect with exponential backoff (1 s → 60 s, jitter) |
| Hook events while the WS is down | `friday-node hook` spools scrubbed events to a local bounded file (≤ 10 MB, 24 h, mode 0600) and uploads them on reconnect. Events only, never commands |
| Approval pending while the owner is away | Expires (10 min for node actions); nothing executes later |

### c.5 Observability

- One **trace id** from wake/push-to-talk through STT, gateway call(s), tool call(s), node invocation and TTS to the audit row(s).
- Node structured logs: local, redacted by the same scrubber, rotated, never uploaded except as a redacted excerpt the owner attaches.
- HUD health panel: mic state, node link and last heartbeat, provider status, last preflight result, budgets used.
- Metrics: approval latency (created → decided), wake accept/reject counts and FAR estimate, per-stage voice latency, ingestion counts and scrubber hits.
- `manage.py preflight` is extended with node reachability and catalogue version, the voice stack (STT/TTS round trip on a fixture WAV), provider rows and budget configuration.

### c.6 Testing strategy

- **Recorded-audio replay harness:** golden WAVs (wake, near-miss, barge-in, Turkish and code-switched phrases, the self-trigger clip) are fed into the pipeline with fakes. It runs in CI without a mic.
- **Role × endpoint permission tests** (R7) for the `nodes` WS consumer, the hook-ingest endpoint and every node capability: anonymous, wrong-kind PAT, revoked node, other-workspace actor.
- **Fuzz and injection tests** for capability argument schemas: path traversal, shell metacharacters, Unicode tricks, oversize args, and `srt` escape attempts (write outside root, network, `/tmp` symlinks).
- **Compromised-server tests:** a fake server sends an invoke for an unknown capability, args outside the schema, a reused or expired nonce, a bad signature, or a policy-mutation message. The node refuses every one.
- **Prompt-injection corpus:** fenced untrusted output (terminal, transcripts, MCP, web) must never produce a tool call or approval, and must never become a provenance quote.
- **Scrubber corpus** (B4) and **extraction tests** (owner-only turns, provenance matching, nightly caps).
- **Real providers and real audio models:** nightly only (R5).

### c.7 Credentials and the node's local attack surface

- The node credential and the hook PAT live in the **macOS Keychain**, never in files. The node holds **no provider API keys**.
- `srt` runs commands with a **stripped environment** (allowlisted variables only) and denies reads of `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.claude`, `~/.codex`, Keychain files and browser profiles.
- `~/.claude/projects` and `~/.codex/sessions` are read only by the node's own reader (not by sandboxed commands), and only after the per-directory Touch ID grant. Content is scrubbed before upload.
- No loopback TCP listener; the hook path is a 0600 Unix socket with a peer-uid check (B1). The WS client pins the server certificate or CA.

### c.8 Dependency and supply-chain controls

| Dependency | Risk | Control |
|---|---|---|
| Pipecat | 0.0.x API churn | exact pin, generated adapter tests, upgrade only in a dedicated branch |
| openWakeWord | code Apache-2.0; pretrained models reportedly non-commercial | confirm the licence from the LICENSE/model cards before P6 branch 4; train our own "hey friday" model; record that Friday is personal, non-distributed use |
| Piper | GPL-3.0 | runs as a separate executable spoken to over stdio/HTTP, never imported or linked; not shipped inside the signed node bundle (installed separately); a test checks there is no `import piper` in the repo |
| Graphify | single maintainer, anomalous stars | AST-only mode inside a network-denied harness container; exact pin + hash; output is data |
| Headroom | silent context loss in the hot path | flag off, per-purpose enable after eval, bypass list (B4) |
| Kokoro, MLX Whisper, smart-turn, `srt`, ccusage, Peekaboo | ordinary | pin + hash |

For all of them: the `docs/security/mcp-governance.md` allowlist + manifest; **hash-pinned lockfiles** (`uv.lock` hashes, `npm ci` with integrity, Swift Package.resolved); **install scripts disabled** (`--ignore-scripts`, no postinstall); a licence review per dependency. The repo has no LICENSE file (private, single-owner): GPL/AGPL code must not be linked into it, and non-commercial model licences are acceptable only for personal use and are recorded in the manifest. Ruflo, OpenClaw, Nod, LiteLLM: not dependencies.

### c.9 Import-linter contracts (R6)

New contracts, added in the branch that adds each app:

- `nodes` → forbidden: `ai_core`, `memory`, `knowledge`, `harness`, `assistant`, `apps.modules.*`. Added to the "kernel apps must not import config" and "ai_core must not import business apps" lists.
- `memory.ingestion.*` connectors may import `memory`, `workspaces`, `audit`, `ai_core` (gateway only); forbidden: `nodes` (they receive data through a `memory`-owned port), `harness` internals (they read harness outcomes through a read model), `connectors` internals.
- A Graphify importer and transcript parser live in `knowledge.importers` / `memory.ingestion` and never import a third-party package outside the pinned manifest.

### c.10 Projected monthly cost (illustrative, to verify in P6/8A)

| Item | Assumption | Estimate |
|---|---|---|
| Episode triage | local tier | 0 USD |
| Extraction (owner-authored text only) | about 40 episodes/day × 2k tokens on a cheap cloud tier (about 0.2–0.6 USD per million input tokens) | about 1–3 USD |
| Nightly consolidation + weekly reflection | about 60k tokens/night on a mid tier (about 3 USD/M in, 15 USD/M out) | about 6–10 USD |
| Session summaries | about 20/day × 4k tokens, cheap tier | about 1–2 USD |
| Turkish cloud TTS (if Piper fails) | about 15 min speech/day ≈ 15k characters/day | about 5–15 USD |
| Local STT/TTS, wake word | local | 0 USD |
| **Total, learning + voice** | | **about 13–30 USD/month**, capped by gateway budgets |

Chat turns and harness coding runs are excluded (already tracked). Default caps: learning 20 USD/month, audio 20 USD/month.

---

## (d) Phase plan (numbers stable; execution order is the row order)

**Numbering rule.** Existing numbers keep their meaning. Phase 7 stays "Workshop" (status: deferred to backlog; no brief or branch exists, so nothing becomes ambiguous). New phases take numbers 8–12. Branches follow `feat/pN-<topic>` with these numbers, for example `feat/p8-core-blocks` and `feat/p9-node-pairing`.

Renumber map from revision 1 of this proposal (the only earlier source of these names): rev1 P7 node → **9**; rev1 P8 memory → **8A/8B**; rev1 P9 control → **10**; rev1 P10 live voice → **11**; rev1 P11 reach → **12**.

| Order | Phase | Scope | Usable outcome | Go/no-go checkpoint | Est. |
|---|---|---|---|---|---|
| 1 | **5 (finish)** | 5.7 Home Assistant reconcile; 5.9 connectors UI; 5.10 closeout with preflight; CI green on `integ/phase-5` (former repository, PR #29) → `main`; seed an `openrouter` text provider row | Deploy + MCP usable from the UI | preflight green; `integ/phase-5` merged to `main` | 2–3 wk |
| 2 | **6 Voice core + web HUD** | Approved branches 1–4 + amendment (§B2); OpenRouter audio spike; Turkish eval set; latency trace; audio caps; HUD v1 (+ health panel, a11y) | Push-to-talk Friday in a browser with a real HUD, Turkish tested | c.3 P6 targets met or consciously re-set; c.1 local p50 ≤ 1.5 s; caps enforced | 5–7 wk |
| 3 | **8A Learning memory core** (server only) | ADR-011; core blocks (`self` deterministic, `developer` owner-edit); source registry + workspace mapping + scrubber + TTL + audit view + forget-source; sources: git (registered repos), harness outcomes, Claude Code hook events via `friday-hook` (append-only PAT), calendar metadata; owner-only extraction, capped morning card; sessions panel v0 | **Vertical slice complete**: talk to Friday, it knows itself and you, shows your Claude Code sessions, learns from yesterday | scrubber corpus 100%; zero cross-workspace leaks in tests; owner accepts at least 60% of proposals over 2 weeks (else tune before 8B) | 5–7 wk |
| 4 | **9 Friday Node v1 (read-only)** | Prereqs: spike S9.0 (device trust `kind=node`), Apple Developer signing + notarization (d9), ADR-012. Then: `nodes` app + WS; Swift menu-bar app; pairing; read capabilities; per-directory grants; Unix-socket hook relay; spool; heartbeat; preflight extension | "Claude Code / Codex oturumlarım ne yapıyor?" with local transcript detail | spike passes; compromised-server tests pass; notarized build keeps its TCC grants across updates | 6–8 wk |
| 5 | **8B Transcripts + knowledge graph** | Claude/Codex transcript ingestion via node (owner turns only); temporal Entity/Fact; RRF retrieval; Graphify AST job; Headroom behind flag + eval; Obsidian mirror format + Inbox import-once; private eval set | Friday knows the details of your work across tools | eval set baseline recorded; Headroom stays off unless ≤ 1 pt loss | 5–6 wk |
| 6 | **10 Control** | ADR-013; gate-class node capabilities (allowlisted repo commands under `srt`, AppleScript recipe catalogue, AX primitives, app open/focus as confirm-class); node confirmation window; invocation grants; HUD terminal panel; harness v3 (Codex app-server + Agent SDK pinned, approval mapping, trust check); voice tools that only propose | "Testleri çalıştır", "Codex ile başlat": proposed by voice, approved on screen, run safely | fuzz/escape suites green; ADR-013 decision on test/lint confirm-class recorded | 6–8 wk |
| 7 | **11 Local live voice** | ADR-014 in full; Pipecat child process; always-on wake word; smart-turn; AEC; barge-in; self-trigger nightly test | Natural, interruptible voice anywhere on the Mac | FAR ≤ 0.2/h on 24 h household audio; self-trigger test green; latency targets met | 5–7 wk |
| 8 | **12 Reach and proactivity** | n8n MCP (allowlisted workflows); heartbeat/cron + spoken morning digest; SKILL.md skills + curator; procedural playbooks from harness runs | Friday starts useful conversations and uses your automations | owner keeps the digest on after 2 weeks | 4–5 wk |
| — | **7 Workshop** (number kept) | Unchanged brief intent | — | after 12, or earlier by owner decision | backlog |

Estimates assume one owner with Claude implementing and an independent Sonnet 5 review loop per branch (often 2–3 rounds). Phases 5 → 8A (the vertical slice) take about 3–4 months; 9 → 10 about 3–4 months more; 11–12 after that. **The whole plan is likely more than 12 months.** Each checkpoint is a real decision point: stop, re-scope, or continue.

**Later list (not scheduled):** cloud realtime live mode (gateway WS relay, ADR-003 amendment); desktop WKWebView overlay; Antigravity driver; ACP generic driver; DSPy/GEPA prompt optimisation; speaker verification; Cua/VM computer use; LiveKit multi-device/phone; Chatterbox Turkish TTS benchmark; WhatsApp.

---

## (e) Exact document edits this plan requires

Each item is written on its own docs branch, reviewed by an independent Sonnet 5 subagent until APPROVE, then accepted by the owner. The implementing model never reviews its own document.

| Document | Edit | When |
|---|---|---|
| `docs/adr/ADR-011-continuous-learning-ingestion.md` (new) | Source registry, workspace mapping, scrubber + corpus, TTL, audit view, forget-source, owner-only extraction, caps, never-ingested list; extends ADR-005 | before 8A branch 1 |
| `docs/adr/ADR-012-friday-node-protocol.md` (new) | Outbound WSS, `kind=node` identity, node-owned catalogue, invocation grant (args hash, nonce, expiry ≤ 120 s, single use), local-only policy with Touch ID, no policy over WS, fail-closed, owner-away rules, Unix-socket hooks | after spike S9.0, before Phase 9 branch 1 |
| `docs/adr/ADR-013-node-action-classification.md` (new) | Table in §B1; gate-class default; confirm-class = open/focus app only; evidence bar for test/lint; dev servers gate-class; "confirm" = human click on the node. ADR-009 unchanged | before Phase 10 |
| `docs/adr/ADR-014-voice-originated-turns.md` (new) | Capability cap, mic indicator, fail-closed mute, self-trigger rule + test, untrusted audio, accepted risk (no speaker ID) | with the Phase 6 amendment (cap + indicator); completed before Phase 11 |
| `docs/adr/ADR-003-ai-gateway-single-door.md` (amend) | Audio purposes and gateway-enforced budget caps (P6); later, a second amendment for the realtime relay as a metered session | P6; the realtime part only when it leaves "later" |
| `docs/MASTER_PLAN.md` §AA table | Phase 7 status → "deferred to backlog (2026-11-xx, owner decision d8)"; add rows 8A/8B, 9, 10, 11, 12 with scope and status "planned"; note the stable-numbering rule | after the owner accepts this proposal |
| `docs/MASTER_PLAN.md` "Roadmap update 2026-11-20" | Point to this plan; move the Workshop paragraph under "Backlog" | same branch |
| `docs/prompts/phase-6-voice-identity/brief.md` | Amendment section: §B2 items 1–6, Turkish eval exit criteria, latency budget, ADR-014 voice cap | before P6 branch 1 |
| `docs/prompts/phase-8-learning-memory/brief.md`, `phase-9-friday-node/`, `phase-10-control/`, `phase-11-live-voice/`, `phase-12-reach/` | New briefs, each written at phase start | at each phase start |
| `docs/security/mcp-governance.md` manifests | One per dependency in c.8 | in the branch that adds it |
| `backend/pyproject.toml` import-linter | Contracts in c.9 | in the branch that adds each app |
| `.env.example` | Budget caps, OpenRouter key name, node WS settings (names only) | in the relevant branches |

---

## (f) Rejected or partly rejected critique points

- **#6 (partly):** "Nothing in the repo shows device trust exists" is not accurate. `accounts.TrustedDevice`, `DevicePairingCode`, `PersonalAccessToken.kind` and `manage.py revoke_all_devices` exist (Phase 4). The substance is accepted: `TrustedDevice` is cookie- and idle-expiry-shaped, so spike S9.0 must prove `kind=node` before Phase 9.
- **#3 (partly), hardware mute:** most Macs have no hardware mic switch, so a hardware mute cannot be a requirement. A software mute that fails closed, plus the macOS orange-dot indicator and our own indicator, is the requirement. An external mic with a physical switch is recommended to the owner, not required.
- **#9 (deferred, not rejected):** the gateway-relay design is fixed now (§B2), but the ADR-003 realtime amendment is written only when cloud live mode leaves the "later" list. Writing it earlier would govern code that does not exist.
- **#14 (mechanism changed):** "bind to a Unix socket or require a browser-proof header" is met with a Unix socket. Claude Code's HTTP hooks cannot target a Unix socket, so hooks use command hooks that call a signed CLI. There is no loopback HTTP receiver, so Origin/Host checks are moot.
- **#23 (partly):** the glossary and decision table are bilingual. The body stays English because all repo docs, ADRs and briefs are English. The executive summary stays Turkish for the owner.
- **#10 (numbers as initial targets):** the WER/FAR/FRR numbers are adopted as exit criteria, but they are explicitly re-settable after the first measurement. No public Turkish baseline exists for this exact stack, so fixed numbers without measurement would be invented precision.

---

## (g) Open decisions for the owner (recommended answers in bold)

1. **d1 Node shell technology.** **A Swift menu-bar app, with the Python voice process as a child (Phase 11).** It gives native TCC grants, Keychain, LocalAuthentication (Touch ID) and voice-processing AEC. Tauri v2 only if a Windows node is ever needed.
2. **d2 May audio go to OpenRouter or another cloud STT/TTS?** **Local by default. Each cloud audio provider is a separate owner toggle (`personal_data_allowed`), never before the wake word, and within the gateway audio cap (default 20 USD/month).** Needed at the **start of Phase 6**, because Turkish TTS will probably need OpenRouter if Piper fails the listening test.
3. **d3 Always-on wake word and cloud live mode.** **Push-to-talk until the Phase 11 false-activation budget is met; always-on after that, with the indicator and fail-closed mute. Cloud live mode (OpenAI Realtime / Gemini Live) stays on the "later" list; if revived, only through the gateway relay.**
4. **d4 Third-party agent terms.** **Before any driver is enabled, the owner checks: (a) whether the Claude subscription plan's terms allow automating Claude Code / the Agent SDK from Friday, or whether an API key is needed; (b) the same for Codex; (c) Antigravity's terms on wrapping `agy`.** Any driver whose terms are unclear stays off.
5. **d5 Default ingestion sources.** **On in 8A: git commits of registered repos, harness outcomes, Claude Code hook events, calendar metadata. On in 8B, after the per-directory grant: Claude/Codex transcripts (owner turns only). Opt-in: shell history, mail bodies, Obsidian Inbox. Never: screen content, screenshots, AX trees, keystrokes, browser history.**
6. **d6 Auto-approve learned memories?** **No.** At most 10 proposals per night, each with a quote of your own words. Changes to core blocks are approved one by one. Only `work_log` stays auto-approved (ADR-008). Review this after a month.
7. **d7 Node action classes.** **New ADR-013 (ADR-009 untouched): everything is gate-class (web approval + click on the Mac) except open/focus app (one click on the Mac) and read-only calls. Test/lint may become one-click only if the throwaway-worktree sandbox evidence is accepted. Dev servers stay gate-class.**
8. **d8 Workshop.** **Keep its number (7), defer it to the backlog after Phase 12.** Its lessons already live in Phases 5–6.
9. **d9 Apple Developer account and signing.** **Yes: enrol in the Apple Developer Program (99 USD/year) before Phase 9.** Developer ID signing and notarization are required for stable TCC grants and Keychain access. The signing key stays on the owner's Mac; CI never holds it.
10. **d10 Raw episode retention.** **30 days for personal sources, 14 days for firm workspaces. Derived memories keep only a provenance quote and the episode id. "Forget source X" is available from day one.**
