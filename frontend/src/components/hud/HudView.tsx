"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { X } from "lucide-react";
import { HudLearningPanel, HudSessionsPanel } from "@/components/hud/HudLearningPanels";
import { HudNoticesPanel } from "@/components/hud/HudNoticesPanel";
import { HudOrb } from "@/components/hud/HudOrb";
import { DigestCard } from "@/components/proactive/DigestCard";
import type { DigestPlayerDeps } from "@/components/proactive/use-digest-player";
import { HudTerminalPanel, type TerminalStreamFactory } from "@/components/hud/HudTerminalPanel";
import {
  ApprovalsPanel,
  CaptionsPanel,
  CostPanel,
  HealthPanel,
  TasksPanel,
  type MicState,
} from "@/components/hud/HudPanels";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { localDateString } from "@/components/tasks/TodayView";
import { useVoiceWake } from "@/components/voice/VoiceProvider";
import { VoiceView } from "@/components/voice/VoiceView";
import { useWorkspace } from "@/contexts/workspace-context";
import { browserAudioContext, EarconPlayer, earconFor, type EarconPlayerDeps } from "@/lib/hud/earcons";
import { frameBudget } from "@/lib/hud/frame-loop";
import { loadHudSettings, saveHudSettings, type HudSettings, DEFAULT_HUD_SETTINGS } from "@/lib/hud/settings";
import { deriveHudState, HUD_VISUALS, type HudState } from "@/lib/hud/states";
import { useHudEnvironment, useHudEvents, useOnline, type HudStreamFactory } from "@/lib/hud/use-hud";
import { tr } from "@/lib/i18n/tr";
import { isVoiceMuted, useVoiceMute } from "@/lib/voice/mute";
import { readVoiceLevel, useVoicePresence } from "@/lib/voice/presence";
import { cn } from "@/lib/utils";

/**
 * HUD v1 (Phase 6 brief A8): a full-screen view over the shell, driven by
 * one event stream (approvals, tasks, cost, health) plus the tab's voice
 * presence. The push-to-talk loop is the same `VoiceView` as /voice, in its
 * `hud` variant (controls only), so speaking from the HUD is the same
 * unchanged chat turn. Read-only otherwise: approvals are deep links to
 * `/approvals/<id>`, there is no decide control (D8).
 *
 * Accessibility: every state is a text label announced through
 * `aria-live`; captions are always available; reduced motion -> a static
 * orb whose colour (and the text) carries the state; frame caps 30 / 5 fps,
 * 0 when the tab is hidden; on battery no resting animation unless the
 * owner turns it on for this device.
 */
export interface HudViewProps {
  /** Test seams. */
  streamFactory?: HudStreamFactory;
  terminalStreamFactory?: TerminalStreamFactory;
  webglAvailable?: () => boolean;
  earconDeps?: Partial<EarconPlayerDeps>;
  /** Phase 12: the digest player's speech seam. */
  digestPlayerDeps?: DigestPlayerDeps;
}

function micState(muted: boolean, phase: string, wake: string): MicState {
  if (muted) return "muted";
  if (phase === "listening") return "open";
  if (wake === "armed" || wake === "capturing" || wake === "paused") return "wake";
  return "closed";
}

export function HudView({
  streamFactory,
  terminalStreamFactory,
  webglAvailable,
  earconDeps,
  digestPlayerDeps,
}: HudViewProps = {}) {
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const presence = useVoicePresence();
  const wake = useVoiceWake();
  const { muted } = useVoiceMute();
  const online = useOnline();
  const env = useHudEnvironment();
  const [today] = useState(() => localDateString(new Date()));
  const { data, status } = useHudEvents(workspaceId, today, streamFactory);
  const [settings, setSettings] = useState<HudSettings>(DEFAULT_HUD_SETTINGS);

  useEffect(() => {
    /* eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of per-device settings after mount */
    setSettings(loadHudSettings());
  }, []);

  const state: HudState = deriveHudState({
    phase: presence.phase,
    voiceError: presence.error,
    wakeState: wake.snapshot.state,
    muted,
    streamLost: status === "lost",
    online,
    pendingApprovals: data?.approvals.pending_count ?? 0,
    // Phase 10 D12: a node action `running` on the Mac is a running tool.
    toolsRunning: (data?.approvals.tools_running ?? 0) + (data?.terminal?.running_count ?? 0),
    // Phase 11 D15: the Mac's live voice, when the browser has none.
    nodeVoice: data?.health?.node?.voice?.state ?? null,
  });
  // With the browser idle, a live voice state on the ring is the Mac's.
  const fromNode =
    presence.phase === "idle" && (state === "listening" || state === "thinking" || state === "speaking");
  const visual = HUD_VISUALS[state];
  const fps = frameBudget({
    active: visual.active,
    reducedMotion: env.reducedMotion,
    hidden: env.hidden,
    onBattery: env.onBattery,
    animateOnBattery: settings.animateOnBattery,
  });

  // Earcons on a few transitions (never muted, never before a gesture).
  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);
  /* eslint-disable react-hooks/refs -- the ref is read at play time, not during render */
  const [player] = useState(
    () =>
      new EarconPlayer({
        createContext: browserAudioContext,
        muted: isVoiceMuted,
        enabled: () => settingsRef.current.earcons,
        ...earconDeps,
      }),
  );
  /* eslint-enable react-hooks/refs */
  const previousRef = useRef<HudState | null>(null);
  useEffect(() => {
    const earcon = earconFor(previousRef.current, state);
    previousRef.current = state;
    if (earcon) player.play(earcon);
  }, [state, player]);
  useEffect(() => {
    const unlock = () => player.unlock();
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, [player]);

  const label = tr.hud.states[state];
  const mic = micState(muted, presence.phase, wake.snapshot.state);
  const level = useMemo(() => () => readVoiceLevel(), []);

  function update(next: Partial<HudSettings>) {
    setSettings(saveHudSettings(next));
  }

  return (
    <div
      // Full screen: the shell hides its sidebar on /hud (Sidebar.tsx).
      className={cn(VIEW_SCROLL_ROOT, "bg-hud-surface text-hud-caption")}
      data-testid="hud"
      data-state={state}
      data-fps={fps}
    >
      <header className="flex items-center justify-between gap-2 px-4 pt-3 sm:px-6">
        <h1 className="text-sm font-semibold">{tr.hud.title}</h1>
        <Link
          href="/voice"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-hud-caption-muted hover:text-hud-caption"
        >
          <X aria-hidden className="size-4" />
          {tr.hud.exit}
        </Link>
      </header>

      <div className="grid flex-1 grid-cols-1 gap-3 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="order-2 flex flex-col gap-3 lg:order-1">
          <TasksPanel tasks={data?.tasks ?? null} />
          <ApprovalsPanel approvals={data?.approvals ?? null} />
          <HudTerminalPanel terminal={data?.terminal ?? null} streamFactory={terminalStreamFactory} />
        </div>

        <div className="order-1 flex flex-col items-center gap-3 lg:order-2">
          <HudOrb
            state={state}
            fps={fps}
            reducedMotion={env.reducedMotion}
            level={level}
            size={240}
            webglAvailable={webglAvailable}
            source={fromNode ? "node" : "browser"}
          />
          <p className="flex items-center gap-2 text-lg font-semibold" data-testid="hud-state-label">
            <span
              aria-hidden
              className="inline-block size-3 rounded-full"
              style={{ backgroundColor: `var(${visual.token})` }}
            />
            <span style={{ color: `var(${visual.token})` }}>{label}</span>
            {fromNode ? (
              <span className="text-xs text-hud-caption-muted" data-testid="hud-state-source">
                {tr.hud.fromNode}
              </span>
            ) : null}
          </p>
          {/* Every state is announced (A8). */}
          <p className="sr-only" role="status" aria-live="polite" data-testid="hud-state-announcement">
            {tr.hud.stateAnnouncement(label)}
          </p>
          <CaptionsPanel presence={presence} />
          {workspaceId ? (
            <VoiceView variant="hud" />
          ) : (
            <p className="text-sm text-hud-caption-muted">{tr.hud.noWorkspace}</p>
          )}
        </div>

        <div className="order-3 flex flex-col gap-3">
          {/* Phase 12 D7/D20: the digest card ("Dinle", speak_on_open) and notices. */}
          <DigestCard
            digest={data?.digest ?? null}
            workspaceId={workspaceId}
            variant="hud"
            autoSpeak
            playerDeps={digestPlayerDeps}
          />
          <HudNoticesPanel notices={data?.notices ?? null} />
          <CostPanel cost={data?.cost ?? null} />
          <HealthPanel health={data?.health ?? null} mic={mic} stream={status} />
          {/* Phase 8A branch 6: the sessions panel and the morning-card summary. */}
          <HudSessionsPanel />
          <HudLearningPanel />
          <fieldset className={cn("flex flex-col gap-1 rounded-xl border border-border/60 p-3 text-xs")}>
            <legend className="px-1 text-hud-caption-muted">{tr.hud.settings.heading}</legend>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.animateOnBattery}
                onChange={(e) => update({ animateOnBattery: e.target.checked })}
              />
              {tr.hud.settings.animateOnBattery}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={settings.earcons} onChange={(e) => update({ earcons: e.target.checked })} />
              {tr.hud.settings.earcons}
            </label>
          </fieldset>
        </div>
      </div>
    </div>
  );
}
