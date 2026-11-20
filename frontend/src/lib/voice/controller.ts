"use client";

import { ApiError } from "@/lib/api-client";
import type { CaptureCallbacks, CaptureSession } from "@/lib/voice/capture";
import { CaptureError } from "@/lib/voice/capture";
import type { MuteState } from "@/lib/voice/mute";
import { Speaker } from "@/lib/voice/playback";
import {
  AFFIRMATION_REPLY,
  DRAFT_NOTICE,
  receiptSentence,
  spokenConfirmation,
  type ActionReceipt,
  type ApiUsageReceipt,
} from "@/lib/voice/receipts";
import { isInterruptWord, routeTranscript } from "@/lib/voice/routes";
import { SentenceSegmenter } from "@/lib/voice/segmenter";
import {
  SPOKEN_MEMORY_MS,
  matchesRecentSpeech,
  withinSelfTriggerWindow,
  type SpokenRecord,
} from "@/lib/voice/self-trigger";
import type { WakeTurnOptions, WakeTurnTarget } from "@/lib/voice/wake-mode";
import { TurnTimer, newTraceId, type StageTimings } from "@/lib/voice/trace";
import type { Transcript, TurnHandlers, TurnOutcome } from "@/lib/voice/api";
import {
  INITIAL_VOICE_STATE,
  voiceReducer,
  type VoiceEvent,
  type VoiceMachineState,
} from "@/lib/voice/voice-machine";
import type { ApiMemory, ChatStreamEvent } from "@/types/api";

/**
 * The push-to-talk loop (Phase 6 branch 3), framework-free so it can be
 * driven with fakes and fake timers:
 *
 *   press -> capture -> release -> transcribe -> finish window (A7) ->
 *   deterministic voice routes (A7) -> or the unchanged chat turn with
 *   `input_mode="voice"` -> sentence segmentation -> the single speak
 *   function -> playback.
 *
 * Voice-turn safety (ADR-015, D8): this loop can READ (the receipt) and
 * PROPOSE (a memory proposal, the chat turn's draft cards) -- it never
 * confirms, approves, rejects or executes anything. Draft cards are handed
 * to the view, which renders the same click-to-confirm cards as chat;
 * Friday only *says* a confirmation when the view hands back a receipt id
 * (`announce`). Nothing here maps a transcript to a click.
 */

export interface VoiceSnapshot extends VoiceMachineState {
  /** What was heard and dispatched for the current/last turn. */
  heard: string;
  /** The reply text streamed so far (always shown: captions). */
  reply: string;
  /** A transcript held for editing ("Göndermeden önce göster", Q5). */
  review: string | null;
  /** A non-fatal notice code/text (TTS failed, muted, …). */
  notice: string | null;
}

export interface VoiceControllerDeps {
  ensureConversation(): Promise<string | null>;
  workspaceId(): string | null;
  startCapture(callbacks: CaptureCallbacks): Promise<CaptureSession>;
  transcribe(conversationId: string, wav: Uint8Array<ArrayBuffer>, traceId: string, signal: AbortSignal): Promise<Transcript>;
  sendTurn(
    conversationId: string,
    content: string,
    options: { inputMode: "voice" | "text"; traceId: string | null; clientLocalDate: string },
    handlers: TurnHandlers,
    signal: AbortSignal,
  ): Promise<{ outcome: TurnOutcome; error?: ApiError | string }>;
  synthesize(
    conversationId: string,
    text: string,
    traceId: string,
    timings: StageTimings,
    signal: AbortSignal,
  ): Promise<ArrayBuffer>;
  fetchReceipt(conversationId: string, traceId: string | null): Promise<ApiUsageReceipt>;
  proposeMemory(workspaceId: string, content: string): Promise<ApiMemory>;
  setMuted(muted: boolean): void;
  readMute(): MuteState;
  createAudioContext?: () => AudioContext;
  clientLocalDate(): string;
  previewBeforeSend(): boolean;
  finishMs: number;
  onSnapshot(snapshot: VoiceSnapshot): void;
  onDraft?(event: Extract<ChatStreamEvent, { type: "draft" }>): void;
  onMemoryProposal?(memory: ApiMemory): void;
  /** The chat turn finished (refresh history, etc.). */
  onTurnSettled?(outcome: TurnOutcome): void;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

function errorCode(err: unknown, fallback: string): string {
  if (err instanceof CaptureError) return err.code;
  if (err instanceof ApiError) return err.code;
  return fallback;
}

/** The wake detection behind the current turn (branch 4), if any. */
interface WakeTurn {
  detectedAt: number;
  /** Detected within 1.5 s after playback ended (A6 self-trigger window). */
  suspect: boolean;
  onDispatched(): void;
}

export class VoiceController implements WakeTurnTarget {
  private readonly deps: VoiceControllerDeps;
  private state: VoiceMachineState = INITIAL_VOICE_STATE;
  private heard = "";
  private reply = "";
  private review: string | null = null;
  private notice: string | null = null;
  private capture: CaptureSession | null = null;
  private capturing: Promise<CaptureSession | null> | null = null;
  private micLevel = 0;
  private finishTimer: unknown = null;
  private traceId: string | null = null;
  /** The trace of the last dispatched brain turn ("bu ne kadar tuttu?"). */
  private lastTurnTrace: string | null = null;
  private timer: TurnTimer;
  private turnAbort: AbortController | null = null;
  private sttAbort: AbortController | null = null;
  private speaker: Speaker;
  private speakReply = false;
  private streaming = false;
  private disposed = false;
  private conversationId: string | null = null;
  private wakeTurn: WakeTurn | null = null;
  private playbackEndedAt: number | null = null;
  private spoken: SpokenRecord[] = [];

  constructor(deps: VoiceControllerDeps) {
    this.deps = deps;
    this.timer = new TurnTimer(deps.now);
    this.speaker = new Speaker({
      synthesize: (text, signal) => this.synthesizeSentence(text, signal),
      createAudioContext: deps.createAudioContext,
      readMute: deps.readMute,
      onPlaybackStart: () => {
        this.timer.mark("playbackStart");
        this.send({ type: "speaking" });
      },
      onIdle: () => {
        this.playbackEndedAt = this.clock();
        if (!this.streaming) this.send({ type: "done" });
      },
      onError: () => this.setNotice("voice.tts_failed"),
    });
  }

  // --- public API ------------------------------------------------------

  get snapshot(): VoiceSnapshot {
    return {
      ...this.state,
      heard: this.heard,
      reply: this.reply,
      review: this.review,
      notice: this.notice,
    };
  }

  /** Orb level 0..1: mic RMS while listening, playback level while speaking. */
  level(): number {
    if (this.state.phase === "listening") return Math.min(1, this.micLevel * 4);
    if (this.state.phase === "speaking") return this.speaker.level();
    return 0;
  }

  /** Friday is speaking (or about to): wake detection pauses (half-duplex, D7). */
  isSpeaking(): boolean {
    return this.speaker.busy || this.state.phase === "speaking";
  }

  /** A turn is in progress (a wake detection is then ignored). */
  isBusy(): boolean {
    const phase = this.state.phase;
    return phase !== "idle" && phase !== "error";
  }

  /**
   * A wake detection starts a turn (branch 4): the capture is the wake
   * Worker's utterance (it starts after the detection frame, D6), and wake
   * mode calls `release()` when the VAD ends it.
   */
  async pressWake(options: WakeTurnOptions): Promise<void> {
    if (this.disposed || this.isBusy() || this.isSpeaking()) return;
    this.wakeTurn = {
      detectedAt: options.detectedAt,
      suspect: withinSelfTriggerWindow(options.detectedAt, this.playbackEndedAt),
      onDispatched: options.onDispatched,
    };
    await this.beginCapture(options.startCapture);
  }

  /** Mic button down / Space down / a toggle click while idle. */
  async press(): Promise<void> {
    const current = this.state.phase;
    if (current === "listening" || current === "transcribing") return;
    // A manual press is not a wake turn (even one continuing in the finish window).
    this.wakeTurn = null;
    await this.beginCapture(this.deps.startCapture);
  }

  private async beginCapture(start: VoiceControllerDeps["startCapture"]): Promise<void> {
    if (this.disposed) return;
    const phase = this.state.phase;
    if (phase === "listening" || phase === "transcribing") return;
    const mute = this.deps.readMute();
    if (mute.muted) {
      this.setNotice(mute.readable ? "muted" : "mute_unreadable");
      return;
    }
    const continuing = phase === "finishing";
    this.clearFinishTimer();
    if (phase === "thinking" || phase === "speaking") {
      // Half-duplex (D7): the owner talks, Friday stops talking. The reply
      // text keeps streaming onto the screen, unspoken.
      this.speakReply = false;
      this.speaker.stop();
    }
    if (!continuing) {
      this.traceId = newTraceId();
      this.timer = new TurnTimer(this.deps.now);
    }
    this.notice = null;
    this.review = null;
    this.send({ type: "press" });
    const pending = start({
        onLevel: (level) => {
          this.micLevel = level;
        },
        onCapReached: () => void this.release(),
        onMuted: (reason) => {
          // A6 fail-closed: the capture path already released the track.
          this.capture = null;
          this.send({ type: "abort" });
          this.setNotice(reason === "unreadable" ? "mute_unreadable" : "muted");
        },
      })
      .then(
        (session) => {
          if (this.state.phase !== "listening" && this.state.phase !== "transcribing") {
            session.cancel();
            return null;
          }
          this.capture = session;
          return session;
        },
        (err) => {
          this.send({ type: "fail", error: errorCode(err, "mic_failed") });
          return null;
        },
      );
    this.capturing = pending;
    await pending;
  }

  /** Mic button up / Space up / the second toggle click. */
  async release(): Promise<void> {
    if (this.state.phase !== "listening") return;
    this.send({ type: "release" });
    this.timer.mark("released", true);
    const session = this.capture ?? (await this.capturing);
    this.capture = null;
    this.micLevel = 0;
    const wav = session ? await session.stop() : null;
    if (this.phase() !== "transcribing") return;
    if (!wav) {
      this.afterTranscript("");
      return;
    }
    const conversationId = await this.conversation();
    if (!conversationId) return;
    const controller = new AbortController();
    this.sttAbort = controller;
    let transcript: Transcript;
    try {
      transcript = await this.deps.transcribe(conversationId, wav, this.traceId!, controller.signal);
    } catch (err) {
      if (controller.signal.aborted || this.phase() !== "transcribing") return;
      this.send({ type: "fail", error: errorCode(err, "voice.stt_failed") });
      return;
    } finally {
      if (this.sttAbort === controller) this.sttAbort = null;
    }
    if (this.phase() !== "transcribing") return;
    this.timer.mark("transcribed", true);
    this.afterTranscript(transcript.text ?? "");
  }

  /** "Durdur" / Escape: stop everything now, nothing more is sent. */
  stop(): void {
    this.wakeTurn = null;
    this.clearFinishTimer();
    this.capture?.cancel();
    this.capture = null;
    void this.capturing?.then((session) => session?.cancel());
    this.sttAbort?.abort();
    this.speakReply = false;
    this.speaker.stop();
    this.turnAbort?.abort();
    this.turnAbort = null;
    this.streaming = false;
    this.send({ type: "abort" });
  }

  /** The mute flag changed (shell toggle, another tab, `?mute=1`). */
  muteChanged(state: MuteState): void {
    if (!state.muted) return;
    this.speaker.stop("muted");
    if (this.capture || this.state.phase === "listening") {
      this.capture?.cancel();
      this.capture = null;
      void this.capturing?.then((session) => session?.cancel());
      this.send({ type: "abort" });
    }
    this.setNotice(state.readable ? "muted" : "mute_unreadable");
  }

  /** Send text (typed, or an edited voice transcript under review). */
  async sendText(text: string, inputMode: "voice" | "text"): Promise<void> {
    const content = text.trim();
    if (!content || this.streaming) return;
    this.review = null;
    if (inputMode === "text") {
      this.traceId = null;
    } else if (!this.traceId) {
      this.traceId = newTraceId();
      this.timer = new TurnTimer(this.deps.now);
    }
    await this.dispatchBrain(content, inputMode);
  }

  /**
   * Say a confirmation built from a receipt (A7 "a yes is not a done"):
   * spoken only when the receipt carries the created row's id.
   */
  announce(receipt: ActionReceipt): boolean {
    const sentence = spokenConfirmation(receipt);
    if (!sentence) return false;
    return this.say(sentence);
  }

  dispose(): void {
    this.disposed = true;
    this.stop();
    this.speaker.dispose();
  }

  // --- internals ---------------------------------------------------------

  /** The current phase (a method, so TS does not narrow across awaits). */
  private phase() {
    return this.state.phase;
  }

  private send(event: VoiceEvent) {
    const next = voiceReducer(this.state, event);
    if (next !== this.state) {
      this.state = next;
      this.emit();
    }
  }

  private emit() {
    if (!this.disposed) this.deps.onSnapshot(this.snapshot);
  }

  private setNotice(notice: string | null) {
    this.notice = notice;
    this.emit();
  }

  private clearFinishTimer() {
    if (this.finishTimer !== null) {
      (this.deps.clearTimer ?? clearTimeout)(this.finishTimer as ReturnType<typeof setTimeout>);
      this.finishTimer = null;
    }
  }

  private async conversation(): Promise<string | null> {
    if (this.conversationId) return this.conversationId;
    const id = await this.deps.ensureConversation();
    if (!id) {
      this.send({ type: "fail", error: "conversation_failed" });
      return null;
    }
    this.conversationId = id;
    return id;
  }

  /** The conversation was replaced (workspace switch, new session). */
  resetConversation(): void {
    this.stop();
    this.conversationId = null;
    this.lastTurnTrace = null;
  }

  private afterTranscript(text: string) {
    // A7: an interrupt word bypasses the finish window and the buffer --
    // playback stops, nothing is dispatched, no request is made.
    if (text && isInterruptWord(text)) {
      this.speakReply = false;
      this.speaker.stop();
      this.send({ type: "routed" });
      return;
    }
    // A6 self-trigger rule: a wake turn detected right after playback whose
    // transcript repeats what Friday just said is Friday hearing itself.
    const wake = this.wakeTurn;
    if (text && wake?.suspect && matchesRecentSpeech(text, this.recentSpoken())) {
      this.wakeTurn = null;
      this.send({ type: "routed" });
      this.setNotice("self_trigger");
      return;
    }
    this.send({ type: "transcript", text });
    if (this.state.phase !== "finishing") {
      this.wakeTurn = null;
      return;
    }
    // Wake mode: the VAD silence window (= VOICE_FINISH_MS, A7) already was
    // the finish window, so the turn is routed right away.
    const wait = wake ? 0 : this.deps.finishMs;
    this.finishTimer = (this.deps.setTimer ?? setTimeout)(() => {
      this.finishTimer = null;
      void this.finish();
    }, wait);
  }

  private clock(): number {
    return (this.deps.now ?? Date.now)();
  }

  private recentSpoken(): SpokenRecord[] {
    const now = this.clock();
    this.spoken = this.spoken.filter((r) => now - r.at <= SPOKEN_MEMORY_MS);
    return this.spoken;
  }

  /** Remember what Friday says (self-trigger comparison); text only, in memory. */
  private noteSpoken(text: string) {
    this.recentSpoken().push({ text, at: this.clock() });
    if (this.spoken.length > 20) this.spoken.shift();
  }

  /** The finish window elapsed: route the whole buffered utterance. */
  private async finish(): Promise<void> {
    if (this.state.phase !== "finishing") return;
    const text = this.state.buffer;
    this.heard = text;
    const route = routeTranscript(text);
    const wake = this.wakeTurn;
    this.wakeTurn = null;
    if (wake && text.trim()) wake.onDispatched();
    switch (route.kind) {
      case "interrupt":
        this.speaker.stop();
        this.send({ type: "routed" });
        return;
      case "mute":
        this.send({ type: "routed" });
        this.deps.setMuted(true);
        this.muteChanged({ muted: true, readable: true });
        return;
      case "affirmation":
        // D8: a spoken yes confirms nothing. Fixed reply, no action.
        this.send({ type: "routed" });
        this.say(AFFIRMATION_REPLY);
        return;
      case "receipt":
        await this.sayReceipt();
        return;
      case "remember":
        await this.remember(route.content);
        return;
      case "brain":
        if (!route.text) {
          this.send({ type: "routed" });
          return;
        }
        if (this.deps.previewBeforeSend()) {
          this.review = route.text;
          this.send({ type: "routed" });
          this.emit();
          return;
        }
        await this.dispatchBrain(route.text, "voice");
    }
  }

  private async sayReceipt() {
    this.send({ type: "routed" });
    const conversationId = await this.conversation();
    if (!conversationId) return;
    try {
      const receipt = await this.deps.fetchReceipt(conversationId, this.lastTurnTrace);
      this.reply = receiptSentence(receipt);
      this.emit();
      this.say(this.reply);
    } catch (err) {
      this.setNotice(errorCode(err, "receipt_failed"));
    }
  }

  private async remember(content: string) {
    this.send({ type: "routed" });
    const workspaceId = this.deps.workspaceId();
    if (!workspaceId) return;
    try {
      const memory = await this.deps.proposeMemory(workspaceId, content);
      this.deps.onMemoryProposal?.(memory);
      // Spoken only with the proposal's id in hand (A7 receipts-only).
      const sentence = spokenConfirmation({ kind: "memory_proposal", memoryId: memory.id });
      if (sentence) {
        this.reply = sentence;
        this.emit();
        this.say(sentence);
      }
    } catch (err) {
      this.setNotice(errorCode(err, "memory_proposal_failed"));
    }
  }

  /** One fixed sentence through the single speak function. */
  private say(sentence: string): boolean {
    if (!this.traceId) this.traceId = newTraceId();
    const spoken = this.speaker.speak(sentence);
    if (spoken) this.noteSpoken(sentence);
    if (spoken) this.speaker.end();
    return spoken;
  }

  private async dispatchBrain(text: string, inputMode: "voice" | "text") {
    const conversationId = await this.conversation();
    if (!conversationId) return;
    this.heard = text;
    this.reply = "";
    this.timer.mark("dispatched", true);
    this.send({ type: "dispatch" });
    // One turn at a time: a newer turn supersedes a still-streaming one.
    this.turnAbort?.abort();
    const controller = new AbortController();
    this.turnAbort = controller;
    this.streaming = true;
    this.speakReply = inputMode === "voice";
    const traceId = inputMode === "voice" ? this.traceId : null;
    if (traceId) this.lastTurnTrace = traceId;
    const segmenter = new SentenceSegmenter();
    const speakAll = (sentences: string[]) => {
      for (const sentence of sentences) {
        if (!this.speakReply) return;
        this.timer.mark("firstSentence");
        if (this.speaker.speak(sentence)) this.noteSpoken(sentence);
      }
    };
    const result = await this.deps.sendTurn(
      conversationId,
      text,
      { inputMode, traceId, clientLocalDate: this.deps.clientLocalDate() },
      {
        onChunk: (chunk) => {
          if (this.turnAbort !== controller) return;
          this.reply += chunk;
          this.emit();
          speakAll(segmenter.push(chunk));
        },
        onEvent: (event) => {
          if (event.type !== "draft" || this.turnAbort !== controller) return;
          // A proposal card on screen; never confirmed from here (D8).
          this.deps.onDraft?.(event);
          if (this.speakReply) this.speaker.speak(DRAFT_NOTICE[event.kind]);
        },
      },
      controller.signal,
    );
    if (this.turnAbort !== controller) {
      // Superseded by a newer turn (or stopped): that turn owns the state.
      this.deps.onTurnSettled?.(result.outcome);
      return;
    }
    this.turnAbort = null;
    this.streaming = false;
    if (result.outcome === "done" || result.outcome === "error") speakAll(segmenter.flush());
    this.deps.onTurnSettled?.(result.outcome);
    switch (result.outcome) {
      case "done":
        this.speaker.end();
        if (!this.speaker.busy) this.send({ type: "done" });
        return;
      case "aborted":
        this.speaker.stop();
        this.send({ type: "abort" });
        return;
      case "error":
        this.speaker.stop();
        this.send({ type: "fail", error: typeof result.error === "string" ? result.error : "stream_error" });
        return;
      case "rejected":
        this.speaker.stop();
        this.send({ type: "fail", error: errorCode(result.error, "send_failed") });
        return;
      case "disconnected":
        // A6 fail-closed: the turn connection dropped -> no open mic.
        this.speaker.stop();
        this.capture?.cancel();
        this.capture = null;
        this.send({ type: "fail", error: "disconnected" });
        return;
    }
  }

  private synthesizeSentence(text: string, signal: AbortSignal): Promise<ArrayBuffer> {
    const conversationId = this.conversationId;
    if (!conversationId) return Promise.reject(new Error("no conversation"));
    this.timer.mark("lastRequest", true);
    return this.deps.synthesize(conversationId, text, this.traceId ?? newTraceId(), this.timer.timings(), signal);
  }
}
