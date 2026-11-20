import { vi, type Mock } from "vitest";
import type { MicStream, MicStreamCallbacks } from "@/lib/voice/mic-stream";
import type { VerifiedModels } from "@/lib/voice/model-loader";
import type { VadEngine } from "@/lib/voice/vad";
import { WakeEngine, type WakeInbound, type WakeOutbound } from "@/lib/voice/wake-engine";
import type { WakeInitMessage, WakeModeDeps, WakePort, WakeSnapshot } from "@/lib/voice/wake-mode";
import type { WakeDetector } from "@/lib/voice/wakeword/openwakeword";

/**
 * Test doubles for wake mode (Phase 6 branch 4): a mocked detector (fires on
 * chosen frame numbers), an energy VAD, an in-memory "Worker" that runs the
 * real WakeEngine, and a fake mic stream the test pushes frames into.
 * No ONNX model, no Worker, no microphone.
 */

export const FRAME = 1280;

/** A frame whose every sample encodes the frame index as PCM16 (index < 32767). */
export function indexedFrame(index: number, speech = true): Float32Array {
  // Speech frames get a +0.2 offset so the energy VAD hears them; the index
  // is still recoverable: pcm = index + round(0.2 * 0x7fff).
  const base = speech ? 0.2 : 0;
  return new Float32Array(FRAME).fill(base + index / 0x7fff);
}

export const SPEECH_OFFSET = Math.round(0.2 * 0x7fff);

export class ScriptedDetector implements WakeDetector {
  calls = 0;
  resets = 0;
  fireOn = new Set<number>();
  /** Fire on every call (a wake storm). */
  always = false;
  async process(): Promise<number> {
    const n = this.calls;
    this.calls += 1;
    return this.always || this.fireOn.has(n) ? 0.99 : 0.01;
  }
  reset() {
    this.resets += 1;
  }
  heldSamples() {
    return 0;
  }
}

/** Speech when the frame's mean amplitude is above 0.1. */
export class EnergyVad implements VadEngine {
  async process(samples: Float32Array): Promise<number> {
    let sum = 0;
    for (const s of samples) sum += Math.abs(s);
    return samples.length && sum / samples.length > 0.1 ? 0.95 : 0.02;
  }
  reset() {}
}

export interface MemoryPort extends WakePort {
  posted: (WakeInbound | WakeInitMessage)[];
  engine(): WakeEngine | null;
  settle(): Promise<void>;
  terminated: boolean;
}

export function memoryPort(detector: WakeDetector, vad: VadEngine = new EnergyVad()): MemoryPort {
  let handler: ((m: WakeOutbound) => void) | null = null;
  let engine: WakeEngine | null = null;
  const port: MemoryPort = {
    posted: [],
    terminated: false,
    post(message) {
      if (port.terminated) return;
      port.posted.push(message.type === "frame" ? { type: "frame", samples: new Float32Array(0) } : message);
      if (message.type === "init") {
        engine = new WakeEngine(detector, vad, (m) => handler?.(m), message.config);
        queueMicrotask(() => handler?.({ type: "ready" }));
        return;
      }
      void engine?.handle(message);
    },
    onMessage(h) {
      handler = h;
    },
    terminate() {
      port.terminated = true;
      handler = null;
    },
    engine: () => engine,
    async settle() {
      for (let i = 0; i < 4; i++) {
        await engine?.idle();
        await new Promise((r) => setTimeout(r, 0));
      }
    },
  };
  return port;
}

export interface FakeStream extends MicStream {
  callbacks: MicStreamCallbacks | null;
  push(samples: Float32Array): void;
  closeSpy: Mock<() => void>;
}

export function fakeStream(): FakeStream {
  let closed = false;
  const stream: FakeStream = {
    callbacks: null,
    closeSpy: vi.fn<() => void>(),
    push(samples) {
      if (!closed) stream.callbacks?.onFrame(samples, 0.1);
    },
    close() {
      closed = true;
      stream.closeSpy();
    },
    get closed() {
      return closed;
    },
  };
  return stream;
}

export const FAKE_MODELS = {
  melspectrogram: new ArrayBuffer(1),
  embedding: new ArrayBuffer(1),
  keyword: new ArrayBuffer(1),
  vad: new ArrayBuffer(1),
} as VerifiedModels;

export function wakeDeps(
  port: WakePort,
  stream: FakeStream,
  overrides: Partial<WakeModeDeps> = {},
): WakeModeDeps & { snapshots: WakeSnapshot[]; muted: { muted: boolean; readable: boolean } } {
  const snapshots: WakeSnapshot[] = [];
  const muted = { muted: false, readable: true };
  return {
    snapshots,
    muted,
    verifyModels: vi.fn(async () => ({ ...FAKE_MODELS })),
    openStream: vi.fn(async (callbacks: MicStreamCallbacks) => {
      stream.callbacks = callbacks;
      return stream;
    }),
    createPort: vi.fn(() => port),
    runtimePath: "/voice-models/ort/",
    readMute: () => ({ ...muted }),
    setMuted: vi.fn((value: boolean) => {
      muted.muted = value;
    }),
    settings: () => ({ threshold: 0.5, idleMinutes: 60 }),
    onSnapshot: (s) => snapshots.push(s),
    userActivationActive: () => undefined,
    ...overrides,
  };
}
