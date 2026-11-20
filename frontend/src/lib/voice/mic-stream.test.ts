import { describe, expect, it, vi } from "vitest";
import { CaptureError } from "@/lib/voice/capture";
import { openMicStream } from "@/lib/voice/mic-stream";

function fakeMedia() {
  const track = { stop: vi.fn(), addEventListener: vi.fn() };
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  const port = { onmessage: null as ((e: MessageEvent) => void) | null, postMessage: vi.fn() };
  const node = { port, disconnect: vi.fn() } as unknown as AudioWorkletNode;
  const context = {
    createMediaStreamSource: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
    close: vi.fn(async () => undefined),
  } as unknown as AudioContext;
  return {
    track,
    port,
    env: {
      getUserMedia: vi.fn(async () => stream),
      createAudioContext: () => context,
      createWorkletNode: () => node,
    },
  };
}

describe("openMicStream (wake mode's open mic)", () => {
  it("hands each frame on and keeps nothing", async () => {
    const media = fakeMedia();
    const onFrame = vi.fn();
    const mic = await openMicStream({ onFrame }, { ...media.env, readMute: () => ({ muted: false, readable: true }) });
    media.port.onmessage!({ data: { samples: new Float32Array(1280), rms: 0.2 } } as MessageEvent);
    expect(onFrame).toHaveBeenCalledWith(expect.any(Float32Array), 0.2);
    mic.close();
    expect(media.track.stop).toHaveBeenCalled();
    expect(mic.closed).toBe(true);
  });

  it("refuses to open while muted", async () => {
    const media = fakeMedia();
    const onMuted = vi.fn();
    await expect(
      openMicStream({ onFrame: vi.fn(), onMuted }, { ...media.env, readMute: () => ({ muted: true, readable: true }) }),
    ).rejects.toBeInstanceOf(CaptureError);
    expect(media.env.getUserMedia).not.toHaveBeenCalled();
    expect(onMuted).toHaveBeenCalledWith("muted");
  });

  it("checks mute on every frame and releases the track when it cannot be read (A6)", async () => {
    const media = fakeMedia();
    let state = { muted: false, readable: true };
    const onFrame = vi.fn();
    const onMuted = vi.fn();
    await openMicStream({ onFrame, onMuted }, { ...media.env, readMute: () => state });
    state = { muted: true, readable: false };
    media.port.onmessage!({ data: { samples: new Float32Array(1280), rms: 0.2 } } as MessageEvent);
    expect(onFrame).not.toHaveBeenCalled();
    expect(onMuted).toHaveBeenCalledWith("unreadable");
    expect(media.track.stop).toHaveBeenCalled();
  });

  it("maps a denied permission", async () => {
    const media = fakeMedia();
    media.env.getUserMedia.mockRejectedValueOnce(new DOMException("no", "NotAllowedError"));
    await expect(
      openMicStream({ onFrame: vi.fn() }, { ...media.env, readMute: () => ({ muted: false, readable: true }) }),
    ).rejects.toMatchObject({ code: "mic_denied" });
  });
});
