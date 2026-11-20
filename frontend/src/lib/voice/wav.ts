import { VOICE_SAMPLE_RATE } from "@/lib/voice/config";

/**
 * The client-side WAV encoder (Phase 6 D3): canonical 44-byte RIFF/WAVE
 * header + 16-bit little-endian mono PCM. The backend validates exactly this
 * shape (`ai_core.audio.parse_stt_input`): PCM, 16-bit, mono, 16 kHz, the
 * declared `data` size equal to the bytes sent, no trailing bytes.
 */
export const WAV_HEADER_BYTES = 44;

function writeAscii(view: DataView, offset: number, text: string) {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
}

export function encodeWav(pcm: Int16Array, sampleRate = VOICE_SAMPLE_RATE): Uint8Array<ArrayBuffer> {
  const dataBytes = pcm.length * 2;
  const buffer = new ArrayBuffer(WAV_HEADER_BYTES + dataBytes);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);
  for (let i = 0; i < pcm.length; i += 1) view.setInt16(WAV_HEADER_BYTES + i * 2, pcm[i] ?? 0, true);
  return new Uint8Array(buffer);
}

/** Float samples in [-1, 1] -> clamped PCM16. */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

export function concatPcm(chunks: Int16Array[]): Int16Array {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Int16Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

export function pcmSeconds(samples: number, sampleRate = VOICE_SAMPLE_RATE): number {
  return samples / sampleRate;
}
