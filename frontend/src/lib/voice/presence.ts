"use client";

import { useSyncExternalStore } from "react";
import type { VoicePhase } from "@/lib/voice/voice-machine";

/**
 * The tab-wide voice presence (Phase 6 branch 5, A8): what the mounted
 * voice loop is doing right now, for the views that only MIRROR it -- the
 * shell mini-orb and the /hud orb and captions. The loop that owns the mic
 * (`VoiceView`, on /voice or embedded in /hud) is the only publisher; the
 * mirrors read it and never act on it. Nothing here leaves the tab: it is
 * in-memory only, never persisted, and cleared when the loop unmounts.
 */
export interface VoicePresence {
  phase: VoicePhase;
  /** The transcript of the current turn (live captions). */
  heard: string;
  /** Friday's reply as it streams (live captions). */
  reply: string;
  /** The voice error code while `phase === "error"`. */
  error: string | null;
  /** True while a voice loop is mounted somewhere in this tab. */
  active: boolean;
}

export const IDLE_PRESENCE: VoicePresence = {
  phase: "idle",
  heard: "",
  reply: "",
  error: null,
  active: false,
};

let current: VoicePresence = IDLE_PRESENCE;
let levelSource: (() => number) | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function same(a: VoicePresence, b: VoicePresence) {
  return (
    a.phase === b.phase &&
    a.heard === b.heard &&
    a.reply === b.reply &&
    a.error === b.error &&
    a.active === b.active
  );
}

export function publishVoicePresence(
  next: Omit<VoicePresence, "active">,
  level?: () => number,
): void {
  if (level) levelSource = level;
  const value: VoicePresence = { ...next, active: true };
  if (same(value, current)) return;
  current = value;
  emit();
}

/** The loop unmounted: back to idle, no level source. */
export function clearVoicePresence(): void {
  levelSource = null;
  if (same(current, IDLE_PRESENCE)) return;
  current = IDLE_PRESENCE;
  emit();
}

export function readVoicePresence(): VoicePresence {
  return current;
}

/** The current audio level 0..1 (mic while listening, playback while
 * speaking); 0 when no loop is mounted. Read per frame by the orbs. */
export function readVoiceLevel(): number {
  try {
    const value = levelSource?.() ?? 0;
    return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  } catch {
    return 0;
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useVoicePresence(): VoicePresence {
  return useSyncExternalStore(subscribe, readVoicePresence, () => IDLE_PRESENCE);
}
