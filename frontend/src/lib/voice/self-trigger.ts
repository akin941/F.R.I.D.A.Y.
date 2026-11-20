import { VOICE_SELF_TRIGGER_MS } from "@/lib/voice/config";

/**
 * The A6 self-trigger rule (ADR-015): a wake detection within 1.5 s after
 * Friday stopped speaking, whose transcript matches what Friday just said,
 * is Friday hearing itself (echo past half-duplex). Such a turn is
 * discarded -- no message, no proposal, nothing spoken.
 */

export interface SpokenRecord {
  text: string;
  at: number;
}

/** How long spoken sentences are remembered for the comparison. */
export const SPOKEN_MEMORY_MS = 60_000;

export function normalizeSpeech(text: string): string {
  return text
    .toLocaleLowerCase("tr-TR")
    .normalize("NFC")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stripWakePhrase(text: string, phrase: string): string {
  const p = normalizeSpeech(phrase);
  return p && text.startsWith(p) ? text.slice(p.length).trim() : text;
}

/** True when the transcript is (mostly) a repeat of something Friday said recently. */
export function matchesRecentSpeech(
  transcript: string,
  recent: readonly SpokenRecord[],
  wakePhrase = "Hey Friday",
): boolean {
  const heard = stripWakePhrase(normalizeSpeech(transcript), wakePhrase);
  if (!heard) return false;
  const heardTokens = heard.split(" ");
  for (const record of recent) {
    const said = stripWakePhrase(normalizeSpeech(record.text), wakePhrase);
    if (!said) continue;
    if (said.includes(heard) || heard.includes(said)) return true;
    const saidTokens = new Set(said.split(" "));
    const overlap = heardTokens.filter((t) => saidTokens.has(t)).length;
    if (heardTokens.length >= 2 && overlap / heardTokens.length >= 0.6) return true;
  }
  return false;
}

/** The detection came within the self-trigger window after playback ended. */
export function withinSelfTriggerWindow(
  detectedAt: number,
  playbackEndedAt: number | null,
  windowMs = VOICE_SELF_TRIGGER_MS,
): boolean {
  if (playbackEndedAt === null) return false;
  const delta = detectedAt - playbackEndedAt;
  return delta >= 0 && delta <= windowMs;
}
