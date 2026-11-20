import { afterEach, describe, expect, it, vi } from "vitest";
import { CAPTURE_PROCESSOR_NAME, CAPTURE_WORKLET_SOURCE, rms } from "@/lib/voice/capture-worklet";
import { CaptureError, startCapture } from "@/lib/voice/capture";

// --- the worklet source itself, evaluated against a fake audio scope -------

interface FakeProcessor {
  port: { postMessage: ReturnType<typeof vi.fn>; onmessage: ((e: { data: unknown }) => void) | null };
  process(inputs: Float32Array[][]): boolean;
}

function loadProcessor(contextRate: number, frameSize = 160): FakeProcessor {
  const registered: Record<string, new (options: unknown) => FakeProcessor> = {};
  class AudioWorkletProcessor {
    port = { postMessage: vi.fn(), onmessage: null };
  }
  new Function("AudioWorkletProcessor", "registerProcessor", "sampleRate", CAPTURE_WORKLET_SOURCE)(
    AudioWorkletProcessor,
    (name: string, ctor: new (options: unknown) => FakeProcessor) => {
      registered[name] = ctor;
    },
    contextRate,
  );
  const Ctor = registered[CAPTURE_PROCESSOR_NAME]!;
  return new Ctor({ processorOptions: { targetRate: 16_000, frameSize } });
}

describe("capture worklet", () => {
  it("downsamples 48 kHz to 16 kHz mono and posts frames with RMS", () => {
    const processor = loadProcessor(48_000);
    const left = new Float32Array(480).fill(0.5);
    const right = new Float32Array(480).fill(0.5);
    expect(processor.process([[left, right]])).toBe(true);
    expect(processor.port.postMessage).toHaveBeenCalledTimes(1);
    const [message] = processor.port.postMessage.mock.calls[0]!;
    expect(message.samples).toHaveLength(160);
    expect(message.samples[0]).toBeCloseTo(0.5);
    expect(message.rms).toBeCloseTo(0.5);
  });

  it("flushes the partial frame on stop, then stops processing", () => {
    const processor = loadProcessor(44_100);
    processor.process([[new Float32Array(128).fill(0.25)]]);
    expect(processor.port.postMessage).not.toHaveBeenCalled();
    processor.port.onmessage!({ data: "stop" });
    const [message] = processor.port.postMessage.mock.calls[0]!;
    expect(message.final).toBe(true);
    expect(message.samples.length).toBeGreaterThan(40);
    expect(processor.process([[new Float32Array(128)]])).toBe(false);
  });

  it("rms helper", () => {
    expect(rms(new Float32Array([1, -1, 1, -1]))).toBe(1);
    expect(rms(new Float32Array(0))).toBe(0);
  });
});

// --- startCapture with mocked getUserMedia / AudioContext / worklet port ---

function fakeMedia() {
  const track = { stop: vi.fn() };
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  const port = { postMessage: vi.fn(), onmessage: null as ((e: MessageEvent) => void) | null };
  const source = { connect: vi.fn(), disconnect: vi.fn() };
  const context = {
    createMediaStreamSource: vi.fn(() => source),
    close: vi.fn(async () => undefined),
  } as unknown as AudioContext;
  const getUserMedia = vi.fn(async () => stream);
  const frame = (values: number[], final = false) =>
    port.onmessage!({ data: { samples: new Float32Array(values), rms: 0.3, final } } as MessageEvent);
  return { track, port, context, getUserMedia, frame };
}

const unmuted = () => ({ muted: false, readable: true });

describe("startCapture", () => {
  afterEach(() => vi.useRealTimers());

  it("collects frames into a WAV and releases the mic track on stop", async () => {
    const media = fakeMedia();
    const onLevel = vi.fn();
    const session = await startCapture(
      { onLevel },
      {
        getUserMedia: media.getUserMedia,
        createAudioContext: () => media.context,
        createWorkletNode: () => media.port && ({ port: media.port, disconnect: vi.fn() } as unknown as AudioWorkletNode),
        readMute: unmuted,
      },
    );
    media.frame([0.5, -0.5]);
    media.frame([0.25]);
    expect(onLevel).toHaveBeenCalledWith(0.3);
    const stopping = session.stop();
    media.frame([], true);
    const wav = await stopping;
    expect(media.port.postMessage).toHaveBeenCalledWith("stop");
    expect(wav!.length).toBe(44 + 3 * 2);
    expect(media.track.stop).toHaveBeenCalled();
    expect(media.getUserMedia).toHaveBeenCalledWith({
      audio: expect.objectContaining({ channelCount: 1, echoCancellation: true }),
    });
  });

  it("an empty capture returns null (nothing to upload)", async () => {
    const media = fakeMedia();
    const session = await startCapture({}, {
      getUserMedia: media.getUserMedia,
      createAudioContext: () => media.context,
      createWorkletNode: () => ({ port: media.port, disconnect: vi.fn() }) as unknown as AudioWorkletNode,
      readMute: unmuted,
    });
    const stopping = session.stop();
    media.frame([], true);
    expect(await stopping).toBeNull();
  });

  it("stops collecting at the utterance cap", async () => {
    const media = fakeMedia();
    const onCapReached = vi.fn();
    const session = await startCapture({ onCapReached }, {
      getUserMedia: media.getUserMedia,
      createAudioContext: () => media.context,
      createWorkletNode: () => ({ port: media.port, disconnect: vi.fn() }) as unknown as AudioWorkletNode,
      readMute: unmuted,
      maxSeconds: 1,
    });
    media.frame(new Array(10_000).fill(0.1));
    media.frame(new Array(10_000).fill(0.1));
    expect(onCapReached).toHaveBeenCalledTimes(1);
    expect(session.samples).toBe(16_000);
  });

  it("refuses to open the mic while muted", async () => {
    const media = fakeMedia();
    const onMuted = vi.fn();
    await expect(
      startCapture({ onMuted }, { getUserMedia: media.getUserMedia, readMute: () => ({ muted: true, readable: true }) }),
    ).rejects.toEqual(new CaptureError("muted"));
    expect(media.getUserMedia).not.toHaveBeenCalled();
    expect(onMuted).toHaveBeenCalledWith("muted");
  });

  it("a mute-state read failure mid-capture releases the mic track and discards audio (A6)", async () => {
    const media = fakeMedia();
    let state = { muted: false, readable: true };
    const onMuted = vi.fn();
    const session = await startCapture({ onMuted }, {
      getUserMedia: media.getUserMedia,
      createAudioContext: () => media.context,
      createWorkletNode: () => ({ port: media.port, disconnect: vi.fn() }) as unknown as AudioWorkletNode,
      readMute: () => state,
    });
    media.frame([0.5, 0.5]);
    // Storage now throws -> readMuteState() reports muted + unreadable.
    state = { muted: true, readable: false };
    media.frame([0.5]);
    expect(media.track.stop).toHaveBeenCalled();
    expect(onMuted).toHaveBeenCalledWith("unreadable");
    expect(session.samples).toBe(0);
    expect(await session.stop()).toBeNull();
  });

  it("maps a denied permission to mic_denied and a missing device to mic_missing", async () => {
    const denied = vi.fn(async () => {
      throw new DOMException("no", "NotAllowedError");
    });
    await expect(startCapture({}, { getUserMedia: denied, readMute: unmuted })).rejects.toMatchObject({
      code: "mic_denied",
    });
    const missing = vi.fn(async () => {
      throw new DOMException("no", "NotFoundError");
    });
    await expect(startCapture({}, { getUserMedia: missing, readMute: unmuted })).rejects.toMatchObject({
      code: "mic_missing",
    });
  });

  it("no mediaDevices (insecure context) -> mic_insecure", async () => {
    vi.stubGlobal("navigator", {});
    await expect(startCapture({}, { readMute: unmuted })).rejects.toMatchObject({ code: "mic_insecure" });
    vi.unstubAllGlobals();
  });

  it("cancel discards the audio and releases the track", async () => {
    const media = fakeMedia();
    const session = await startCapture({}, {
      getUserMedia: media.getUserMedia,
      createAudioContext: () => media.context,
      createWorkletNode: () => ({ port: media.port, disconnect: vi.fn() }) as unknown as AudioWorkletNode,
      readMute: unmuted,
    });
    media.frame([0.5]);
    session.cancel();
    expect(media.track.stop).toHaveBeenCalled();
    expect(await session.stop()).toBeNull();
  });
});
