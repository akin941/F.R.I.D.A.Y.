"use client";

import { readMuteState } from "@/lib/voice/mute";

/**
 * Speech out (Phase 6 D7): a sentence queue with one synthesis request in
 * flight plus one prefetched, sequential playback through ONE AudioContext
 * with an AnalyserNode (feeds the orb's `speaking` level), and abort.
 *
 * `speak()` is the single speak function: every sentence Friday says goes
 * through it, and the mute flag is checked inside it (A6/A7) -- when muted
 * (or the mute state is unreadable) it makes no synthesis request and no
 * playback call. Mute is checked again right before each sentence starts
 * playing, so muting mid-reply silences the rest.
 */

export type Synthesize = (text: string, signal: AbortSignal) => Promise<ArrayBuffer>;

export interface SpeakerOptions {
  synthesize: Synthesize;
  createAudioContext?: () => AudioContext;
  readMute?: typeof readMuteState;
  /** The first sentence of a reply started playing. */
  onPlaybackStart?: () => void;
  /** Speaking -> silent with nothing queued (after `end()`), or stopped. */
  onIdle?: (reason: "done" | "stopped" | "muted") => void;
  /** A sentence failed to synthesize or decode (reply text stays on screen). */
  onError?: (err: unknown) => void;
  /** At most this many queued sentences synthesizing (in flight + prefetch). */
  lookahead?: number;
}

interface Item {
  text: string;
  controller: AbortController;
  audio: Promise<ArrayBuffer> | null;
}

export class Speaker {
  private readonly opts: SpeakerOptions;
  private readonly lookahead: number;
  private queue: Item[] = [];
  private playing: AudioBufferSourceNode | null = null;
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private ended = false;
  private active = false;
  private startedThisReply = false;
  private generation = 0;
  private looping = false;
  private samples: Float32Array<ArrayBuffer> | null = null;

  constructor(opts: SpeakerOptions) {
    this.opts = opts;
    this.lookahead = Math.max(1, opts.lookahead ?? 2);
  }

  private muted(): boolean {
    return (this.opts.readMute ?? readMuteState)().muted;
  }

  /** True while audio is queued or playing. */
  get busy(): boolean {
    return this.active;
  }

  /** THE speak function. Returns false (and does nothing at all) when muted. */
  speak(text: string): boolean {
    const sentence = text.trim();
    if (!sentence) return false;
    if (this.muted()) {
      if (this.active) this.stop("muted");
      return false;
    }
    if (!this.active) {
      this.active = true;
      this.ended = false;
      this.startedThisReply = false;
    }
    this.queue.push({ text: sentence, controller: new AbortController(), audio: null });
    this.prefetch();
    this.kick();
    return true;
  }

  /** No more sentences for this reply: go idle once the queue drains. */
  end(): void {
    this.ended = true;
    if (this.active && !this.playing && this.queue.length === 0 && !this.looping) {
      this.finish("done");
    }
  }

  /** Stop playback now and abort every pending synthesis request. */
  stop(reason: "stopped" | "muted" = "stopped"): void {
    this.generation += 1;
    for (const item of this.queue) item.controller.abort();
    this.queue = [];
    if (this.playing) {
      const node = this.playing;
      this.playing = null;
      try {
        node.stop();
      } catch {
        // Not started yet.
      }
      node.onended?.(new Event("ended"));
    }
    if (this.active) this.finish(reason);
  }

  /** Current output level 0..1 (RMS of the analyser), for the orb. */
  level(): number {
    if (!this.analyser || !this.playing) return 0;
    if (!this.samples || this.samples.length !== this.analyser.fftSize) {
      this.samples = new Float32Array(this.analyser.fftSize);
    }
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (const value of this.samples) sum += value * value;
    return Math.min(1, Math.sqrt(sum / this.samples.length) * 3);
  }

  dispose(): void {
    this.stop();
    void this.context?.close().catch(() => undefined);
    this.context = null;
    this.analyser = null;
  }

  private finish(reason: "done" | "stopped" | "muted") {
    this.active = false;
    this.ended = false;
    this.opts.onIdle?.(reason);
  }

  private prefetch() {
    let outstanding = 0;
    for (const item of this.queue) {
      if (outstanding >= this.lookahead) break;
      if (!item.audio) {
        item.audio = this.opts.synthesize(item.text, item.controller.signal);
        // Unhandled-rejection guard; the error is handled in the loop.
        item.audio.catch(() => undefined);
      }
      outstanding += 1;
    }
  }

  private audioContext(): AudioContext {
    if (!this.context) {
      this.context = this.opts.createAudioContext
        ? this.opts.createAudioContext()
        : new AudioContext();
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.connect(this.context.destination);
    }
    return this.context;
  }

  private kick() {
    if (this.looping) return;
    this.looping = true;
    const generation = this.generation;
    void this.loop(generation).finally(() => {
      this.looping = false;
      if (this.active && this.queue.length > 0) this.kick();
      else if (this.active && this.ended && !this.playing) this.finish("done");
    });
  }

  /** Plays the queue in order; `stop()` bumps the generation to end it. */
  private async loop(generation: number): Promise<void> {
    while (generation === this.generation) {
      const item = this.queue[0];
      if (!item) return;
      this.prefetch();
      let bytes: ArrayBuffer;
      try {
        bytes = await item.audio!;
      } catch (err) {
        if (generation !== this.generation) return;
        this.queue.shift();
        if (!(err instanceof DOMException && err.name === "AbortError")) this.opts.onError?.(err);
        continue;
      }
      if (generation !== this.generation) return;
      // A6: mute checked again inside the speak path, right before playback.
      if (this.muted()) {
        this.stop("muted");
        return;
      }
      const context = this.audioContext();
      let buffer: AudioBuffer;
      try {
        buffer = await context.decodeAudioData(bytes.slice(0));
      } catch (err) {
        if (generation !== this.generation) return;
        this.queue.shift();
        this.opts.onError?.(err);
        continue;
      }
      if (generation !== this.generation) return;
      this.queue.shift();
      this.prefetch();
      const node = context.createBufferSource();
      node.buffer = buffer;
      node.connect(this.analyser!);
      this.playing = node;
      const ended = new Promise<void>((resolve) => {
        node.onended = () => resolve();
      });
      if (context.state === "suspended") await context.resume().catch(() => undefined);
      if (generation !== this.generation) return;
      node.start();
      if (!this.startedThisReply) {
        this.startedThisReply = true;
        this.opts.onPlaybackStart?.();
      }
      await ended;
      if (this.playing === node) this.playing = null;
    }
  }
}
