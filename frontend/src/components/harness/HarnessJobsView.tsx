"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { HarnessJobGoalForm } from "@/components/harness/HarnessJobGoalForm";
import { HarnessJobDetail } from "@/components/harness/HarnessJobDetail";
import { HARNESS_JOB_IN_FLIGHT_STATUSES, HARNESS_JOB_STATUS_LABELS } from "@/components/harness/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import type { ApiHarnessJob } from "@/types/api";

export function HarnessJobsView() {
  const { activeWorkspace } = useWorkspace();

  const [jobs, setJobs] = useState<ApiHarnessJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadJobs = useCallback(async (workspaceId: string, options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiHarnessJob[]>(
        `/api/harness/jobs?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setJobs(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId && !options?.silent) {
        setLoadError("Harness işleri yüklenemedi.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId && !options?.silent) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setJobs([]);
    setCreating(false);
    setSelectedJobId(null);
    if (workspaceId) {
      void loadJobs(workspaceId);
    }
  }, [activeWorkspace, loadJobs]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // While viewing the list (not a single job's own detail poll), keep
  // in-flight rows' statuses fresh too -- same reasoning as AgentsView.
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    if (!workspaceId || selectedJobId) return;
    const hasInFlight = jobs.some((j) => HARNESS_JOB_IN_FLIGHT_STATUSES.includes(j.status));
    if (!hasInFlight) return;
    const interval = setInterval(() => {
      void loadJobs(workspaceId, { silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [jobs, selectedJobId, activeWorkspace, loadJobs]);

  function handleCreated(job: ApiHarnessJob) {
    if (activeWorkspaceIdRef.current !== job.workspace) return;
    setJobs((prev) => [job, ...prev]);
    setCreating(false);
  }

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  if (selectedJobId) {
    return <HarnessJobDetail jobId={selectedJobId} onBack={() => setSelectedJobId(null)} />;
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Harness</h1>
          <p className="text-sm text-muted-foreground">
            Kayıtlı bir depoda salt okunur bir keşif çalıştır, brifing al — hiçbir yazma/dallanma/
            dağıtım işlemi yok.
          </p>
        </div>
        <Button onClick={() => setCreating((v) => !v)}>{creating ? "İptal" : "Yeni iş"}</Button>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {creating ? (
        <HarnessJobGoalForm
          key={activeWorkspace.id}
          workspaceId={activeWorkspace.id}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">Henüz harness işi yok.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {jobs.map((job) => (
            <Card
              key={job.id}
              className="cursor-pointer transition-colors hover:bg-accent/50"
              onClick={() => setSelectedJobId(job.id)}
            >
              <CardHeader>
                <CardTitle className="text-base">{job.goal}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  {HARNESS_JOB_STATUS_LABELS[job.status]}
                  {job.failure_reason ? ` — ${job.failure_reason}` : ""}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
