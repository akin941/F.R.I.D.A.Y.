/**
 * Client-side voice knobs (Phase 6 branch 3). Build-time `NEXT_PUBLIC_*`
 * values with the brief's defaults; nothing here is a secret or trust.
 */

function intFromEnv(raw: string | undefined, fallback: number, min: number, max: number): number {
  const value = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, value));
}

/** The wire format (D3): 16 kHz mono PCM16 WAV. */
export const VOICE_SAMPLE_RATE = 16_000;

/**
 * End-of-speech finish window (A7): after the owner lets go of the mic,
 * Friday waits this long before dispatching the turn, so a mid-sentence
 * pause (press again within the window) appends instead of dispatching.
 */
export const VOICE_FINISH_MS = intFromEnv(process.env.NEXT_PUBLIC_VOICE_FINISH_MS, 900, 0, 5_000);

/** The utterance cap (D2), mirrored from the backend's default. */
export const VOICE_MAX_UTTERANCE_SECONDS = intFromEnv(
  process.env.NEXT_PUBLIC_VOICE_MAX_UTTERANCE_SECONDS,
  30,
  1,
  30,
);

/** A press shorter than this is a toggle (click), longer is hold-to-talk. */
export const VOICE_HOLD_THRESHOLD_MS = 350;

/** Spoken reply language sent to `/api/voice/speech` (A2 routes by it). */
export const VOICE_LANGUAGE = process.env.NEXT_PUBLIC_VOICE_LANGUAGE || "tr";
