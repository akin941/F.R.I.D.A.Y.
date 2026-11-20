import { describe, expect, it, vi } from "vitest";
import { Speaker, type Synthesize } from "@/lib/voice/playback";

/** A fake AudioContext whose buffer sources end when the test says so. */
export function fakeAudio() {
  const sources: { start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; end(): void }[] = [];
  const context = {
    state: "running",
    destination: {},
    createAnalyser: () => ({
      fftSize: 1024,
      connect: vi.fn(),
      getFloatTimeDomainData: (out: Float32Array) => out.fill(0.1),
    }),
    decodeAudioData: vi.fn(async () => ({ duration: 1 }) as unknown as AudioBuffer),
    createBufferSource: () => {
      const node = {
        buffer: null as unknown,
        onended: null as (() => void) | null,
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
        end() {
          node.onended?.();
        },
      };
      sources.push(node);
      return node;
    },
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  };
  return { context: context as unknown as AudioContext, sources };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const unmuted = () => ({ muted: false, readable: true });

function deferredSynth() {
  const calls: { text: string; signal: AbortSignal; resolve: () => void; reject: (e: unknown) => void }[] = [];
  const synthesize: Synthesize = (text, signal) =>
    new Promise<ArrayBuffer>((resolve, reject) => {
      calls.push({ text, signal, resolve: () => resolve(new ArrayBuffer(8)), reject });
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  return { synthesize, calls };
}

describe("Speaker", () => {
  it("plays sentences in order with one request in flight plus one prefetched", async () => {
    const audio = fakeAudio();
    const synth = deferredSynth();
    const onPlaybackStart = vi.fn();
    const onIdle = vi.fn();
    const speaker = new Speaker({
      synthesize: synth.synthesize,
      createAudioContext: () => audio.context,
      readMute: unmuted,
      onPlaybackStart,
      onIdle,
    });
    speaker.speak("Birinci cümle burada.");
    speaker.speak("İkinci cümle burada.");
    speaker.speak("Üçüncü cümle burada.");
    // Only two synthesis requests outstanding, never three.
    expect(synth.calls.map((c) => c.text)).toEqual(["Birinci cümle burada.", "İkinci cümle burada."]);
    synth.calls[0]!.resolve();
    await flush();
    expect(audio.sources).toHaveLength(1);
    expect(audio.sources[0]!.start).toHaveBeenCalled();
    expect(onPlaybackStart).toHaveBeenCalledTimes(1);
    // The first started playing -> the third is prefetched now.
    expect(synth.calls).toHaveLength(3);
    synth.calls[1]!.resolve();
    synth.calls[2]!.resolve();
    speaker.end();
    audio.sources[0]!.end();
    await flush();
    audio.sources[1]!.end();
    await flush();
    audio.sources[2]!.end();
    await flush();
    expect(audio.sources).toHaveLength(3);
    expect(onPlaybackStart).toHaveBeenCalledTimes(1);
    expect(onIdle).toHaveBeenCalledWith("done");
    expect(speaker.busy).toBe(false);
  });

  it("stop aborts pending synthesis requests and stops the current sentence", async () => {
    const audio = fakeAudio();
    const synth = deferredSynth();
    const onIdle = vi.fn();
    const speaker = new Speaker({
      synthesize: synth.synthesize,
      createAudioContext: () => audio.context,
      readMute: unmuted,
      onIdle,
    });
    speaker.speak("Birinci cümle burada.");
    speaker.speak("İkinci cümle burada.");
    synth.calls[0]!.resolve();
    await flush();
    speaker.stop();
    expect(synth.calls[1]!.signal.aborted).toBe(true);
    expect(audio.sources[0]!.stop).toHaveBeenCalled();
    expect(onIdle).toHaveBeenCalledWith("stopped");
    synth.calls[1]!.resolve();
    await flush();
    expect(audio.sources).toHaveLength(1);
  });

  it("with mute set, speak makes no synthesis request and no playback call", async () => {
    const audio = fakeAudio();
    const synthesize = vi.fn<Synthesize>(async () => new ArrayBuffer(8));
    const speaker = new Speaker({
      synthesize,
      createAudioContext: () => audio.context,
      readMute: () => ({ muted: true, readable: true }),
    });
    expect(speaker.speak("Merhaba, bugün nasılsın?")).toBe(false);
    await flush();
    expect(synthesize).not.toHaveBeenCalled();
    expect(audio.context.decodeAudioData).not.toHaveBeenCalled();
    expect(audio.sources).toHaveLength(0);
  });

  it("an unreadable mute state also blocks playback (fail-closed)", () => {
    const synthesize = vi.fn<Synthesize>(async () => new ArrayBuffer(8));
    const speaker = new Speaker({ synthesize, readMute: () => ({ muted: true, readable: false }) });
    expect(speaker.speak("Merhaba, bugün nasılsın?")).toBe(false);
    expect(synthesize).not.toHaveBeenCalled();
  });

  it("muting mid-reply silences the next sentence before it plays", async () => {
    const audio = fakeAudio();
    let muted = false;
    const synth = deferredSynth();
    const onIdle = vi.fn();
    const speaker = new Speaker({
      synthesize: synth.synthesize,
      createAudioContext: () => audio.context,
      readMute: () => ({ muted, readable: true }),
      onIdle,
    });
    speaker.speak("Birinci cümle burada.");
    speaker.speak("İkinci cümle burada.");
    synth.calls[0]!.resolve();
    await flush();
    muted = true;
    synth.calls[1]!.resolve();
    audio.sources[0]!.end();
    await flush();
    expect(audio.sources).toHaveLength(1);
    expect(onIdle).toHaveBeenCalledWith("muted");
  });

  it("a failed sentence is skipped and reported; the rest still plays", async () => {
    const audio = fakeAudio();
    const synth = deferredSynth();
    const onError = vi.fn();
    const speaker = new Speaker({
      synthesize: synth.synthesize,
      createAudioContext: () => audio.context,
      readMute: unmuted,
      onError,
    });
    speaker.speak("Birinci cümle burada.");
    speaker.speak("İkinci cümle burada.");
    synth.calls[0]!.reject(new Error("tts down"));
    synth.calls[1]!.resolve();
    await flush();
    await flush();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(audio.sources).toHaveLength(1);
  });

  it("level reads the analyser while playing", async () => {
    const audio = fakeAudio();
    const synth = deferredSynth();
    const speaker = new Speaker({
      synthesize: synth.synthesize,
      createAudioContext: () => audio.context,
      readMute: unmuted,
    });
    expect(speaker.level()).toBe(0);
    speaker.speak("Birinci cümle burada.");
    synth.calls[0]!.resolve();
    await flush();
    expect(speaker.level()).toBeCloseTo(0.3);
  });
});
