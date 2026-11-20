"use client";

import { useSyncExternalStore } from "react";

/**
 * The shell's voice mute flag (Phase 6 A6/A7), fail-closed.
 *
 * Read inside the capture path (every captured frame) and inside the single
 * speak function (`playback.ts`). A tab opened with `?mute=1` is always
 * silent (closeout preflight). When the state cannot be read -- storage
 * blocked or throwing -- it reads as MUTED (`readable: false`): the capture
 * path then stops and releases the mic track, and nothing is played.
 */
export const MUTE_STORAGE_KEY = "friday:voice:muted";
const MUTE_EVENT = "friday:voice-mute";

export interface MuteState {
  muted: boolean;
  readable: boolean;
}

function urlForcesMute(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("mute") === "1";
}

export function readMuteState(): MuteState {
  try {
    if (urlForcesMute()) return { muted: true, readable: true };
    const value = window.localStorage.getItem(MUTE_STORAGE_KEY);
    return { muted: value === "1", readable: true };
  } catch {
    return { muted: true, readable: false };
  }
}

/** True when audio must not be captured or played (fail-closed). */
export function isVoiceMuted(): boolean {
  return readMuteState().muted;
}

export function setVoiceMuted(muted: boolean): void {
  try {
    if (muted) window.localStorage.setItem(MUTE_STORAGE_KEY, "1");
    else window.localStorage.removeItem(MUTE_STORAGE_KEY);
  } catch {
    // Unwritable storage reads back as muted anyway (fail-closed).
  }
  window.dispatchEvent(new Event(MUTE_EVENT));
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === MUTE_STORAGE_KEY) onChange();
  };
  window.addEventListener(MUTE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(MUTE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

// useSyncExternalStore needs a stable snapshot: encode the two booleans.
function snapshot(): string {
  const state = readMuteState();
  return `${state.muted ? 1 : 0}${state.readable ? 1 : 0}`;
}

export function useVoiceMute(): MuteState {
  const value = useSyncExternalStore(subscribe, snapshot, () => "01");
  return { muted: value[0] === "1", readable: value[1] === "1" };
}
