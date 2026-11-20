"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LowTrustTranscript } from "@/components/harness/LowTrustTranscript";
import {
  HARNESS_AGENT_LABELS,
  HARNESS_JOB_IN_FLIGHT_STATUSES,
  HARNESS_JOB_STATUS_LABELS,
  harnessFailureReasonLabel,
} from "@/components/harness/labels";
import { implementationRequestHref } from "@/components/harness/links";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, pollWhile } from "@/lib/query/polling";
import type { ApiHarnessJobDetail } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const copy = tr.harness.console;

/** Route `/harness/jobs/[jobId]`: the detail under the active workspace. */
export function HarnessJobDetailPage({ jobId }: { jobId: string }) {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  return <HarnessJobDetail key={activeWorkspace.id} jobId={jobId} workspaceId={activeWorkspace.id} />;
}

/**
 * Detail view for one `HarnessJob` (brief §4). Polls while the job is
 * still in flight (pending/running) via the query layer -- stops on its
 * own once the job reaches a terminal status.
 *
 * R2 mitigation (prompt injection, brief §3/§4): `summary`/`findings` are
 * Friday's own gateway-produced, trustworthy content; `raw_transcript` is
 * the CLI agent's own low-trust output. The two live on separate tabs
 * (Brifing / Transkript) and the transcript stays collapsed behind an
 * explicit, labeled disclosure (shared `LowTrustTranscript`) -- never
 * merged into one undifferentiated wall of text.
 *
 * A finished discovery run links to the console's implementation form,
 * prefilled with the same repo + goal and linked via `discovery_job`.
 */
export function HarnessJobDetail({ jobId, workspaceId }: { jobId: string; workspaceId: string }) {
  const queryClient = useQueryClient();

  const detailKey = queryKeys.harnessJob(workspaceId, jobId);
  const query = useQuery({
    queryKey: detailKey,
    queryFn: () => apiGetJson<ApiHarnessJobDetail>(`/api/harness/jobs/${jobId}`),
    refetchInterval: pollWhile<ApiHarnessJobDetail>((job) =>
      HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status) ? POLL_FAST_MS : null,
    ),
  });
  const job = query.data ?? null;

  // Runs only from the confirmation dialog, which reports a failure.
  async function handleCancel() {
    await apiPostJson(`/api/harness/jobs/${jobId}/cancel`);
    void queryClient.invalidateQueries({ queryKey: detailKey });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.harnessJobs(workspaceId),
      exact: true,
    });
  }

  const canCancel = job != null && HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status);
  // `briefing` is `{}` for any job that hasn't reached finalization yet
  // (backend model default) -- normalized here rather than assumed fully
  // populated (an undefined `findings` once crashed `.length` for a
  // still-pending job).
  const findings = job?.briefing.findings ?? [];
  const summary = job?.briefing.summary ?? "";
  // A job whose briefing gateway call failed/was skipped still persists
  // `raw_transcript` with an empty summary/findings -- the transcript
  // must stay reachable then too.
  const rawTranscript = job?.briefing.raw_transcript ?? "";
  const terminal = job != null && !HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status);

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")}>
      <Button variant="outline" size="sm" className="self-start" asChild>
        <Link href="/harness">&larr; {copy.backToConsole}</Link>
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
            <div className="flex min-w-0 flex-col gap-1">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {copy.discoveryJob}
              </p>
              <h1 className="text-xl font-semibold tracking-tight break-words whitespace-pre-wrap">
                {job.goal}
              </h1>
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
              {job.status === "succeeded" ? (
                <Button asChild>
                  <Link href={implementationRequestHref(job)}>Uygulama iste</Link>
                </Button>
              ) : null}
              {canCancel ? (
                <ConfirmDialog
                  trigger={<Button variant="outline">İşi iptal et</Button>}
                  title={tr.confirm.harnessJob.title}
                  description={tr.confirm.harnessJob.description(job.goal)}
                  confirmLabel={tr.confirm.harnessJob.confirm}
                  busyLabel={tr.confirm.harnessJob.busy}
                  errorFallback={tr.confirm.harnessJob.failed}
                  onConfirm={handleCancel}
                />
              ) : null}
            </div>
          </div>

          <Tabs defaultValue="briefing">
            <TabsList aria-label={copy.detailTabs}>
              <TabsTrigger value="briefing">{copy.tabs.briefing}</TabsTrigger>
              <TabsTrigger value="transcripts">{copy.tabs.transcripts}</TabsTrigger>
            </TabsList>
            <TabsContent value="briefing" className="flex flex-col gap-4 pt-2">
              {summary || findings.length > 0 ? (
                <>
                  <p className="text-xs text-muted-foreground">{copy.ownSummaryNote}</p>
                  {summary ? (
                    <div>
                      <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                        Özet
                      </h2>
                      <p className="text-sm whitespace-pre-wrap">{summary}</p>
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
                </>
              ) : rawTranscript ? (
                <p className="text-sm text-muted-foreground">{copy.noSummaryButTranscript}</p>
              ) : terminal ? (
                <p className="text-sm text-muted-foreground">Bu iş için brifing yok.</p>
              ) : (
                <p className="text-sm text-muted-foreground">{copy.briefingPending}</p>
              )}
            </TabsContent>
            <TabsContent value="transcripts" className="flex flex-col gap-2 pt-2">
              {rawTranscript ? (
                <LowTrustTranscript
                  text={rawTranscript}
                  showLabel="Ham çıktıyı göster"
                  hideLabel="Ham çıktıyı gizle"
                  warning="Bu, ajanın kendi ham çıktısıdır ve güvenilmeyen içerik olarak ele alınmalıdır. Brifing sekmesindeki özet ve bulgular ise Friday'nin kendi özetidir (model üretimi); ikisini birbirine karıştırma."
                />
              ) : (
                <p className="text-sm text-muted-foreground">{copy.noTranscript}</p>
              )}
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
