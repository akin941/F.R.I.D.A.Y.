import { describe, expect, it } from "vitest";
import { concatPcm, encodeWav, floatToPcm16, pcmSeconds, WAV_HEADER_BYTES } from "@/lib/voice/wav";

function ascii(bytes: Uint8Array, start: number, length: number) {
  return String.fromCharCode(...bytes.slice(start, start + length));
}

describe("encodeWav", () => {
  it("writes the canonical 16 kHz mono PCM16 header byte for byte", () => {
    const wav = encodeWav(new Int16Array([0, 1, -1, 32767, -32768]));
    const view = new DataView(wav.buffer);
    expect(wav.length).toBe(WAV_HEADER_BYTES + 10);
    expect(ascii(wav, 0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(36 + 10);
    expect(ascii(wav, 8, 4)).toBe("WAVE");
    expect(ascii(wav, 12, 4)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint32(28, true)).toBe(32_000);
    expect(view.getUint16(32, true)).toBe(2);
    expect(view.getUint16(34, true)).toBe(16);
    expect(ascii(wav, 36, 4)).toBe("data");
    // The declared data size equals the bytes actually present (D3).
    expect(view.getUint32(40, true)).toBe(wav.length - WAV_HEADER_BYTES);
    expect([...wav.slice(44)]).toEqual([0, 0, 1, 0, 0xff, 0xff, 0xff, 0x7f, 0x00, 0x80]);
  });

  it("an empty utterance is a valid header-only WAV", () => {
    const wav = encodeWav(new Int16Array(0));
    expect(wav.length).toBe(44);
    expect(new DataView(wav.buffer).getUint32(40, true)).toBe(0);
  });
});

describe("PCM helpers", () => {
  it("clamps floats into PCM16", () => {
    expect([...floatToPcm16(new Float32Array([0, 1, -1, 2, -2, 0.5]))]).toEqual([
      0, 32767, -32768, 32767, -32768, 16384,
    ]);
  });

  it("concatenates chunks and measures seconds", () => {
    const pcm = concatPcm([new Int16Array([1, 2]), new Int16Array([3])]);
    expect([...pcm]).toEqual([1, 2, 3]);
    expect(pcmSeconds(32_000)).toBe(2);
  });
});
