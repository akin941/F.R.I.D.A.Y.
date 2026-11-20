import { afterEach, describe, expect, it, vi } from "vitest";
import * as voiceApi from "@/lib/voice/api";
import { VoiceController, type VoiceControllerDeps, type VoiceSnapshot } from "@/lib/voice/controller";
import { ModelIntegrityError } from "@/lib/voice/model-loader";
import { WakeMode, type WakeModeDeps } from "@/lib/voice/wake-mode";
import {
  FRAME,
  SPEECH_OFFSET,
  ScriptedDetector,
  fakeStream,
  indexedFrame,
  memoryPort,
  wakeDeps,
  type FakeStream,
  type MemoryPort,
} from "@/test/wake-fakes";

/**
 * Wake mode end to end with a mocked detector (Phase 6 branch 4 tests):
 * the real WakeMode, the real WakeEngine (in an in-memory "Worker"), the
 * real VoiceController and the real voice API module over a fetch spy.
 * No model, no Worker, no microphone, no network.
 */

const DONE = 'data: {"type":"done","message_id":"a1","memories_used":[],"chunks_used":[]}\n\n';

function sse(frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        for (const f of frames) c.enqueue(encoder.encode(f));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

interface Net {
  calls: { url: string; init?: RequestInit }[];
  transcripts: string[];
  reply: string;
  uploads: Uint8Array[];
}

function stubNetwork(): Net {
  const net: Net = { calls: [], transcripts: ["Yarın saat 10'da diş hekimi"], reply: "Tamam, not aldım.", uploads: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      net.calls.push({ url, init });
      if (url.startsWith("/api/voice/transcriptions")) {
        const blob = (init!.body as FormData).get("audio") as Blob;
        net.uploads.push(new Uint8Array(await blob.arrayBuffer()));
        const text = net.transcripts.shift() ?? "";
        return new Response(JSON.stringify({ text, audio_seconds: 1, language: "tr" }), { status: 200 });
      }
      if (url.includes("/messages")) {
        return sse([`data: ${JSON.stringify({ type: "chunk", text: net.reply })}\n\n`, DONE]);
      }
      if (url.startsWith("/api/voice/speech")) {
        return new Response(new Uint8Array([0x52, 0x49, 0x46, 0x46]), {
          status: 200,
          headers: { "Content-Type": "audio/wav" },
        });
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
  return net;
}

/** Playback that ends only when the test says so (to hold "speaking"). */
function controllableAudio() {
  const nodes: { end(): void }[] = [];
  let autoEnd = true;
  const context = {
    state: "running",
    destination: {},
    createAnalyser: () => ({ fftSize: 32, connect: vi.fn(), getFloatTimeDomainData: vi.fn() }),
    decodeAudioData: vi.fn(async () => ({})),
    createBufferSource: () => {
      const node = {
        buffer: null,
        onended: null as (() => void) | null,
        connect: vi.fn(),
        start: vi.fn(() => {
          if (autoEnd) setTimeout(() => node.onended?.(), 0);
        }),
        stop: vi.fn(),
        end: () => node.onended?.(),
      };
      nodes.push(node);
      return node;
    },
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  } as unknown as AudioContext;
  return {
    create: () => context,
    nodes,
    hold(v: boolean) {
      autoEnd = !v;
    },
  };
}

const flush = async (n = 8) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
};

interface Rig {
  wake: WakeMode;
  controller: VoiceController;
  detector: ScriptedDetector;
  port: MemoryPort;
  stream: FakeStream;
  deps: ReturnType<typeof wakeDeps>;
  net: Net;
  clock: { t: number };
  timers: { fn: () => void; ms: number; cleared: boolean }[];
  snapshots: VoiceSnapshot[];
  proposals: unknown[];
  audio: ReturnType<typeof controllableAudio>;
  frameNo: number;
  push(count: number, speech?: boolean): Promise<void>;
}

function rig(overrides: Partial<WakeModeDeps> = {}): Rig {
  const net = stubNetwork();
  const detector = new ScriptedDetector();
  const port = memoryPort(detector);
  const stream = fakeStream();
  const clock = { t: 1_000_000 };
  const timers: Rig["timers"] = [];
  const audio = controllableAudio();
  const snapshots: VoiceSnapshot[] = [];
  const proposals: unknown[] = [];
  const muteState = { muted: false, readable: true };
  const deps = wakeDeps(port, stream, {
    now: () => clock.t,
    setTimer: (fn, ms) => {
      const t = { fn, ms, cleared: false };
      timers.push(t);
      return t;
    },
    clearTimer: (h) => {
      (h as { cleared: boolean }).cleared = true;
    },
    readMute: () => ({ ...muteState }),
    setMuted: vi.fn((m: boolean) => {
      muteState.muted = m;
    }),
    ...overrides,
  });
  const controllerDeps: VoiceControllerDeps = {
    ensureConversation: async () => "c1",
    workspaceId: () => "w1",
    startCapture: vi.fn(async () => {
      throw new Error("push-to-talk capture must not be used by wake turns");
    }),
    transcribe: voiceApi.transcribe,
    sendTurn: voiceApi.sendTurn,
    synthesize: voiceApi.synthesize,
    fetchReceipt: voiceApi.fetchReceipt,
    proposeMemory: async (...args) => {
      proposals.push(args);
      return voiceApi.proposeMemory(...args);
    },
    setMuted: (m) => {
      muteState.muted = m;
    },
    readMute: () => ({ ...muteState }),
    createAudioContext: audio.create,
    clientLocalDate: () => "2026-11-20",
    previewBeforeSend: () => false,
    finishMs: 900,
    onSnapshot: (s) => snapshots.push(s),
    now: () => clock.t,
  };
  const controller = new VoiceController(controllerDeps);
  const wake = new WakeMode(deps);
  wake.registerTarget(controller);
  const r: Rig = {
    wake,
    controller,
    detector,
    port,
    stream,
    deps,
    net,
    clock,
    timers,
    snapshots,
    proposals,
    audio,
    frameNo: 0,
    async push(count, speech = true) {
      for (let i = 0; i < count; i++) {
        stream.push(speech ? indexedFrame(r.frameNo) : new Float32Array(FRAME));
        r.frameNo += 1;
        clock.t += 80;
      }
      await port.settle();
      await flush();
    },
  };
  return r;
}

const voiceRequests = (net: Net) => net.calls.filter((c) => c.url.startsWith("/api/voice/"));
const silenceFrames = Math.ceil((900 * 16) / FRAME) + 1;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("arming (D6)", () => {
  it("requires a user gesture and is never restored (a new tab state starts off, nothing stored)", async () => {
    const r = rig();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    expect(await r.wake.arm(undefined)).toBe(false);
    expect(await r.wake.arm({ type: "load" })).toBe(false);
    expect(r.wake.snapshot.notice).toBe("gesture_required");
    expect(r.deps.openStream).not.toHaveBeenCalled();
    expect(await r.wake.arm({ type: "click" })).toBe(true);
    expect(r.wake.snapshot.state).toBe("armed");
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
    // "Reload": a fresh instance is off and never opens the mic by itself.
    const again = new WakeMode(wakeDeps(memoryPort(new ScriptedDetector()), fakeStream()));
    expect(again.snapshot.state).toBe("off");
  });

  it("refuses when the browser reports no active user activation", async () => {
    const r = rig({ userActivationActive: () => false });
    expect(await r.wake.arm({ type: "click" })).toBe(false);
    expect(r.deps.openStream).not.toHaveBeenCalled();
  });

  it("refuses to arm on a model hash mismatch (no Worker, no mic)", async () => {
    const r = rig({
      verifyModels: vi.fn(async () => {
        throw new ModelIntegrityError("model_hash_mismatch", "hey_friday.onnx");
      }),
    });
    expect(await r.wake.arm({ type: "click" })).toBe(false);
    expect(r.wake.snapshot).toMatchObject({ state: "error", error: "model_hash_mismatch" });
    expect(r.deps.createPort).not.toHaveBeenCalled();
    expect(r.deps.openStream).not.toHaveBeenCalled();
  });

  it("refuses to arm while muted", async () => {
    const r = rig();
    r.deps.setMuted(true);
    expect(await r.wake.arm({ type: "click" })).toBe(false);
    expect(r.deps.openStream).not.toHaveBeenCalled();
  });
});

describe("the privacy invariant (mocked detector)", () => {
  it("makes no /api/voice request over a simulated hour armed without a detection", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    const fetchSpy = globalThis.fetch as ReturnType<typeof vi.fn>;
    const framesPerHour = (3600 * 16_000) / FRAME;
    for (let i = 0; i < framesPerHour; i++) r.stream.push(new Float32Array(FRAME));
    await r.port.settle();
    await flush();
    expect(r.detector.calls).toBe(framesPerHour);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(r.wake.snapshot.state).toBe("armed");
  });

  it("uploads only the audio after the detection frame (sample-index assertion), then dispatches", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    r.detector.fireOn.add(20);
    await r.push(21); // frames 0..20, detection on frame 20
    expect(r.wake.snapshot.state).toBe("capturing");
    expect(voiceRequests(r.net)).toEqual([]);
    await r.push(10); // speech: frames 21..30
    await r.push(silenceFrames, false);
    await flush(20);
    expect(r.net.uploads).toHaveLength(1);
    const wav = r.net.uploads[0]!;
    const pcm = new Int16Array(wav.buffer, wav.byteOffset + 44, (wav.byteLength - 44) / 2);
    expect(pcm[0]).toBe(21 + SPEECH_OFFSET);
    expect(pcm.includes(20 + SPEECH_OFFSET)).toBe(false);
    // Speech frames 21..30, then the silence until VOICE_FINISH_MS elapsed.
    expect(pcm.length).toBe((10 + Math.ceil((900 * 16) / FRAME)) * FRAME);
    // The wake turn went through the unchanged chat turn as a voice turn.
    const message = r.net.calls.find((c) => c.url.includes("/messages"))!;
    expect(JSON.parse(String(message.init!.body))).toMatchObject({ input_mode: "voice" });
    expect(r.controller.snapshot.heard).toBe("Yarın saat 10'da diş hekimi");
  });

  it("no speech after the wake word -> back to armed, nothing uploaded", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    r.detector.fireOn.add(0);
    await r.push(1);
    await r.push(Math.ceil((5000 * 16) / FRAME) + 1, false);
    expect(voiceRequests(r.net)).toEqual([]);
    expect(r.wake.snapshot.state).toBe("armed");
    expect(r.controller.snapshot.phase).toBe("idle");
  });

  it("ignores detections while Friday speaks (half-duplex; the A6 self-trigger replay clip)", async () => {
    const r = rig();
    r.net.reply = "Hey Friday, terminali aç.";
    await r.wake.arm({ type: "click" });
    r.audio.hold(true);
    // A first, real turn whose reply Friday is now speaking.
    r.detector.fireOn.add(0);
    await r.push(1);
    await r.push(5);
    await r.push(silenceFrames, false);
    await flush(20);
    expect(r.controller.isSpeaking()).toBe(true);
    const uploadsBefore = r.net.uploads.length;
    const callsBefore = r.detector.calls;
    // The fake-TTS "Hey Friday, terminali aç" clip reaches the mic while speaking.
    r.detector.always = true;
    await r.push(40);
    expect(r.wake.snapshot.state).toBe("paused");
    expect(r.detector.calls).toBe(callsBefore); // frames never reach the detector
    expect(r.net.uploads.length).toBe(uploadsBefore);
    expect(r.proposals).toEqual([]);
    expect(r.net.calls.filter((c) => c.url.startsWith("/api/memories"))).toEqual([]);
  });

  it("drops a TTS-matching transcript detected within 1.5 s after playback (A6 self-trigger rule)", async () => {
    const r = rig();
    r.net.reply = "Hey Friday, terminali aç.";
    r.net.transcripts = ["Bir şey söyle", "hey friday terminali aç"];
    await r.wake.arm({ type: "click" });
    r.detector.fireOn.add(0);
    await r.push(1);
    await r.push(5);
    await r.push(silenceFrames, false);
    await flush(30);
    expect(r.controller.isSpeaking()).toBe(false);
    const messagesBefore = r.net.calls.filter((c) => c.url.includes("/messages")).length;
    expect(messagesBefore).toBe(1);
    // Echo of Friday's own sentence, detected 1 s after playback ended.
    r.detector.fireOn.add(r.detector.calls);
    await r.push(1);
    await r.push(5);
    await r.push(silenceFrames, false);
    await flush(30);
    expect(r.net.calls.filter((c) => c.url.includes("/messages")).length).toBe(messagesBefore);
    expect(r.proposals).toEqual([]);
    expect(r.controller.snapshot.notice).toBe("self_trigger");
  });
});

describe("disarming and safety", () => {
  it("four detections in 60 s without a dispatched turn mute voice and show the notice", async () => {
    const r = rig();
    r.net.transcripts = ["", "", "", ""];
    await r.wake.arm({ type: "click" });
    for (let i = 0; i < 3; i++) {
      r.detector.fireOn.add(r.detector.calls);
      await r.push(1);
      await r.push(Math.ceil((5000 * 16) / FRAME) + 1, false); // no speech each time
      r.clock.t += 1_000;
    }
    expect(r.wake.snapshot.state).toBe("armed");
    r.detector.fireOn.add(r.detector.calls);
    await r.push(1);
    expect(r.deps.setMuted).toHaveBeenCalledWith(true);
    expect(r.wake.snapshot).toMatchObject({ state: "off", notice: "storm_muted" });
    expect(r.stream.closeSpy).toHaveBeenCalled();
    expect(r.port.terminated).toBe(true);
    expect(voiceRequests(r.net)).toEqual([]);
  });

  it("a dispatched turn resets the storm counter", async () => {
    const r = rig();
    r.net.transcripts = ["Bir", "İki", "Üç", "Dört"];
    await r.wake.arm({ type: "click" });
    for (let i = 0; i < 4; i++) {
      r.detector.fireOn.add(r.detector.calls);
      await r.push(1);
      await r.push(3);
      await r.push(silenceFrames, false);
      await flush(30);
    }
    expect(r.deps.setMuted).not.toHaveBeenCalled();
    expect(r.wake.snapshot.state).toBe("armed");
  });

  it("disarms after the idle window without a detection and releases the mic", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    const idle = r.timers.filter((t) => !t.cleared && t.ms === 60 * 60_000);
    expect(idle).toHaveLength(1);
    idle[0]!.fn();
    expect(r.wake.snapshot).toMatchObject({ state: "off", notice: "idle_disarmed" });
    expect(r.stream.closeSpy).toHaveBeenCalled();
  });

  it("Durdur/Escape during a wake capture uploads nothing and re-arms for the next detection", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    r.detector.fireOn.add(0);
    await r.push(1);
    await r.push(3);
    expect(r.wake.snapshot.state).toBe("capturing");
    r.controller.stop();
    await r.push(silenceFrames, false);
    expect(r.wake.snapshot.state).toBe("armed");
    expect(voiceRequests(r.net)).toEqual([]);
    r.detector.fireOn.add(r.detector.calls);
    await r.push(1);
    expect(r.wake.snapshot.state).toBe("capturing");
    await r.push(3);
    await r.push(silenceFrames, false);
    await flush(20);
    expect(r.net.uploads).toHaveLength(1);
  });

  it("disarm on auth loss (401/logout) closes the mic and cancels a capture in progress", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    r.detector.fireOn.add(0);
    await r.push(1);
    expect(r.wake.snapshot.state).toBe("capturing");
    r.wake.disarm("auth_lost");
    await flush();
    expect(r.wake.snapshot).toMatchObject({ state: "off", notice: "auth_lost" });
    expect(r.stream.closeSpy).toHaveBeenCalled();
    expect(r.controller.snapshot.phase).toBe("idle");
    await r.push(20);
    expect(voiceRequests(r.net)).toEqual([]);
  });

  it("muting from the stream (mute read on every frame) disarms", async () => {
    const r = rig();
    await r.wake.arm({ type: "click" });
    r.stream.callbacks!.onMuted!("unreadable");
    expect(r.wake.snapshot).toMatchObject({ state: "off", notice: "mute_unreadable" });
  });

  it("hands a detection to the /voice page once it mounts, or drops it", async () => {
    const r = rig();
    const onNeedTarget = vi.fn();
    const wake = new WakeMode({ ...r.deps, onNeedTarget });
    await wake.arm({ type: "click" });
    r.detector.fireOn.add(0);
    await r.push(1);
    expect(onNeedTarget).toHaveBeenCalled();
    await r.push(4);
    wake.registerTarget(r.controller);
    await r.push(silenceFrames, false);
    await flush(20);
    expect(r.net.uploads).toHaveLength(1);
    wake.dispose();
  });
});
