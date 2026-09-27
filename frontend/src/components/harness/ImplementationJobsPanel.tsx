"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import {
  ImplementationJobForm,
  type ImplementationPrefill,
} from "@/components/harness/ImplementationJobForm";
import {
  GATE_STATUS_LABELS,
  IMPL_STATUS_LABELS,
  fastestPollInterval,
  harnessFailureReasonLabel,
  implPollInterval,
  isImplTerminal,
} from "@/components/harness/labels";
import { apiGetJson } from "@/lib/api-client";
import { clickableProps } from "@/lib/keyboard";
import { queryKeys } from "@/lib/query/keys";
import { pollWhile } from "@/lib/query/polling";
import type { ApiImplementationJob } from "@/types/api";

/**
 * "Uygulama işleri" list + request form. Rows come from the workspace-keyed
 * query cache (a workspace switch never shows another workspace's rows);
 * the list polls only while some row is non-terminal (fast while a job is
 * working, slow while jobs only await a gate; paused when hidden).
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
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(prefill != null);

  // Polls only while some row is non-terminal: fast while a job is
  // working, slow while jobs only await a gate (never while hidden).
  const jobs = useQuery({
    queryKey: queryKeys.implementationJobs(workspaceId),
    queryFn: () =>
      apiGetJson<ApiImplementationJob[]>(
        `/api/harness/implementation-jobs?workspace=${encodeURIComponent(workspaceId)}`,
      ),
    refetchInterval: pollWhile<ApiImplementationJob[]>((list) =>
      fastestPollInterval(list.map((j) => implPollInterval(j.status))),
    ),
  });

  function closeForm() {
    setCreating(false);
    if (prefill) onPrefillConsumed();
  }

  function handleCreated(job: ApiImplementationJob) {
    queryClient.setQueryData<ApiImplementationJob[]>(
      queryKeys.implementationJobs(job.workspace),
      (prev = []) => [job, ...prev.filter((j) => j.id !== job.id)],
    );
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

      {jobs.isError && !jobs.data ? (
        <ErrorState
          error={jobs.error}
          title="Uygulama işleri yüklenemedi."
          onRetry={() => void jobs.refetch()}
          retrying={jobs.isFetching}
        />
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

      {jobs.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !jobs.data ? null : jobs.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">Henüz uygulama işi yok.</p>
      ) : (
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
              <CardContent className="flex flex-col gap-1 text-sm text-muted-foreground">
                <p className="flex flex-wrap items-center gap-2">
                  <StatusPill
                    domain="implementation"
                    status={job.status}
                    label={IMPL_STATUS_LABELS[job.status] ?? job.status}
                  />
                  {job.failure_reason ? (
                    <span>{harnessFailureReasonLabel(job.failure_reason)}</span>
                  ) : null}
                  {job.cancel_requested && !isImplTerminal(job.status) ? (
                    <span>iptal istendi</span>
                  ) : null}
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
