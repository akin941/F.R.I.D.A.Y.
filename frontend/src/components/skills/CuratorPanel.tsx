"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api-client";
import {
  ARCHIVE_REASON_LABELS,
  archiveSkill,
  dismissSuggestion,
  fetchCurator,
  type CuratorState,
} from "@/lib/api/skills";

const OUTCOME_LABELS: Record<string, string> = {
  running: "çalışıyor",
  proposals: "öneri var",
  quiet: "öneri yok",
  no_skills: "beceri yok",
  skipped_cap: "bekleyen öneri sınırı dolu",
  skipped_budget: "bütçe sınırı",
  no_prompt: "istem yok",
  no_provider: "uygun model yok",
  gateway_error: "model hatası",
  parse_error: "yanıt okunamadı",
  denied: "engellendi",
  failed: "başarısız",
};

/**
 * The weekly curator on `/skills` (Phase 12 D16): the latest run's counts
 * and its archive suggestions. Archiving is the owner's one click on one
 * skill; a suggestion can also be dismissed. Edit and merge proposals are
 * pending versions decided on each skill's page. Nothing here is applied
 * by the curator itself.
 */
export function CuratorPanel({ workspaceId, onChanged }: { workspaceId: string; onChanged?: () => void }) {
  const [state, setState] = useState<CuratorState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const current = useRef<string | null>(null);

  const load = useCallback(async (id: string) => {
    try {
      const next = await fetchCurator(id);
      if (current.current === id) setState(next);
    } catch {
      // The curator panel is secondary: the skill list works without it.
      if (current.current === id) setState(null);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-workspace-change */
  useEffect(() => {
    current.current = workspaceId;
    setState(null);
    setError(null);
    void load(workspaceId);
  }, [workspaceId, load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function run(action: () => Promise<unknown>, failure: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load(workspaceId);
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err, failure));
    } finally {
      setBusy(false);
    }
  }

  if (state === null || (state.latest_run === null && state.suggestions.length === 0)) return null;
  const latest = state.latest_run;

  return (
    <Card data-testid="skills-curator">
      <CardHeader>
        <CardTitle className="text-base">Haftalık küratör</CardTitle>
        {latest ? (
          <p className="text-sm text-muted-foreground">
            {latest.week}: {OUTCOME_LABELS[latest.outcome] ?? latest.outcome} · {latest.stale_marked} beceri
            kullanılmıyor olarak işaretlendi · {latest.proposals_created} sürüm önerisi
          </p>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {state.suggestions.length === 0 ? (
          <p className="text-sm text-muted-foreground">Açık arşiv önerisi yok.</p>
        ) : (
          <ul className="flex flex-col gap-2" aria-label="Arşiv önerileri">
            {state.suggestions.map((s) => (
              <li
                key={`${s.run_id}:${s.skill_id}`}
                data-testid={`suggestion-${s.skill_name}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm"
              >
                <span className="flex flex-col">
                  <Link href={`/skills/${s.skill_id}`} className="font-medium underline underline-offset-2">
                    {s.skill_name}
                  </Link>
                  <span className="text-xs text-muted-foreground">
                    {ARCHIVE_REASON_LABELS[s.reason] ?? s.reason}
                    {s.into_skill_name ? ` (${s.into_skill_name})` : ""} · {s.use_count} kullanım
                  </span>
                </span>
                <span className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void run(() => archiveSkill(s.skill_id), "Arşivlenemedi.")}
                  >
                    Arşivle
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void run(() => dismissSuggestion(s.run_id, s.skill_id), "Yoksayılamadı.")}
                  >
                    Yoksay
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
