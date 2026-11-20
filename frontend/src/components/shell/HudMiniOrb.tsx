"use client";

import Link from "next/link";
import { useVoiceWake } from "@/components/voice/VoiceProvider";
import { useWorkspace } from "@/contexts/workspace-context";
import { deriveHudState, HUD_VISUALS } from "@/lib/hud/states";
import { useOnline } from "@/lib/hud/use-hud";
import { tr } from "@/lib/i18n/tr";
import { useActivitySummary } from "@/lib/query/activity";
import { useVoiceMute } from "@/lib/voice/mute";
import { useVoicePresence } from "@/lib/voice/presence";
import { cn } from "@/lib/utils";

/**
 * The shell header's mini-orb (Phase 6 A8): mirrors the tab's global voice
 * state -- the voice presence, wake mode, the mute flag, the browser's
 * online state and the pending approvals of the active workspace (the
 * shell's existing badge feed; no second stream). A link to /hud. No
 * animation frame loop: a CSS pulse only for active states and only when
 * motion is allowed (`motion-safe:`); the state is always in the label.
 */
export function HudMiniOrb({ className, onNavigate }: { className?: string; onNavigate?: () => void }) {
  const presence = useVoicePresence();
  const wake = useVoiceWake();
  const { muted } = useVoiceMute();
  const online = useOnline();
  const { user, loading, activeWorkspace } = useWorkspace();
  const activity = useActivitySummary(!loading && user != null);
  const row = activity.data?.workspaces.find((w) => w.workspace === activeWorkspace?.id);
  const state = deriveHudState({
    phase: presence.phase,
    voiceError: presence.error,
    wakeState: wake.snapshot.state,
    muted,
    streamLost: false,
    online,
    pendingApprovals: row?.pending_approvals ?? 0,
    toolsRunning: 0,
  });
  const visual = HUD_VISUALS[state];
  const label = tr.hud.states[state];
  return (
    <Link
      href="/hud"
      onClick={onNavigate}
      aria-label={tr.hud.miniOrb(label)}
      title={tr.hud.miniOrb(label)}
      data-testid="hud-mini-orb"
      data-state={state}
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn("block size-3.5 rounded-full", visual.active && "motion-safe:animate-pulse")}
        style={{
          backgroundColor: `var(${visual.token})`,
          boxShadow: `0 0 0 3px color-mix(in oklch, var(${visual.token}) 25%, transparent)`,
        }}
      />
    </Link>
  );
}
