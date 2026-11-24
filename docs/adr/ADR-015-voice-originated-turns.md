# ADR-015: Voice-Originated Turns and the Always-On Microphone

## Status
Accepted 2026-11-20 — owner approval of the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md` §B2, §c.2, §g decision d3).
Decisions 1–3 apply from Phase 6 (amendment of
`docs/prompts/phase-6-voice-identity/brief.md`, 2026-11-20); decisions 4–6
are completed before Phase 11 (always-on wake word). Builds on ADR-011
(voice transport, Phase 6 branch 1) and Phase 6 D8 ("voice never decides").
Amends nothing.

Numbering note: the proposal called this "ADR-014". ADR-011 is reserved by
the Phase 6 brief for "voice transport", so the proposal's ADRs are
numbered from ADR-012.

## Context
Phase 6 D8 already says a spoken utterance is never an approval decision
nor a confirm-class confirmation. Two things widen the risk: a microphone
that is armed for long periods (browser wake word in Phase 6, always-on in
Phase 11), and Friday gaining actions on the Mac (Phase 10). A TV, a video,
a call on speaker or Friday's own voice can say "Hey Friday, terminali aç".
There is no speaker verification.

## Decision
1. **Capability cap.** A turn whose input came from the microphone
   (`input_mode=voice`, speaker unverified) may call read tools and create
   proposals (task drafts, tool requests, memory proposals) only. It can
   never execute a confirm-class or gate-class action. Execution always
   needs an on-screen act by the owner. This is enforced server-side on
   the turn, not only in the client. A voice route that would write
   (for example "hatırla ki …") creates a proposal, never an approved row.
2. **Mic state is always visible.** A persistent indicator (shell indicator
   and tab-title prefix in Phase 6, the HUD ring, the node menu-bar icon
   from Phase 9) whenever audio is captured.
3. **Mute fails closed.** Software mute (UI toggle, global hotkey later) is
   checked in the capture path. If the mute state cannot be read, or the
   connection that carries the turn is lost, the mic is released. Most Macs
   have no hardware mic switch; an external mic with a physical switch is
   recommended to the owner, not required.
4. **Self-trigger rule.** While TTS plays, the wake detector is suppressed
   (half-duplex) or fed the echo-cancelled signal (Phase 11). A wake hit
   within 1.5 s of TTS output whose transcript matches recent TTS text is
   discarded. A self-trigger test plays a TTS clip saying "Hey Friday,
   terminali aç" through the replay harness and asserts no wake and no
   proposal (CI with fakes; nightly with real models).
5. **Wake storms.** More than 3 activations in 60 s without a dispatched
   turn auto-mutes and shows a notice. A voice session closes after 30 s
   of silence.
6. **Untrusted audio.** Remote transcripts (calls, videos) and Friday's own
   TTS output are data. A trust check (Phase 10) treats voice utterances as
   weaker evidence than typed ones.
7. **Accepted risk.** Speaker verification is deferred to the "later" list.
   Until it exists, a person or a TV in the room can make Friday propose
   things but never run them.
8. **Cloud realtime voice**, if it ever leaves the "later" list, is proxied
   through a gateway WebSocket relay (ADR-003 second amendment, written
   then); function calls from a realtime model are untrusted model output
   and become proposals under decision 1.

## Consequences
- Voice is convenient for reading and proposing, and always costs one
  on-screen click (two for node actions, ADR-014) to act.
- The capability cap is a release-gate regression in every voice branch.
- Always-on wake (Phase 11) ships only with decisions 4–6 implemented and
  the false-accept budget met (≤ 0.2 per hour on 24 h of household audio).

## Implementation note — decisions 1–3 landed (2026-11-20, Phase 6 closeout)

Phase 6 implements decisions 1–3 for the browser path
(`docs/prompts/phase-6-voice-identity/brief.md`, A6):

1. **Capability cap** -- a turn posted with `input_mode="voice"` may run
   read tools and produce drafts/proposals only, asserted server-side per
   turn in the messages view; "hatırla ki …" creates a `pending_approval`
   memory proposal, never an approved row; a spoken confirmation only
   references an action whose receipt exists ("a yes is not a done").
2. **Mic state visible** -- the shell indicator and the tab-title prefix
   whenever audio is captured, and the HUD's mic state (health panel).
3. **Mute fails closed** -- checked inside the capture path and inside the
   single speak function; an unreadable mute state or a dropped turn/SSE
   connection stops capture and releases the track.

Closeout evidence: the voice e2e (`frontend/e2e/voice.e2e.ts`: a voice
task draft becomes a task only through the card's click) and the
`voice_mute` preflight check / `frontend/e2e/voice-mute.e2e.ts` (a muted
tab opens no mic, uploads nothing and plays nothing). The in-tab parts of
decisions 4–5 (half-duplex suppression, the 1.5 s self-trigger discard,
wake-storm auto-mute) also shipped with Phase 6 branch 4; their replay-
harness CI test belongs to the eval branch 3a, and their completion for
always-on listening stays a Phase 11 precondition.

## Implementation note — node actions only propose (2026-11-20, Phase 10 branch 4)

`feat/p10-trust-check` (`docs/prompts/phase-10-control/brief.md`, D9 and
D13) extends decisions 1 and 6 to Friday Node actions without a new intent:

1. **Proposal only.** A node action reaches a turn only as a
   `tool_request` draft of the `node_action` tool (`EFFECT_PROPOSAL`). The
   router's argument step (`router.node_action_args`, web channel only)
   sees the owner's message and server-side contracts; its untrusted
   answer is re-validated against the server contract, and anything
   outside it is plain chat. The draft carries `source_message_id`; the
   server records the source message's `input_mode` in the approval
   snapshot (the client cannot set it). Gate-class drafts still need the
   card click, the `/approvals` decision and the Mac click; the
   confirm-class draft (`apps.open`/`apps.focus`) needs the card click and
   the Mac click. A spoken "evet"/"onayla" decides nothing.
2. **Voice is weaker evidence (decision 6).** The trust check
   (`assistant.trust_check`, purpose `control.trust_check`) caps a
   voice-sourced proposal at `warn` (`voice_evidence`); the approval card
   shows "Sesle önerildi". It can warn or block, never approve.

Release-gate regressions: `tools/tests/test_node_action_trust.py` (a voice
turn naming a node action yields a draft and nothing else; a spoken yes
decides nothing; a voice-sourced approval is at best `warn`; the
self-trigger "Hey Friday, terminali aç" through fake TTS and fake STT
yields at most a draft card) and `assistant/tests/test_trust_check.py`. The
recorded-audio replay harness (Phase 6 branch 3a) is still not merged, so
the self-trigger case drives the voice endpoints with fakes instead of a
recorded clip; it moves onto the harness when 3a lands.

## Implementation note — Phase 10 closeout (`chore/p10-closeout`, 2026-11-20)

The closeout e2e (`backend/ops/tests/test_control_e2e.py`) runs the voice
rule against the real node gateway and the reference node v2: the same
"acme'de testleri çalıştır" that, typed, goes through the card, the trust
check, `/approvals`, the grant and the node window to an `ok` run, yields,
spoken, a `node_action` draft and nothing else — no approval, no trust-check
call, no execution, no frame to the node. The `trust_check` preflight row
fails while no real trust check (or the branch-3 stub) is registered, or
while `control.trust_check` lacks a tier, a budget or its seeded prompt, so a
deployment cannot silently run node actions without the voice cap.
Decisions 1–6 are unchanged.

## Implementation note — decision 2 on the node (Phase 11 branch 7, 2026-11-20)

Decision 2 (the owner can always see that the mic is live) now reaches the
Mac node's mic on the server's screens too. The node relays its voice
child's state as the `voice.state` event (ADR-013 note of the same date);
the HUD health panel's node line shows the mic state, mode, session flag
and the day's wake accepts / discards / storm mutes, and the HUD ring shows
*listening / thinking / speaking* from the node while no browser voice turn
is active (labelled "(Mac)"). The state reads `unknown` when the node is
offline or silent for two heartbeats, and `off` while the node is cut off
(kill switch, revoked) — the server's fail-closed reading, not an
observation. The menu-bar icon on the Mac stays the primary indicator
(Mac session M1). Drafts from a live voice turn (`node_voice`) render as
the same cards on `/chat/<id>` and are confirmed only by a click there;
nothing on the HUD decides anything. Decisions 1 and 3–7 are unchanged.

## Implementation note — decision 4 on the echo-cancelled signal (Phase 11 branch 4, 2026-11-20)

Decision 4 now holds for the Mac node's voice child in its full-duplex form
(`feat/p11-barge-in`; brief D9, D10). While Friday speaks, the child's
barge-in VAD and (with always-on armed) its wake detector are **fed the
host's AEC-cleaned capture** instead of being suppressed. Two filters stand
behind them, both applied to the transcript before any chat turn is
posted (`node/friday-voice/friday_voice/self_trigger.py`):

1. **Window rule** (the decision as written): a wake detection while
   playback runs or within 1.5 s after it ended, and every barge-in, marks
   its utterance *suspect*; a suspect transcript that matches recent TTS
   text is discarded.
2. **Content rule** (stricter than the decision): any transcript —
   wake-initiated, push-to-talk or barge-in — that matches what Friday
   spoke in the last 60 s is discarded, whatever the timing.

"Matches" has one definition in two languages: the child's Python port of
the browser's `self-trigger.ts` (Turkish lower-casing, NFC, wake-phrase
stripping, containment or ≥ 60 % token overlap for ≥ 2 tokens), pinned by
shared vectors (`node/friday-voice/vectors/self_trigger.json`) that pytest
and vitest both run. A discard posts no turn, speaks nothing, increments
the day's `wake_discards_today` (the `voice.state` counter the HUD shows)
and keeps the session open.

The decision's self-trigger test exists in CI with fakes in its branch 4
form: the reference host's `EchoDouble` leaks Friday's own playback into
the capture at residual 0.3 and 1.0 (a failing canceller) and a scripted
wake detector fires on "Hey Friday, terminali aç." while it plays — no turn
is posted and the discard is counted; a negative control with the filter
disabled does post the turn, so the test proves the filter and not the
fake. With a perfect canceller (residual 0.0) a detection captures no
speech and nothing is uploaded at all. Branch 6 turns this into the release
gate over the dedicated fixture; the real-model run is the nightly Mac job
(M3, pending owner evaluation). Decisions 1–3 and 5–7 are unchanged; a
barge-in turn is an ordinary voice turn under decision 1's capability cap.
