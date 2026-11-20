import {
  VOICE_MAX_UTTERANCE_SECONDS,
  VOICE_SAMPLE_RATE,
  VOICE_VAD_THRESHOLD,
  VOICE_WAKE_DEFAULT_THRESHOLD,
  VOICE_WAKE_NO_SPEECH_MS,
  VOICE_WAKE_SILENCE_MS,
} from "@/lib/voice/config";
import type { VadEngine } from "@/lib/voice/vad";
import { floatToPcm16, concatPcm } from "@/lib/voice/wav";
import type { WakeDetector } from "@/lib/voice/wakeword/openwakeword";

/**
 * The wake-mode capture state machine (Phase 6 branch 4, D6), the part that
 * runs inside the Worker. Pure and framework-free: the Worker wraps it with
 * real ONNX sessions, tests drive it with a mocked detector and VAD.
 *
 *   armed ─score ≥ threshold on frame k→ capturing (utterance starts at frame k+1)
 *   capturing ─VAD silence ≥ VOICE_FINISH_MS after speech | 30 s cap | finish→ utterance → armed
 *   capturing ─no speech within 5 s | cancel→ no_speech (nothing leaves) → armed
 *   paused (Friday speaks, half-duplex): frames dropped, detector reset
 *
 * The privacy invariant (D6): pre-wake audio is only ever handed to the
 * detector (whose buffers are bounded, see openwakeword.ts) and is never
 * posted out. The only audio that leaves the engine is an `utterance`, and
 * its first sample is the first sample of the frame AFTER the detection
 * frame -- no pre-roll, not even the wake phrase itself.
 */

export interface WakeEngineConfig {
  threshold: number;
  silenceMs: number;
  noSpeechMs: number;
  maxSamples: number;
  vadThreshold: number;
}

export const DEFAULT_WAKE_ENGINE_CONFIG: WakeEngineConfig = {
  threshold: VOICE_WAKE_DEFAULT_THRESHOLD,
  silenceMs: VOICE_WAKE_SILENCE_MS,
  noSpeechMs: VOICE_WAKE_NO_SPEECH_MS,
  maxSamples: VOICE_MAX_UTTERANCE_SECONDS * VOICE_SAMPLE_RATE,
  vadThreshold: VOICE_VAD_THRESHOLD,
};

export type WakeInbound =
  | { type: "configure"; config: Partial<WakeEngineConfig> }
  | { type: "frame"; samples: Float32Array }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "cancel" }
  | { type: "finish" };

export type UtteranceEndReason = "silence" | "cap" | "finish";

export type WakeOutbound =
  | { type: "ready" }
  | { type: "detected"; frame: number; score: number; startSample: number }
  | { type: "utterance"; pcm: Int16Array; startSample: number; reason: UtteranceEndReason }
  | { type: "no_speech" }
  | { type: "error"; message: string };

type Mode = "armed" | "capturing" | "paused";

export class WakeEngine {
  private readonly detector: WakeDetector;
  private readonly vad: VadEngine;
  private readonly post: (message: WakeOutbound) => void;
  private config: WakeEngineConfig;
  private mode: Mode = "armed";
  private chain: Promise<void> = Promise.resolve();
  private frameIndex = 0;
  private sampleIndex = 0;
  private chunks: Int16Array[] = [];
  private captured = 0;
  private utteranceStart = 0;
  private heardSpeech = false;
  private silence = 0;

  constructor(
    detector: WakeDetector,
    vad: VadEngine,
    post: (message: WakeOutbound) => void,
    config: Partial<WakeEngineConfig> = {},
  ) {
    this.detector = detector;
    this.vad = vad;
    this.post = post;
    this.config = { ...DEFAULT_WAKE_ENGINE_CONFIG, ...config };
  }

  get state(): Mode {
    return this.mode;
  }

  /** Messages are handled strictly in order (frames are never reordered). */
  handle(message: WakeInbound): Promise<void> {
    this.chain = this.chain.then(() => this.dispatch(message)).catch((err: unknown) => {
      this.post({ type: "error", message: err instanceof Error ? err.message : String(err) });
    });
    return this.chain;
  }

  /** Resolves once every queued message has been handled. */
  idle(): Promise<void> {
    return this.chain;
  }

  private async dispatch(message: WakeInbound): Promise<void> {
    switch (message.type) {
      case "configure":
        this.config = { ...this.config, ...message.config };
        return;
      case "pause":
        this.dropUtterance();
        this.mode = "paused";
        this.detector.reset();
        return;
      case "resume":
        if (this.mode === "paused") {
          this.detector.reset();
          this.mode = "armed";
        }
        return;
      case "cancel":
        if (this.mode === "capturing") {
          this.dropUtterance();
          this.backToArmed();
        }
        return;
      case "finish":
        if (this.mode !== "capturing") return;
        if (this.heardSpeech && this.captured > 0) this.emitUtterance("finish");
        else this.emitNoSpeech();
        return;
      case "frame":
        await this.frame(message.samples);
        return;
    }
  }

  private async frame(samples: Float32Array): Promise<void> {
    const index = this.frameIndex;
    this.frameIndex += 1;
    this.sampleIndex += samples.length;
    if (this.mode === "paused") return; // half-duplex: Friday is speaking
    if (this.mode === "armed") {
      const score = await this.detector.process(samples);
      if (score >= this.config.threshold) {
        this.mode = "capturing";
        this.chunks = [];
        this.captured = 0;
        this.heardSpeech = false;
        this.silence = 0;
        // No pre-roll: the utterance starts at the first sample after this frame.
        this.utteranceStart = this.sampleIndex;
        this.vad.reset();
        this.post({ type: "detected", frame: index, score, startSample: this.utteranceStart });
      }
      return;
    }
    // capturing
    const room = this.config.maxSamples - this.captured;
    const piece = samples.length > room ? samples.subarray(0, room) : samples;
    this.chunks.push(floatToPcm16(piece));
    this.captured += piece.length;
    const prob = await this.vad.process(piece);
    if (prob >= this.config.vadThreshold) {
      this.heardSpeech = true;
      this.silence = 0;
    } else {
      this.silence += piece.length;
    }
    const msToSamples = (ms: number) => (ms * VOICE_SAMPLE_RATE) / 1000;
    if (this.captured >= this.config.maxSamples) {
      if (this.heardSpeech) this.emitUtterance("cap");
      else this.emitNoSpeech();
    } else if (this.heardSpeech && this.silence >= msToSamples(this.config.silenceMs)) {
      this.emitUtterance("silence");
    } else if (!this.heardSpeech && this.captured >= msToSamples(this.config.noSpeechMs)) {
      this.emitNoSpeech();
    }
  }

  private emitUtterance(reason: UtteranceEndReason) {
    const pcm = concatPcm(this.chunks);
    const startSample = this.utteranceStart;
    this.chunks = [];
    this.backToArmed();
    this.post({ type: "utterance", pcm, startSample, reason });
  }

  private emitNoSpeech() {
    this.dropUtterance();
    this.backToArmed();
    this.post({ type: "no_speech" });
  }

  private dropUtterance() {
    this.chunks = [];
    this.captured = 0;
  }

  private backToArmed() {
    this.mode = "armed";
    this.captured = 0;
    this.heardSpeech = false;
    this.silence = 0;
    // The wake phrase's features must not re-fire on the next frame.
    this.detector.reset();
    this.vad.reset();
  }
}
