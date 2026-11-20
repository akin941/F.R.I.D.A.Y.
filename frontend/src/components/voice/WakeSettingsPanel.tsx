"use client";

import { useId } from "react";
import { Radio } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useVoiceWake } from "@/components/voice/VoiceProvider";
import { tr } from "@/lib/i18n/tr";
import {
  VOICE_WAKE_MAX_THRESHOLD,
  VOICE_WAKE_MIN_THRESHOLD,
} from "@/lib/voice/config";

/**
 * Wake-mode controls on /voice (Phase 6 branch 4): the arm/disarm toggle
 * (a click is the user gesture D6 requires; push-to-talk stays the default,
 * d3), the per-device detection threshold and idle-disarm minutes.
 */

function wakeErrorText(code: string): string {
  return tr.voice.wake.errors[code] ?? tr.voice.errors[code] ?? tr.voice.wake.state.error!;
}

export function WakeSettingsPanel({ disabled }: { disabled?: boolean }) {
  const wake = useVoiceWake();
  const id = useId();
  if (!wake.available) return null;
  const { state, error, notice } = wake.snapshot;
  const on = state === "armed" || state === "capturing" || state === "paused";
  const loading = state === "loading";

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex w-full max-w-xl flex-col gap-3 rounded-xl border p-4 text-sm"
      data-testid="wake-settings"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${id}-heading`} className="flex items-center gap-2 font-medium">
          <Radio aria-hidden className="size-4" />
          {tr.voice.wake.heading}
        </h2>
        <Button
          type="button"
          size="sm"
          variant={on ? "default" : "outline"}
          aria-pressed={on}
          disabled={loading || (disabled && !on)}
          data-testid="wake-toggle"
          onClick={(event) => {
            if (on) wake.disarm();
            else void wake.arm({ type: event.type });
          }}
        >
          {on ? tr.voice.wake.disarm : tr.voice.wake.arm(wake.phrase)}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{tr.voice.wake.description(wake.phrase)}</p>
      <p className="text-xs text-muted-foreground">{tr.voice.wake.defaultHint}</p>
      <p role="status" aria-live="polite" className="text-xs" data-testid="wake-state">
        {tr.voice.wake.state[state]}
      </p>
      {state === "error" && error ? (
        <p role="alert" className="text-xs text-destructive" data-testid="wake-error">
          {wakeErrorText(error)}
        </p>
      ) : null}
      {notice ? (
        <p className="text-xs text-muted-foreground" data-testid="wake-notice">
          {tr.voice.wake.notices[notice]}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-threshold`}>
          <span>
            {tr.voice.wake.threshold}: {wake.settings.threshold.toFixed(2)}
          </span>
          <input
            id={`${id}-threshold`}
            type="range"
            min={VOICE_WAKE_MIN_THRESHOLD}
            max={VOICE_WAKE_MAX_THRESHOLD}
            step={0.05}
            value={wake.settings.threshold}
            disabled={on || loading}
            aria-describedby={`${id}-threshold-hint`}
            data-testid="wake-threshold"
            onChange={(e) => wake.updateSettings({ threshold: Number(e.target.value) })}
          />
          <span id={`${id}-threshold-hint`} className="text-muted-foreground">
            {tr.voice.wake.thresholdHint}
          </span>
        </label>
        <label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-idle`}>
          <span>{tr.voice.wake.idleMinutes}</span>
          <input
            id={`${id}-idle`}
            type="number"
            min={1}
            max={1440}
            value={wake.settings.idleMinutes}
            disabled={on || loading}
            data-testid="wake-idle"
            className="h-8 w-24 rounded-md border bg-background px-2"
            onChange={(e) => {
              const n = Number.parseInt(e.target.value, 10);
              if (Number.isFinite(n)) wake.updateSettings({ idleMinutes: n });
            }}
          />
        </label>
      </div>
    </section>
  );
}
