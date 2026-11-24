"use client";

import { useEffect, useState } from "react";
import { DigestCard } from "@/components/proactive/DigestCard";
import { PauseToggle } from "@/components/proactive/PauseToggle";
import { ScheduleEditor } from "@/components/proactive/ScheduleEditor";
import { useWorkspace } from "@/contexts/workspace-context";
import { fetchLatestDigest, type Digest } from "@/lib/api/proactive";

/**
 * The `/today` proactive block (Phase 12 D20): the latest digest card
 * ("Dinle", "Ayrıntı"), the schedule editor and the pause toggle.
 */
export function TodayDigestSection() {
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const [digest, setDigest] = useState<Digest | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    /* eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-workspace-change */
    setDigest(null);
    fetchLatestDigest(workspaceId)
      .then((value) => {
        if (!cancelled) setDigest(value);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  if (!workspaceId) return null;
  return (
    <section className="flex flex-col gap-4" data-testid="today-digest">
      {failed ? <p className="text-sm text-muted-foreground">Brifing yüklenemedi.</p> : null}
      <DigestCard
        digest={
          digest
            ? {
                id: digest.id,
                created_at: digest.created_at,
                spoken_text: digest.spoken_text,
                speak_on_open: digest.speak_on_open,
                spoken: digest.spoken_at !== null,
                sections: digest.sections,
              }
            : null
        }
        workspaceId={workspaceId}
        variant="today"
        ackOpened
      />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <ScheduleEditor workspaceId={workspaceId} />
        <PauseToggle workspaceId={workspaceId} />
      </div>
    </section>
  );
}
