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

// --- Wake mode (Phase 6 branch 4, D6/A6/A7) ---------------------------------

/**
 * Wake mode disarms itself after this many minutes without a detection
 * (D6, default 60). Per-device override in the wake settings panel.
 */
export const VOICE_WAKE_IDLE_MINUTES = intFromEnv(
  process.env.NEXT_PUBLIC_VOICE_WAKE_IDLE_MINUTES,
  60,
  1,
  24 * 60,
);

/** After a detection, no speech within this window -> back to armed, nothing uploaded (D6). */
export const VOICE_WAKE_NO_SPEECH_MS = 5_000;

/**
 * End of a wake-mode utterance: VAD silence of at least this long after
 * speech. Aligned with the push-to-talk finish window (A7), so a pause
 * shorter than `VOICE_FINISH_MS` never cuts the turn.
 */
export const VOICE_WAKE_SILENCE_MS = Math.max(300, VOICE_FINISH_MS);

/** Default keyword-score threshold (0..1); per-device in the settings panel. */
export const VOICE_WAKE_DEFAULT_THRESHOLD = 0.5;
export const VOICE_WAKE_MIN_THRESHOLD = 0.2;
export const VOICE_WAKE_MAX_THRESHOLD = 0.95;

/** Pre-wake audio lives only in the Worker, in buffers of at most this many seconds (D6). */
export const VOICE_WAKE_RING_SECONDS = 2;

/** A6 self-trigger rule: a detection this soon after playback ends is suspect. */
export const VOICE_SELF_TRIGGER_MS = 1_500;

/** A6 wake storms: more than this many activations in the window without a dispatched turn -> auto-mute. */
export const VOICE_WAKE_STORM_MAX = 3;
export const VOICE_WAKE_STORM_WINDOW_MS = 60_000;

/** Silero VAD speech-probability threshold. */
export const VOICE_VAD_THRESHOLD = 0.5;

/** A detection that waits this long for the /voice page to mount is dropped. */
export const VOICE_WAKE_HANDOFF_MS = 4_000;
