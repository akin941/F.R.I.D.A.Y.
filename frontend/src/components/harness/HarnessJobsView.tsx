"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
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
import { tr } from "@/lib/i18n/tr";
import { clickableProps } from "@/lib/keyboard";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, pollWhile } from "@/lib/query/polling";
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

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  // Keyed by workspace: switching workspace resets the selection, tab and
  // open forms; the query keys isolate the rows themselves.
  return <HarnessConsole key={activeWorkspace.id} workspaceId={activeWorkspace.id} />;
}

function HarnessConsole({ workspaceId }: { workspaceId: string }) {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [tab, setTab] = useState<HarnessTab>("discovery");
  const [selectedImplJobId, setSelectedImplJobId] = useState<string | null>(null);
  const [implPrefill, setImplPrefill] = useState<ImplementationPrefill | null>(null);

  if (selectedJobId) {
    return (
      <HarnessJobDetail
        jobId={selectedJobId}
        workspaceId={workspaceId}
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
        workspaceId={workspaceId}
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
      <PageHeader
        title="Harness"
        description="Keşif: kayıtlı bir depoda salt okunur bir inceleme çalıştır, brifing al. Uygulama: ajandan değişiklik iste — plan ve gönderim ayrı ayrı senin onayından geçer."
      />

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
            key={workspaceId}
            workspaceId={workspaceId}
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
        >
          <DiscoveryJobsPanel workspaceId={workspaceId} onSelect={setSelectedJobId} />
        </div>
      )}
    </div>
  );
}

/**
 * "Keşif işleri": the discovery job list + request form. Polls only while
 * mounted (this tab, list visible) and some job is still in flight.
 */
function DiscoveryJobsPanel({
  workspaceId,
  onSelect,
}: {
  workspaceId: string;
  onSelect: (jobId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const jobs = useQuery({
    queryKey: queryKeys.harnessJobs(workspaceId),
    queryFn: () =>
      apiGetJson<ApiHarnessJob[]>(`/api/harness/jobs?workspace=${encodeURIComponent(workspaceId)}`),
    refetchInterval: pollWhile<ApiHarnessJob[]>((list) =>
      list.some((j) => HARNESS_JOB_IN_FLIGHT_STATUSES.includes(j.status)) ? POLL_FAST_MS : null,
    ),
  });

  function handleCreated(job: ApiHarnessJob) {
    queryClient.setQueryData<ApiHarnessJob[]>(queryKeys.harnessJobs(job.workspace), (prev = []) => [
      job,
      ...prev.filter((j) => j.id !== job.id),
    ]);
    setCreating(false);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-end">
        <Button onClick={() => setCreating((v) => !v)}>{creating ? "İptal" : "Yeni iş"}</Button>
      </div>

      {jobs.isError && !jobs.data ? (
        <ErrorState
          error={jobs.error}
          title="Harness işleri yüklenemedi."
          onRetry={() => void jobs.refetch()}
          retrying={jobs.isFetching}
        />
      ) : null}

      {creating ? (
        <HarnessJobGoalForm
          workspaceId={workspaceId}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {jobs.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : jobs.data?.length === 0 ? (
        <p className="text-sm text-muted-foreground">Henüz harness işi yok.</p>
      ) : jobs.data ? (
        <div className="flex flex-col gap-3">
          {jobs.data.map((job) => (
            <Card
              key={job.id}
              className="cursor-pointer transition-colors hover:bg-accent/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              {...clickableProps(() => onSelect(job.id))}
            >
              <CardHeader>
                <CardTitle className="text-base">{job.goal}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <StatusPill
                  domain="harnessJob"
                  status={job.status}
                  label={HARNESS_JOB_STATUS_LABELS[job.status] ?? job.status}
                />
                {job.failure_reason ? (
                  <span>{harnessFailureReasonLabel(job.failure_reason)}</span>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}
    </div>
  );
}
