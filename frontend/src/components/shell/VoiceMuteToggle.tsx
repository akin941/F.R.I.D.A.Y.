"use client";

import { Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { tr } from "@/lib/i18n/tr";
import { setVoiceMuted, useVoiceMute } from "@/lib/voice/mute";
import { cn } from "@/lib/utils";

/**
 * The shell's voice mute toggle (Phase 6 A6/A7). The flag is read inside
 * the capture path and inside the single speak function; this is only its
 * switch. An unreadable state shows as muted (fail-closed).
 */
export function VoiceMuteToggle({ className }: { className?: string }) {
  const { muted, readable } = useVoiceMute();
  const label = muted ? tr.voice.unmute : tr.voice.mute;
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-pressed={muted}
      aria-label={label}
      title={muted ? tr.voice.mutedNotice : label}
      data-testid="voice-mute-toggle"
      disabled={!readable}
      onClick={() => setVoiceMuted(!muted)}
      className={cn("justify-start gap-2", className)}
    >
      {muted ? <VolumeX aria-hidden className="size-4" /> : <Volume2 aria-hidden className="size-4" />}
      <span>{label}</span>
    </Button>
  );
}
