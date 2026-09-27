"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { HarnessJobGoalForm } from "@/components/harness/HarnessJobGoalForm";
import { HarnessJobDetail } from "@/components/harness/HarnessJobDetail";
import { ImplementationJobDetail } from "@/components/harness/ImplementationJobDetail";
import { ImplementationJobsPanel } from "@/components/harness/ImplementationJobsPanel";
import type { ImplementationPrefill } from "@/components/harness/ImplementationJobForm";
import {
  HARNESS_JOB_IN_FLIGHT_STATUSES,
  HARNESS_JOB_STATUS_LABELS,
  harnessFailureReasonLabel,
} from "@/components/harness/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { clickableProps } from "@/lib/keyboard";
import type { ApiHarnessJob } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

type HarnessTab = "discovery" | "implementation";

const TABS: { id: HarnessTab; label: string }[] = [
  { id: "discovery", label: "Keşif işleri" },
  { id: "implementation", label: "Uygulama işleri" },
];

export function HarnessJobsView() {
  const { activeWorkspace } = useWorkspace();

  const [jobs, setJobs] = useState<ApiHarnessJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [tab, setTab] = useState<HarnessTab>("discovery");
  const [selectedImplJobId, setSelectedImplJobId] = useState<string | null>(null);
  const [implPrefill, setImplPrefill] = useState<ImplementationPrefill | null>(null);

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
    setSelectedImplJobId(null);
    setImplPrefill(null);
    if (workspaceId) {
      void loadJobs(workspaceId);
    }
  }, [activeWorkspace, loadJobs]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // While viewing the list (not a single job's own detail poll), keep
  // in-flight rows' statuses fresh too -- same reasoning as AgentsView.
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    if (!workspaceId || selectedJobId || tab !== "discovery") return;
    const hasInFlight = jobs.some((j) => HARNESS_JOB_IN_FLIGHT_STATUSES.includes(j.status));
    if (!hasInFlight) return;
    const interval = setInterval(() => {
      void loadJobs(workspaceId, { silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [jobs, selectedJobId, tab, activeWorkspace, loadJobs]);

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
    return (
      <HarnessJobDetail
        jobId={selectedJobId}
        onBack={() => setSelectedJobId(null)}
        onRequestImplementation={(prefill) => {
          setImplPrefill(prefill);
          setSelectedJobId(null);
          setTab("implementation");
        }}
      />
    );
  }

  if (selectedImplJobId) {
    return (
      <ImplementationJobDetail
        jobId={selectedImplJobId}
        onBack={() => setSelectedImplJobId(null)}
      />
    );
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const index = TABS.findIndex((t) => t.id === tab);
    const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    if (!next) return;
    setTab(next.id);
    document.getElementById(`harness-tab-${next.id}`)?.focus();
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Harness</h1>
          <p className="text-sm text-muted-foreground">
            Keşif: kayıtlı bir depoda salt okunur bir inceleme çalıştır, brifing al. Uygulama:
            ajandan değişiklik iste — plan ve gönderim ayrı ayrı senin onayından geçer.
          </p>
        </div>
      </div>

      <div role="tablist" aria-label="Harness işleri" className="flex gap-1 border-b">
        {TABS.map((t) => (
          <button
            key={t.id}
            id={`harness-tab-${t.id}`}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            aria-controls={`harness-panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => setTab(t.id)}
            onKeyDown={handleTabKeyDown}
            className={
              tab === t.id
                ? "-mb-px border-b-2 border-primary px-3 py-2 text-sm font-medium"
                : "-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "implementation" ? (
        <div
          role="tabpanel"
          id="harness-panel-implementation"
          aria-labelledby="harness-tab-implementation"
        >
          <ImplementationJobsPanel
            // Keyed by workspace only: a prefill is only ever set from the
            // discovery detail view, i.e. while this panel is unmounted, so
            // it always mounts fresh with it -- clearing it afterwards must
            // not remount (and so reload over) the just-created job.
            key={activeWorkspace.id}
            workspaceId={activeWorkspace.id}
            prefill={implPrefill}
            onPrefillConsumed={() => setImplPrefill(null)}
            onSelect={setSelectedImplJobId}
          />
        </div>
      ) : (
        <div
          role="tabpanel"
          id="harness-panel-discovery"
          aria-labelledby="harness-tab-discovery"
          className="flex flex-col gap-6"
        >
          <div className="flex justify-end">
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
                  className="cursor-pointer transition-colors hover:bg-accent/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                  {...clickableProps(() => setSelectedJobId(job.id))}
                >
                  <CardHeader>
                    <CardTitle className="text-base">{job.goal}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground">
                      {HARNESS_JOB_STATUS_LABELS[job.status]}
                      {job.failure_reason ? ` — ${harnessFailureReasonLabel(job.failure_reason)}` : ""}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
