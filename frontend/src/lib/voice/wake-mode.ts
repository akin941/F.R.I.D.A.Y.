"use client";

import { CaptureError, type CaptureCallbacks, type CaptureSession } from "@/lib/voice/capture";
import {
  VOICE_WAKE_HANDOFF_MS,
  VOICE_WAKE_STORM_MAX,
  VOICE_WAKE_STORM_WINDOW_MS,
} from "@/lib/voice/config";
import type { MicStream, MicStreamCallbacks } from "@/lib/voice/mic-stream";
import { ModelIntegrityError, type VerifiedModels } from "@/lib/voice/model-loader";
import type { MuteState } from "@/lib/voice/mute";
import type { WakeEngineConfig, WakeInbound, WakeOutbound } from "@/lib/voice/wake-engine";
import { encodeWav } from "@/lib/voice/wav";

/**
 * Wake mode, page side (Phase 6 branch 4, D6/A6). Framework-free; the
 * shell-level `VoiceProvider` owns one instance per tab.
 *
 *   off ─arm(user gesture)→ loading (verify model hashes, start Worker, open mic) → armed
 *   armed ─detection→ capturing (the voice loop gets a capture session) → armed
 *   armed ⇄ paused while Friday speaks (half-duplex: frames are not even sent)
 *   any ─disarm (user, idle, logout/401, mute, wake storm, error)→ off
 *
 * What this module guarantees, mechanically:
 * - It never makes a network request. Audio reaches the network only when a
 *   detection has produced an utterance and the voice loop uploads it.
 * - Arming needs a user gesture and verified models; it is never persisted
 *   (a reload starts `off`).
 * - Detections while Friday speaks (or while a turn is running) are dropped.
 * - More than `VOICE_WAKE_STORM_MAX` activations within 60 s without a
 *   dispatched turn mute voice and disarm, with a notice (A6 wake storm).
 */

export type WakeState = "off" | "loading" | "armed" | "capturing" | "paused" | "error";

export type WakeNotice =
  | "idle_disarmed"
  | "auth_lost"
  | "storm_muted"
  | "muted"
  | "mute_unreadable"
  | "mic_ended"
  | "gesture_required"
  | null;

export interface WakeSnapshot {
  state: WakeState;
  /** Model / mic / worker error code while `error`. */
  error: string | null;
  notice: WakeNotice;
}

export interface WakeTurnOptions {
  startCapture(callbacks: CaptureCallbacks): Promise<CaptureSession>;
  detectedAt: number;
  /** The voice loop dispatched (routed) this turn: resets the storm counter. */
  onDispatched(): void;
}

/** The voice loop as wake mode sees it (implemented by `VoiceController`). */
export interface WakeTurnTarget {
  pressWake(options: WakeTurnOptions): Promise<void>;
  release(): Promise<void>;
  stop(): void;
  isSpeaking(): boolean;
  isBusy(): boolean;
}

export interface WakeInitMessage {
  type: "init";
  models: VerifiedModels;
  runtimePath: string;
  config: Partial<WakeEngineConfig>;
}

export interface WakePort {
  post(message: WakeInbound | WakeInitMessage, transfer?: Transferable[]): void;
  onMessage(handler: (message: WakeOutbound) => void): void;
  terminate(): void;
}

export interface UserGesture {
  type: string;
}

export interface WakeModeDeps {
  verifyModels(): Promise<VerifiedModels>;
  openStream(callbacks: MicStreamCallbacks): Promise<MicStream>;
  createPort(): WakePort;
  runtimePath: string;
  readMute(): MuteState;
  setMuted(muted: boolean): void;
  settings(): { threshold: number; idleMinutes: number };
  onSnapshot(snapshot: WakeSnapshot): void;
  /** A detection fired while no voice page is mounted (the provider navigates to /voice). */
  onNeedTarget?(): void;
  /** `navigator.userActivation.isActive` where supported. */
  userActivationActive?(): boolean | undefined;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

const GESTURE_TYPES = new Set(["click", "keydown", "keyup", "pointerup", "pointerdown", "touchend", "change"]);

function defaultUserActivation(): boolean | undefined {
  if (typeof navigator === "undefined") return undefined;
  const ua = (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation;
  return ua ? ua.isActive : undefined;
}

/** One detection's utterance, handed to the voice loop as a capture session. */
class WakeCapture {
  callbacks: CaptureCallbacks = {};
  consumed = false;
  done = false;
  private pcm: Int16Array | null = null;
  private waiters: ((pcm: Int16Array | null) => void)[] = [];
  private readonly port: WakePort;

  constructor(port: WakePort) {
    this.port = port;
  }

  settle(pcm: Int16Array | null) {
    if (this.done) return;
    this.done = true;
    this.pcm = pcm;
    for (const w of this.waiters) w(pcm);
    this.waiters = [];
  }

  private result(): Promise<Int16Array | null> {
    if (this.done) return Promise.resolve(this.pcm);
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  session(callbacks: CaptureCallbacks): CaptureSession {
    this.consumed = true;
    this.callbacks = callbacks;
    const count = () => this.pcm?.length ?? 0;
    return {
      get samples() {
        return count();
      },
      stop: async () => {
        if (!this.done) this.port.post({ type: "finish" });
        const pcm = await this.result();
        return pcm && pcm.length > 0 ? encodeWav(pcm) : null;
      },
      cancel: () => {
        if (!this.done) this.port.post({ type: "cancel" });
        this.settle(null);
      },
    };
  }
}

export class WakeMode {
  private readonly deps: WakeModeDeps;
  private state: WakeState = "off";
  private error: string | null = null;
  private notice: WakeNotice = null;
  private port: WakePort | null = null;
  private stream: MicStream | null = null;
  private target: WakeTurnTarget | null = null;
  private capture: WakeCapture | null = null;
  private activations: number[] = [];
  private idleTimer: unknown = null;
  private handoffTimer: unknown = null;
  private generation = 0;

  constructor(deps: WakeModeDeps) {
    this.deps = deps;
  }

  get snapshot(): WakeSnapshot {
    return { state: this.state, error: this.error, notice: this.notice };
  }

  /** The mic is open (armed, capturing or paused): the indicator is on. */
  get micOpen(): boolean {
    return this.state === "armed" || this.state === "capturing" || this.state === "paused";
  }

  private now() {
    return (this.deps.now ?? Date.now)();
  }

  private setTimer(fn: () => void, ms: number) {
    return (this.deps.setTimer ?? setTimeout)(fn, ms);
  }

  private clearTimer(handle: unknown) {
    if (handle !== null) (this.deps.clearTimer ?? clearTimeout)(handle as ReturnType<typeof setTimeout>);
  }

  private set(state: WakeState, extra: { error?: string | null; notice?: WakeNotice } = {}) {
    this.state = state;
    if ("error" in extra) this.error = extra.error ?? null;
    if ("notice" in extra) this.notice = extra.notice ?? null;
    this.deps.onSnapshot(this.snapshot);
  }

  /** The /voice page mounted (target) or unmounted (null). */
  registerTarget(target: WakeTurnTarget | null): void {
    this.target = target;
    const capture = this.capture;
    if (target && capture && !capture.consumed) {
      this.clearTimer(this.handoffTimer);
      this.handoffTimer = null;
      void this.startTurn(capture, this.now());
    }
  }

  /** The voice loop dispatched a turn: the storm counter starts over. */
  noteDispatched(): void {
    this.activations = [];
  }

  /** Arm from a user gesture only (D6; also the browser's AudioContext policy). */
  async arm(gesture: UserGesture | undefined): Promise<boolean> {
    if (this.state !== "off" && this.state !== "error") return false;
    const activation = (this.deps.userActivationActive ?? defaultUserActivation)();
    if (!gesture || !GESTURE_TYPES.has(gesture.type) || activation === false) {
      this.set(this.state, { notice: "gesture_required" });
      return false;
    }
    const mute = this.deps.readMute();
    if (mute.muted) {
      this.set("off", { notice: mute.readable ? "muted" : "mute_unreadable" });
      return false;
    }
    const generation = ++this.generation;
    this.set("loading", { error: null, notice: null });

    let models: VerifiedModels;
    try {
      models = await this.deps.verifyModels();
    } catch (err) {
      if (generation !== this.generation) return false;
      this.set("error", { error: err instanceof ModelIntegrityError ? err.code : "model_missing" });
      return false;
    }
    if (generation !== this.generation) return false;

    const port = this.deps.createPort();
    this.port = port;
    const ready = new Promise<string | null>((resolve) => {
      port.onMessage((message) => {
        if (message.type === "ready") resolve(null);
        else if (message.type === "error" && this.state === "loading") resolve("wake_failed");
        this.onWorkerMessage(port, message);
      });
    });
    const { threshold } = this.deps.settings();
    port.post(
      { type: "init", models, runtimePath: this.deps.runtimePath, config: { threshold } },
      Object.values(models),
    );
    const workerError = await ready;
    if (generation !== this.generation) return false;
    if (workerError) {
      this.teardown();
      this.set("error", { error: workerError });
      return false;
    }

    try {
      this.stream = await this.deps.openStream({
        onFrame: (samples, rms) => this.onFrame(port, samples, rms),
        onMuted: (reason) => this.disarm(reason === "unreadable" ? "mute_unreadable" : "muted"),
        onEnded: () => this.disarm("mic_ended"),
      });
    } catch (err) {
      if (generation !== this.generation) return false;
      this.teardown();
      if (err instanceof CaptureError && err.code === "muted") {
        this.set("off", { notice: "muted" });
      } else {
        this.set("error", { error: err instanceof CaptureError ? err.code : "mic_failed" });
      }
      return false;
    }
    if (generation !== this.generation) {
      this.stream.close();
      this.stream = null;
      return false;
    }
    this.activations = [];
    this.set("armed");
    this.resetIdleTimer();
    return true;
  }

  /** Close the mic, stop the Worker, drop any utterance in progress. */
  disarm(reason: Exclude<WakeNotice, "gesture_required"> | "user" = "user"): void {
    this.generation += 1;
    const wasActive = this.state !== "off";
    const capture = this.capture;
    this.teardown();
    if (capture && capture.consumed && !capture.done) this.target?.stop();
    capture?.settle(null);
    if (wasActive || reason !== "user") {
      this.set("off", { error: null, notice: reason === "user" ? null : reason });
    }
  }

  dispose(): void {
    this.disarm();
    this.target = null;
  }

  private teardown() {
    this.clearTimer(this.idleTimer);
    this.idleTimer = null;
    this.clearTimer(this.handoffTimer);
    this.handoffTimer = null;
    this.stream?.close();
    this.stream = null;
    this.port?.terminate();
    this.port = null;
    this.capture = null;
  }

  private resetIdleTimer() {
    this.clearTimer(this.idleTimer);
    const minutes = this.deps.settings().idleMinutes;
    this.idleTimer = this.setTimer(() => {
      this.idleTimer = null;
      this.disarm("idle_disarmed");
    }, minutes * 60_000);
  }

  private onFrame(port: WakePort, samples: Float32Array, rms: number) {
    if (port !== this.port) return;
    // Half-duplex (D7): while Friday speaks the detector gets nothing at all.
    const speaking = this.target?.isSpeaking() ?? false;
    if (speaking) {
      if (this.state === "armed") {
        port.post({ type: "pause" });
        this.set("paused");
      }
      return;
    }
    if (this.state === "paused") {
      port.post({ type: "resume" });
      this.set(this.capture ? "capturing" : "armed");
    }
    if (this.state !== "armed" && this.state !== "capturing") return;
    if (this.capture) this.capture.callbacks.onLevel?.(rms);
    const copy = samples.slice();
    port.post({ type: "frame", samples: copy }, [copy.buffer]);
  }

  private onWorkerMessage(port: WakePort, message: WakeOutbound) {
    if (port !== this.port) return;
    switch (message.type) {
      case "detected":
        this.onDetected(port);
        return;
      case "utterance":
        this.onUtteranceEnd(message.pcm);
        return;
      case "no_speech":
        this.onUtteranceEnd(null);
        return;
      case "error":
        if (this.state !== "loading") {
          this.teardown();
          this.set("error", { error: "wake_failed" });
        }
        return;
      default:
        return;
    }
  }

  private onDetected(port: WakePort) {
    const target = this.target;
    // Half-duplex / one turn at a time: a detection while Friday speaks,
    // while a turn runs, or while paused is dropped.
    if (this.state !== "armed" || this.capture || (target && (target.isSpeaking() || target.isBusy()))) {
      port.post({ type: "cancel" });
      return;
    }
    const now = this.now();
    this.activations = this.activations.filter((t) => now - t < VOICE_WAKE_STORM_WINDOW_MS);
    this.activations.push(now);
    if (this.activations.length > VOICE_WAKE_STORM_MAX) {
      // A6 wake storm: mute and disarm, with a notice. Nothing is uploaded.
      port.post({ type: "cancel" });
      this.deps.setMuted(true);
      this.disarm("storm_muted");
      return;
    }
    this.resetIdleTimer();
    const capture = new WakeCapture(port);
    this.capture = capture;
    this.set("capturing");
    if (target) {
      void this.startTurn(capture, now);
    } else {
      this.deps.onNeedTarget?.();
      this.handoffTimer = this.setTimer(() => {
        this.handoffTimer = null;
        if (this.capture === capture && !capture.consumed) this.dropCapture(capture);
      }, VOICE_WAKE_HANDOFF_MS);
    }
  }

  private async startTurn(capture: WakeCapture, detectedAt: number) {
    const target = this.target;
    if (!target) return;
    await target.pressWake({
      startCapture: async (callbacks) => capture.session(callbacks),
      detectedAt,
      onDispatched: () => this.noteDispatched(),
    });
    if (this.capture !== capture) return;
    if (!capture.consumed) {
      // The loop refused the turn (muted, busy): nothing is uploaded.
      this.dropCapture(capture);
      return;
    }
    if (capture.done) {
      this.capture = null;
      if (this.state === "capturing") this.set("armed");
      await target.release();
    }
  }

  private dropCapture(capture: WakeCapture) {
    if (!capture.done) this.port?.post({ type: "cancel" });
    capture.settle(null);
    if (this.capture === capture) {
      this.capture = null;
      if (this.state === "capturing") this.set("armed");
    }
  }

  private onUtteranceEnd(pcm: Int16Array | null) {
    const capture = this.capture;
    if (!capture) return;
    capture.settle(pcm);
    // Still waiting for the /voice page: kept until it registers (then
    // handed over) or the handoff timer drops it.
    if (!capture.consumed) return;
    this.capture = null;
    if (this.state === "capturing") this.set("armed");
    this.clearTimer(this.handoffTimer);
    this.handoffTimer = null;
    if (pcm && pcm.length > 0) void this.target?.release();
    else this.target?.stop(); // no speech: back to armed, nothing uploaded
  }
}
