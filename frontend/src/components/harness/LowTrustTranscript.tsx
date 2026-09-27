"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";

/**
 * R2 mitigation (prompt injection): a CLI agent's own raw output is
 * low-trust content. It is never shown by default and never merged with
 * Jarvis's own gateway-produced summaries -- it sits collapsed behind an
 * explicit disclosure, and when opened it renders as preformatted plain
 * text (React escapes it; no markdown/HTML interpretation) under an
 * explicit warning label. Shared by the discovery briefing and the
 * implementation/review transcripts so every surface keeps the same
 * discipline.
 */
export function LowTrustTranscript({
  text,
  showLabel,
  hideLabel,
  warning,
}: {
  text: string;
  showLabel: string;
  hideLabel: string;
  warning: string;
}) {
  const [open, setOpen] = useState(false);
  const regionId = useId();

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? hideLabel : showLabel}
      </Button>
      {open ? (
        <div id={regionId} className="flex flex-col gap-1">
          <Alert>
            <AlertDescription>{warning}</AlertDescription>
          </Alert>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted p-3 text-xs">
            {text}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
