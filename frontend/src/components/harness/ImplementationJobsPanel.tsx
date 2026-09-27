"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ImplementationJobForm,
  type ImplementationPrefill,
} from "@/components/harness/ImplementationJobForm";
import {
  GATE_STATUS_LABELS,
  IMPL_STATUS_LABELS,
  harnessFailureReasonLabel,
  isImplTerminal,
} from "@/components/harness/labels";
import { apiGetJson } from "@/lib/api-client";
import { clickableProps } from "@/lib/keyboard";
import type { ApiImplementationJob } from "@/types/api";

/**
 * "Uygulama işleri" list + request form. Same data-loading discipline as
 * HarnessJobsView's discovery list: an `activeWorkspaceIdRef` guard drops a
 * response for a workspace the user already switched away from, and the
 * list polls silently every 3s only while some row is non-terminal.
 *
 * `prefill` (set when the user asked for an implementation from a
 * discovery job) opens the form pre-populated; `onPrefillConsumed` lets
 * the parent clear it once the form is closed or submitted.
 */
export function ImplementationJobsPanel({
  workspaceId,
  prefill,
  onPrefillConsumed,
  onSelect,
}: {
  workspaceId: string;
  prefill: ImplementationPrefill | null;
  onPrefillConsumed: () => void;
  onSelect: (jobId: string) => void;
}) {
  const [jobs, setJobs] = useState<ApiImplementationJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(prefill != null);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadJobs = useCallback(async (wsId: string, options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiImplementationJob[]>(
        `/api/harness/implementation-jobs?workspace=${encodeURIComponent(wsId)}`,
      );
      if (activeWorkspaceIdRef.current !== wsId) return;
      setJobs(list);
    } catch {
      if (activeWorkspaceIdRef.current === wsId && !options?.silent) {
        setLoadError("Uygulama işleri yüklenemedi.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === wsId && !options?.silent) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    activeWorkspaceIdRef.current = workspaceId;
    setJobs([]);
    void loadJobs(workspaceId);
  }, [workspaceId, loadJobs]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    const hasInFlight = jobs.some((j) => !isImplTerminal(j.status));
    if (!hasInFlight) return;
    const interval = setInterval(() => {
      void loadJobs(workspaceId, { silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [jobs, workspaceId, loadJobs]);

  function closeForm() {
    setCreating(false);
    if (prefill) onPrefillConsumed();
  }

  function handleCreated(job: ApiImplementationJob) {
    if (activeWorkspaceIdRef.current !== job.workspace) return;
    setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
    closeForm();
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Ajan, onaylı bir plana göre ayrı bir çalışma ağacında değişiklik yapar; testler ve
          Codex CLI incelemesi geçtikten sonra dal, ancak ikinci onayla gönderilir.
        </p>
        <Button onClick={() => (creating ? closeForm() : setCreating(true))}>
          {creating ? "İptal" : "Uygulama iste"}
        </Button>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {creating ? (
        <ImplementationJobForm
          key={prefill?.discoveryJobId ?? "fresh"}
          workspaceId={workspaceId}
          prefill={prefill}
          onCreated={handleCreated}
          onCancel={closeForm}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : jobs.length === 0 ? (
        loadError ? null : <p className="text-sm text-muted-foreground">Henüz uygulama işi yok.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {jobs.map((job) => (
            <Card
              key={job.id}
              className="cursor-pointer transition-colors hover:bg-accent/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              {...clickableProps(() => onSelect(job.id))}
            >
              <CardHeader>
                <CardTitle className="text-base">{job.goal}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-1 text-sm text-muted-foreground">
                <p>
                  {IMPL_STATUS_LABELS[job.status] ?? job.status}
                  {job.failure_reason
                    ? ` — ${harnessFailureReasonLabel(job.failure_reason)}`
                    : ""}
                  {job.cancel_requested && !isImplTerminal(job.status) ? " — iptal istendi" : ""}
                </p>
                <p>
                  Depo: {job.repo_name}
                  {job.gate_a ? ` · Kapı A: ${GATE_STATUS_LABELS[job.gate_a.status]}` : ""}
                  {job.gate_b ? ` · Kapı B: ${GATE_STATUS_LABELS[job.gate_b.status]}` : ""}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
