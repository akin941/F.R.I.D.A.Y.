"use client";

import { useEffect, useRef, useState } from "react";

// Something is actively changing on its own (a worker/container running).
export const POLL_FAST_MS = 3_000;
// Only a human can move it forward (e.g. a gate awaiting the owner's
// decision) -- refresh occasionally in case it was decided elsewhere.
export const POLL_SLOW_MS = 30_000;

/**
 * Calls `callback` every `intervalMs` while `intervalMs` is non-null and
 * the tab is visible. Polling pauses while `document.hidden` (no requests
 * from a background tab) and, on becoming visible again, refreshes once
 * immediately before resuming the interval. The interval only restarts
 * when the cadence or visibility changes -- not on every re-render.
 */
export function usePolling(callback: () => void, intervalMs: number | null) {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  });

  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || !document.hidden,
  );
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  const missedWhileHiddenRef = useRef(false);
  useEffect(() => {
    if (intervalMs == null) return;
    if (!visible) {
      missedWhileHiddenRef.current = true;
      return;
    }
    if (missedWhileHiddenRef.current) {
      missedWhileHiddenRef.current = false;
      callbackRef.current();
    }
    const id = setInterval(() => callbackRef.current(), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, visible]);
}
