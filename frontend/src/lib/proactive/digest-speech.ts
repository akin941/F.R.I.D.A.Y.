"use client";

import { SentenceSegmenter } from "@/lib/voice/segmenter";
import { readMuteState, type MuteState } from "@/lib/voice/mute";

/**
 * "Dinle" (Phase 12 D7, web half): the digest's `spoken_text` is split by
 * the existing sentence segmenter and each sentence goes through the single
 * speak function (`Speaker.speak`, `lib/voice/playback.ts`) -- one
 * `POST /api/voice/speech` per sentence, the mute flag checked inside.
 * Nothing here opens the microphone.
 */

export interface DigestSpeaker {
  speak(text: string): boolean;
  end(): void;
  stop(reason?: "stopped" | "muted"): void;
}

/** The digest text as speakable sentences (the voice segmenter's rules). */
export function digestSentences(text: string): string[] {
  const segmenter = new SentenceSegmenter();
  return [...segmenter.push(text), ...segmenter.flush()].map((s) => s.trim()).filter(Boolean);
}

/**
 * Feed every sentence to `speaker`. Returns the number of sentences queued;
 * 0 when muted (or the mute state is unreadable: fail-closed) -- then no
 * synthesis request is made at all.
 */
export function speakDigest(
  text: string,
  speaker: DigestSpeaker,
  readMute: () => MuteState = readMuteState,
): number {
  if (readMute().muted) return 0;
  let queued = 0;
  for (const sentence of digestSentences(text)) {
    if (!speaker.speak(sentence)) break;
    queued += 1;
  }
  speaker.end();
  return queued;
}

const SPOKEN_ON_OPEN_KEY = "friday:digest:spoken-on-open";

/**
 * `speak_on_open` (D7): the HUD speaks a digest once, the first time it
 * opens after the digest is ready -- only when the schedule asks for it,
 * the digest was not spoken yet (any channel) and this device has not
 * already spoken it on open. Fail-closed on unreadable storage.
 */
export function shouldSpeakOnOpen(
  digest: { id: string; speak_on_open: boolean; spoken: boolean } | null,
  readMute: () => MuteState = readMuteState,
): boolean {
  if (!digest || !digest.speak_on_open || digest.spoken) return false;
  if (readMute().muted) return false;
  try {
    return window.localStorage.getItem(SPOKEN_ON_OPEN_KEY) !== digest.id;
  } catch {
    return false;
  }
}

export function markSpokenOnOpen(digestId: string): void {
  try {
    window.localStorage.setItem(SPOKEN_ON_OPEN_KEY, digestId);
  } catch {
    // Unwritable storage: shouldSpeakOnOpen already reads as "no".
  }
}
