"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MicOff, Radio } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AUTH_LOST_EVENT } from "@/lib/auth-events";
import { tr } from "@/lib/i18n/tr";
import { openMicStream } from "@/lib/voice/mic-stream";
import { loadVerifiedModels } from "@/lib/voice/model-loader";
import { runtimeBasePath, wakePhrase } from "@/lib/voice/model-manifest";
import { readMuteState, setVoiceMuted, useVoiceMute } from "@/lib/voice/mute";
import { holdListeningTitle } from "@/lib/voice/title-indicator";
import {
  WakeMode,
  type UserGesture,
  type WakeModeDeps,
  type WakeSnapshot,
  type WakeTurnTarget,
} from "@/lib/voice/wake-mode";
import { createBrowserWakePort } from "@/lib/voice/wake-port";
import { loadWakeSettings, saveWakeSettings, type WakeSettings } from "@/lib/voice/wake-settings";

/**
 * The shell-level voice provider (Phase 6 branch 4, D6). It owns wake mode
 * for this tab, so navigating between pages keeps it armed; the /voice page
 * registers its voice loop as the target that receives a detection's
 * utterance (a detection elsewhere navigates to /voice first).
 *
 * Push-to-talk stays the default (owner decision d3): wake mode starts
 * `off` on every page load, arms only from a click, and disarms on logout /
 * a hard 401, mute, idle, a wake storm, or the toggle. While the mic is open
 * the shell shows a persistent indicator and the tab title is prefixed.
 */

export interface VoiceWakeContext {
  snapshot: WakeSnapshot;
  phrase: string;
  settings: WakeSettings;
  arm(gesture: UserGesture): Promise<boolean>;
  disarm(): void;
  registerTarget(target: WakeTurnTarget | null): void;
  updateSettings(next: Partial<WakeSettings>): void;
  available: boolean;
}

const OFF: WakeSnapshot = { state: "off", error: null, notice: null };

const NOOP_CONTEXT: VoiceWakeContext = {
  snapshot: OFF,
  phrase: wakePhrase(),
  settings: { threshold: 0.5, idleMinutes: 60 },
  arm: async () => false,
  disarm: () => undefined,
  registerTarget: () => undefined,
  updateSettings: () => undefined,
  available: false,
};

const Context = createContext<VoiceWakeContext>(NOOP_CONTEXT);

export function useVoiceWake(): VoiceWakeContext {
  return useContext(Context);
}

function micOpen(state: WakeSnapshot["state"]) {
  return state === "armed" || state === "capturing" || state === "paused";
}

export interface VoiceProviderProps {
  children: React.ReactNode;
  /** Test seam: replace wake mode's model/mic/worker edges. */
  depsOverride?: Partial<WakeModeDeps>;
}

export function VoiceProvider({ children, depsOverride }: VoiceProviderProps) {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<WakeSnapshot>(OFF);
  const [settings, setSettings] = useState<WakeSettings>(NOOP_CONTEXT.settings);
  const settingsRef = useRef<WakeSettings>(settings);
  const routerRef = useRef(router);
  const { muted } = useVoiceMute();

  useEffect(() => {
    routerRef.current = router;
  }, [router]);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- one-time read of per-device settings after mount */
    const loaded = loadWakeSettings();
    settingsRef.current = loaded;
    setSettings(loaded);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);

  /* eslint-disable react-hooks/refs -- refs are read at call time, not during render */
  const [wake] = useState(
    () =>
      new WakeMode({
        verifyModels: () => loadVerifiedModels(),
        openStream: (callbacks) => openMicStream(callbacks),
        createPort: createBrowserWakePort,
        runtimePath: runtimeBasePath(),
        readMute: readMuteState,
        setMuted: setVoiceMuted,
        settings: () => settingsRef.current,
        onSnapshot: setSnapshot,
        onNeedTarget: () => routerRef.current.push("/voice"),
        ...depsOverride,
      }),
  );
  /* eslint-enable react-hooks/refs */

  // Leaving the shell (logout navigation, unmount) closes the mic.
  useEffect(() => () => wake.dispose(), [wake]);

  // Logout / a hard 401 / a revoked device (D6).
  useEffect(() => {
    const onAuthLost = () => wake.disarm("auth_lost");
    window.addEventListener(AUTH_LOST_EVENT, onAuthLost);
    return () => window.removeEventListener(AUTH_LOST_EVENT, onAuthLost);
  }, [wake]);

  // The shell mute (A6): muting closes the wake mic too.
  useEffect(() => {
    if (muted && wake.micOpen) wake.disarm("muted");
  }, [muted, wake]);

  const open = micOpen(snapshot.state);
  useEffect(() => {
    if (!open) return;
    return holdListeningTitle();
  }, [open]);

  const arm = useCallback((gesture: UserGesture) => wake.arm(gesture), [wake]);
  const disarm = useCallback(() => wake.disarm(), [wake]);
  const registerTarget = useCallback((target: WakeTurnTarget | null) => wake.registerTarget(target), [wake]);
  const updateSettings = useCallback((next: Partial<WakeSettings>) => {
    const saved = saveWakeSettings(next);
    settingsRef.current = saved;
    setSettings(saved);
  }, []);

  const value = useMemo<VoiceWakeContext>(
    () => ({
      snapshot,
      phrase: wakePhrase(),
      settings,
      arm,
      disarm,
      registerTarget,
      updateSettings,
      available: true,
    }),
    [snapshot, settings, arm, disarm, registerTarget, updateSettings],
  );

  return (
    <Context.Provider value={value}>
      {children}
      <WakeIndicator snapshot={snapshot} phrase={value.phrase} onDisarm={disarm} />
    </Context.Provider>
  );
}

/** The persistent mic indicator while wake mode holds the mic open (D6). */
export function WakeIndicator({
  snapshot,
  phrase,
  onDisarm,
}: {
  snapshot: WakeSnapshot;
  phrase: string;
  onDisarm: () => void;
}) {
  if (!micOpen(snapshot.state)) return null;
  const label =
    snapshot.state === "paused"
      ? tr.voice.wake.indicatorPaused
      : snapshot.state === "capturing"
        ? tr.voice.wake.indicatorCapturing
        : tr.voice.wake.indicator(phrase);
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="wake-indicator"
      data-state={snapshot.state}
      className="fixed right-3 bottom-3 z-50 flex items-center gap-2 rounded-full border bg-background/95 py-1 pr-1 pl-3 text-xs shadow-md"
    >
      <Radio aria-hidden className="size-3.5 text-red-600 dark:text-red-400" />
      <span>{label}</span>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 rounded-full"
        onClick={onDisarm}
        aria-label={tr.voice.wake.disarm}
        data-testid="wake-indicator-disarm"
      >
        <MicOff aria-hidden className="size-3.5" />
      </Button>
    </div>
  );
}
