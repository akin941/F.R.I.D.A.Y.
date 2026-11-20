"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import {
  HudStream,
  hudEventsUrl,
  type HudData,
  type HudStreamCallbacks,
  type HudStreamStatus,
} from "@/lib/hud/events";
import { watchBattery, watchHidden, watchReducedMotion } from "@/lib/hud/frame-loop";

export type HudStreamFactory = (url: string, callbacks: HudStreamCallbacks) => Pick<HudStream, "start" | "stop">;

const defaultFactory: HudStreamFactory = (url, callbacks) => new HudStream(url, callbacks);

/** The HUD event stream for one workspace (reconnects on its own). */
export function useHudEvents(
  workspaceId: string | null,
  date: string,
  factory: HudStreamFactory = defaultFactory,
): { data: HudData | null; status: HudStreamStatus } {
  const [data, setData] = useState<HudData | null>(null);
  const [status, setStatus] = useState<HudStreamStatus>("connecting");

  useEffect(() => {
    if (!workspaceId) return;
    /* eslint-disable react-hooks/set-state-in-effect -- a new workspace starts from an empty HUD */
    setData(null);
    setStatus("connecting");
    /* eslint-enable react-hooks/set-state-in-effect */
    const stream = factory(hudEventsUrl(workspaceId, date), { onData: setData, onStatus: setStatus });
    stream.start();
    return () => stream.stop();
  }, [workspaceId, date, factory]);

  return { data, status };
}

function subscribeOnline(listener: () => void) {
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
  };
}

/** `navigator.onLine`, live (true during prerender). */
export function useOnline(): boolean {
  return useSyncExternalStore(
    subscribeOnline,
    () => navigator.onLine,
    () => true,
  );
}

/** Reduced motion, hidden tab and battery, live. */
export function useHudEnvironment(): { reducedMotion: boolean; hidden: boolean; onBattery: boolean } {
  const [reducedMotion, setReducedMotion] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [onBattery, setOnBattery] = useState(false);
  useEffect(() => watchReducedMotion(setReducedMotion), []);
  useEffect(() => watchHidden(setHidden), []);
  useEffect(() => watchBattery(setOnBattery), []);
  return { reducedMotion, hidden, onBattery };
}
