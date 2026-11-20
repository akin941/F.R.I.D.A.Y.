import { describe, expect, it } from "vitest";
import { WakeEngine, type WakeOutbound } from "@/lib/voice/wake-engine";
import { EnergyVad, FRAME, SPEECH_OFFSET, ScriptedDetector, indexedFrame } from "@/test/wake-fakes";

function engine(config = {}) {
  const detector = new ScriptedDetector();
  const out: WakeOutbound[] = [];
  const e = new WakeEngine(detector, new EnergyVad(), (m) => out.push(m), {
    threshold: 0.5,
    silenceMs: 900,
    noSpeechMs: 5000,
    maxSamples: 30 * 16_000,
    ...config,
  });
  return { e, detector, out };
}

async function feed(e: WakeEngine, frames: Float32Array[]) {
  for (const f of frames) void e.handle({ type: "frame", samples: f });
  await e.idle();
}

const silence = () => new Float32Array(FRAME);
const framesOfMs = (ms: number) => Math.ceil((ms * 16) / FRAME);

describe("WakeEngine (mocked detector, D6 capture state machine)", () => {
  it("posts nothing at all while armed without a detection", async () => {
    const { e, out } = engine();
    await feed(e, Array.from({ length: 500 }, (_, i) => indexedFrame(i)));
    expect(out).toEqual([]);
  });

  it("starts the utterance at the frame after the detection frame (no pre-roll)", async () => {
    const { e, detector, out } = engine();
    detector.fireOn.add(10);
    const frames = Array.from({ length: 30 }, (_, i) => indexedFrame(i));
    await feed(e, [...frames, ...Array.from({ length: framesOfMs(900) + 1 }, silence)]);
    const detected = out.find((m) => m.type === "detected");
    expect(detected).toMatchObject({ type: "detected", frame: 10, startSample: 11 * FRAME });
    const utterance = out.find((m) => m.type === "utterance") as Extract<WakeOutbound, { type: "utterance" }>;
    expect(utterance.reason).toBe("silence");
    expect(utterance.startSample).toBe(11 * FRAME);
    // Sample-index assertion: the first uploaded sample belongs to frame 11.
    expect(utterance.pcm[0]).toBe(11 + SPEECH_OFFSET);
    expect(utterance.pcm[0]).not.toBe(10 + SPEECH_OFFSET);
    // Frames 11..29 (speech) then the trailing silence.
    expect(utterance.pcm[19 * FRAME - 1]).toBe(29 + SPEECH_OFFSET);
    // Back to armed; detector buffers reset so the wake phrase cannot re-fire.
    expect(e.state).toBe("armed");
    expect(detector.resets).toBeGreaterThan(0);
  });

  it("no speech within 5 s of the wake -> no_speech, nothing posted as audio", async () => {
    const { e, detector, out } = engine();
    detector.fireOn.add(0);
    await feed(e, [indexedFrame(0), ...Array.from({ length: framesOfMs(5000) + 1 }, silence)]);
    expect(out.map((m) => m.type)).toEqual(["detected", "no_speech"]);
    expect(e.state).toBe("armed");
  });

  it("a pause shorter than the finish window does not end the utterance (A7 alignment)", async () => {
    const { e, detector, out } = engine({ silenceMs: 900 });
    detector.fireOn.add(0);
    await feed(e, [
      indexedFrame(0),
      indexedFrame(1),
      ...Array.from({ length: framesOfMs(600) }, silence),
      indexedFrame(2),
    ]);
    expect(out.map((m) => m.type)).toEqual(["detected"]);
    await feed(e, Array.from({ length: framesOfMs(900) + 1 }, silence));
    expect(out.map((m) => m.type)).toEqual(["detected", "utterance"]);
  });

  it("caps an utterance at the configured maximum", async () => {
    const { e, detector, out } = engine({ maxSamples: 10 * FRAME });
    detector.fireOn.add(0);
    await feed(e, Array.from({ length: 20 }, (_, i) => indexedFrame(i)));
    const utterance = out.find((m) => m.type === "utterance") as Extract<WakeOutbound, { type: "utterance" }>;
    expect(utterance.reason).toBe("cap");
    expect(utterance.pcm.length).toBe(10 * FRAME);
  });

  it("drops every frame while paused (half-duplex) and cancels an utterance", async () => {
    const { e, detector, out } = engine();
    detector.always = true;
    void e.handle({ type: "pause" });
    await feed(e, Array.from({ length: 50 }, (_, i) => indexedFrame(i)));
    expect(detector.calls).toBe(0);
    expect(out).toEqual([]);
    void e.handle({ type: "resume" });
    await feed(e, [indexedFrame(50)]);
    expect(out.map((m) => m.type)).toEqual(["detected"]);
    void e.handle({ type: "cancel" });
    await feed(e, []);
    expect(e.state).toBe("armed");
  });

  it("finish ends the utterance now (with speech) or reports no speech", async () => {
    const { e, detector, out } = engine();
    detector.fireOn.add(0);
    await feed(e, [indexedFrame(0), indexedFrame(1)]);
    void e.handle({ type: "finish" });
    await e.idle();
    expect(out.at(-1)).toMatchObject({ type: "utterance", reason: "finish" });
  });
});
