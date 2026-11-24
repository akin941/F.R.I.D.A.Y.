"use client";

import { useEffect, useState } from "react";
import { errorMessage } from "@/lib/api-client";
import { fetchPause, setPause } from "@/lib/api/proactive";

/**
 * "Proaktif çalışmaları duraklat" (Phase 12 D3): the owner's one toggle
 * over `GET/POST/DELETE /api/proactive/pause`. Paused, the heartbeat skips
 * every schedule of the workspace; a run can neither pause nor resume.
 */
export function PauseToggle({ workspaceId }: { workspaceId: string }) {
  const [paused, setPaused] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    /* eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-workspace-change */
    setPaused(null);
    fetchPause(workspaceId)
      .then((value) => {
        if (!cancelled) setPaused(value);
      })
      .catch(() => {
        if (!cancelled) setError("Duraklatma durumu okunamadı.");
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  async function toggle(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      setPaused(await setPause(workspaceId, next));
    } catch (err) {
      setError(errorMessage(err, "Duraklatma değiştirilemedi."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1 text-sm" data-testid="pause-toggle">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={paused ?? false}
          disabled={paused === null || busy}
          onChange={(e) => void toggle(e.target.checked)}
        />
        Proaktif çalışmaları duraklat
      </label>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
