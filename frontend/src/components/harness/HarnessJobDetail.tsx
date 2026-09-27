"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { LowTrustTranscript } from "@/components/harness/LowTrustTranscript";
import type { ImplementationPrefill } from "@/components/harness/ImplementationJobForm";
import {
  HARNESS_AGENT_LABELS,
  HARNESS_JOB_IN_FLIGHT_STATUSES,
  HARNESS_JOB_STATUS_LABELS,
  harnessFailureReasonLabel,
} from "@/components/harness/labels";
import { apiGetJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, pollWhile } from "@/lib/query/polling";
import type { ApiHarnessJobDetail } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

/**
 * Detail view for one `HarnessJob` (brief §4). Polls while the job is
 * still in flight (pending/running) via the query layer -- stops on its
 * own once the job reaches a terminal status.
 *
 * R2 mitigation (prompt injection, brief §3/§4): `summary`/`findings` are
 * Jarvis's own gateway-produced, trustworthy content; `raw_transcript` is
 * the CLI agent's own low-trust output. This is the presentation-layer
 * half of that structural guarantee -- the two are rendered in visibly
 * distinct blocks, never merged into one undifferentiated wall of text,
 * and the raw transcript is collapsed behind an explicit disclosure
 * rather than shown by default (shared `LowTrustTranscript`).
 *
 * `onRequestImplementation`: a finished discovery run can seed an
 * implementation request (same repo + goal, linked via `discovery_job`).
 */
export function HarnessJobDetail({
  jobId,
  workspaceId,
  onBack,
  onRequestImplementation,
}: {
  jobId: string;
  workspaceId: string;
  onBack: () => void;
  onRequestImplementation?: (prefill: ImplementationPrefill) => void;
}) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const detailKey = queryKeys.harnessJob(workspaceId, jobId);
  const query = useQuery({
    queryKey: detailKey,
    queryFn: () => apiGetJson<ApiHarnessJobDetail>(`/api/harness/jobs/${jobId}`),
    refetchInterval: pollWhile<ApiHarnessJobDetail>((job) =>
      HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status) ? POLL_FAST_MS : null,
    ),
  });
  const job = query.data ?? null;

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await apiPostJson(`/api/harness/jobs/${jobId}/cancel`);
      void queryClient.invalidateQueries({ queryKey: detailKey });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.harnessJobs(workspaceId),
        exact: true,
      });
    } catch (err) {
      setCancelError(errorMessage(err, "İş iptal edilemedi."));
    } finally {
      setCancelling(false);
    }
  }

  const canCancel = job != null && HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status);
  // `briefing` is `{}` for any job that hasn't reached finalization yet
  // (backend model default) -- normalized here rather than assumed fully
  // populated (Codex-style finding, caught in manual verification: an
  // undefined `findings` crashed `.length` for a still-pending job).
  const findings = job?.briefing.findings ?? [];
  const rawTranscript = job?.briefing.raw_transcript ?? "";
  // Codex code-review P2, branch 4 round 1: a job whose briefing gateway
  // call failed/was skipped (harness/execution.py's own `_finalize`)
  // deliberately still persists `raw_transcript` with an empty
  // `summary`/`findings` -- the transcript must render in that case too,
  // not just when a summary was successfully produced, or curators lose
  // the one thing they need to debug why summarization didn't run.
  const hasBriefing =
    job != null && (Boolean(job.briefing.summary) || findings.length > 0 || Boolean(rawTranscript));

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")}>
      <Button variant="outline" size="sm" className="self-start" onClick={onBack}>
        &larr; Harness işlerine dön
      </Button>

      {/* Skeleton only while the first load is in flight -- a failed first
          load shows the error with a retry instead. */}
      {!job ? (
        query.isPending ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <ErrorState
            error={query.error}
            title="Bu iş yüklenemedi."
            onRetry={() => void query.refetch()}
            retrying={query.isFetching}
          />
        )
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold tracking-tight">{job.goal}</h1>
              <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <StatusPill
                  domain="harnessJob"
                  status={job.status}
                  label={HARNESS_JOB_STATUS_LABELS[job.status] ?? job.status}
                />
                {job.failure_reason ? (
                  <span>{harnessFailureReasonLabel(job.failure_reason)}</span>
                ) : null}
              </p>
              <p className="text-sm text-muted-foreground">
                Ajan: {HARNESS_AGENT_LABELS[job.agent]}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {job.status === "succeeded" && onRequestImplementation ? (
                <Button
                  onClick={() =>
                    onRequestImplementation({
                      repo: job.repo,
                      goal: job.goal,
                      discoveryJobId: job.id,
                    })
                  }
                >
                  Uygulama iste
                </Button>
              ) : null}
              {canCancel ? (
                <Button variant="outline" onClick={() => void handleCancel()} disabled={cancelling}>
                  {cancelling ? "İptal ediliyor…" : "İşi iptal et"}
                </Button>
              ) : null}
            </div>
          </div>

          {cancelError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{cancelError}</AlertDescription>
            </Alert>
          ) : null}

          {hasBriefing ? (
            <section className="flex flex-col gap-4">
              {job.briefing.summary ? (
                <div>
                  <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                    Özet
                  </h2>
                  <p className="whitespace-pre-wrap text-sm">{job.briefing.summary}</p>
                </div>
              ) : null}
              {findings.length > 0 ? (
                <div>
                  <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                    Bulgular
                  </h2>
                  <ul className="list-inside list-disc text-sm">
                    {findings.map((finding, index) => (
                      <li key={index}>{finding}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {rawTranscript ? (
                <LowTrustTranscript
                  text={rawTranscript}
                  showLabel="Ham çıktıyı göster"
                  hideLabel="Ham çıktıyı gizle"
                  warning="Bu, ajanın kendi ham çıktısıdır ve güvenilmeyen içerik olarak ele alınmalıdır. Yukarıdaki özet ve bulgular ise Jarvis'in kendi özetidir (model üretimi); ikisini birbirine karıştırma."
                />
              ) : null}
            </section>
          ) : job.status === "succeeded" || job.status === "failed" ? (
            <p className="text-sm text-muted-foreground">Bu iş için brifing yok.</p>
          ) : null}
        </>
      )}
    </div>
  );
}
