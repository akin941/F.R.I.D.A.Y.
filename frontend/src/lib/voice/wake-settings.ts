"use client";

import {
  VOICE_WAKE_DEFAULT_THRESHOLD,
  VOICE_WAKE_IDLE_MINUTES,
  VOICE_WAKE_MAX_THRESHOLD,
  VOICE_WAKE_MIN_THRESHOLD,
} from "@/lib/voice/config";

/**
 * Per-device wake settings (Phase 6 branch 4): the detector threshold and
 * the idle-disarm minutes, kept in localStorage like the other voice
 * conveniences. Nothing here is trust-bearing.
 *
 * Deliberately NOT stored: whether wake mode is armed. Arming needs a user
 * gesture in this tab and is never restored on page load (D6).
 */

export const WAKE_THRESHOLD_KEY = "friday:voice:wake-threshold";
export const WAKE_IDLE_KEY = "friday:voice:wake-idle-minutes";

export interface WakeSettings {
  threshold: number;
  idleMinutes: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function loadWakeSettings(): WakeSettings {
  let threshold = VOICE_WAKE_DEFAULT_THRESHOLD;
  let idleMinutes = VOICE_WAKE_IDLE_MINUTES;
  try {
    const t = Number.parseFloat(window.localStorage.getItem(WAKE_THRESHOLD_KEY) ?? "");
    if (Number.isFinite(t)) threshold = clamp(t, VOICE_WAKE_MIN_THRESHOLD, VOICE_WAKE_MAX_THRESHOLD);
    const m = Number.parseInt(window.localStorage.getItem(WAKE_IDLE_KEY) ?? "", 10);
    if (Number.isFinite(m)) idleMinutes = clamp(m, 1, 24 * 60);
  } catch {
    // Defaults.
  }
  return { threshold, idleMinutes };
}

export function saveWakeSettings(settings: Partial<WakeSettings>): WakeSettings {
  const next = { ...loadWakeSettings(), ...settings };
  next.threshold = clamp(next.threshold, VOICE_WAKE_MIN_THRESHOLD, VOICE_WAKE_MAX_THRESHOLD);
  next.idleMinutes = clamp(Math.round(next.idleMinutes), 1, 24 * 60);
  try {
    window.localStorage.setItem(WAKE_THRESHOLD_KEY, String(next.threshold));
    window.localStorage.setItem(WAKE_IDLE_KEY, String(next.idleMinutes));
  } catch {
    // Per-device convenience only.
  }
  return next;
}
