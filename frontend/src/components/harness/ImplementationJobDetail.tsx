"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { approvalHref } from "@/components/approvals/links";
import { LowTrustTranscript } from "@/components/harness/LowTrustTranscript";
import { PullRequestPanel } from "@/components/harness/PullRequestPanel";
import { TechnicalDetail } from "@/components/harness/TechnicalDetail";
import {
  GATE_STATUS_LABELS,
  HARNESS_AGENT_LABELS,
  IMPL_PIPELINE_STEP_LABELS,
  IMPL_STATUS_LABELS,
  REVIEW_STATUS_LABELS,
  REVIEW_VERDICT_LABELS,
  harnessFailureReasonLabel,
  implPollInterval,
  isImplTerminal,
} from "@/components/harness/labels";
import { pipelineStepStates, type PipelineStepState } from "@/components/harness/pipeline";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { pollWhile } from "@/lib/query/polling";
import { cn } from "@/lib/utils";
import type {
  ApiGateSummary,
  ApiImplementationJob,
  ApiImplementationJobDetail,
  ApiImplementationReview,
} from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";

const copy = tr.harness.console;

const STEP_STATE_CLASSES: Record<PipelineStepState, string> = {
  done: "border-primary bg-primary text-primary-foreground",
  current: "border-primary text-foreground ring-3 ring-ring/50",
  stopped: "border-destructive bg-destructive/15 text-destructive",
  upcoming: "border-border text-muted-foreground",
};

const STEP_STATE_SR: Record<PipelineStepState, string> = {
  done: "tamamlandı",
  current: "şu anda",
  stopped: "burada durdu",
  upcoming: "sırada",
};

export function shortSha(sha: string): string {
  return sha.slice(0, 12);
}

function GateChip({ label, gate }: { label: string; gate: ApiGateSummary }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm">
      <span className="font-medium">{label}:</span>
      {gate ? (
        <>
          <StatusPill domain="gate" status={gate.status} label={GATE_STATUS_LABELS[gate.status]} />
          <Link href={approvalHref(gate.id)} className="text-xs underline underline-offset-4">
            {gate.status === "pending" ? "Onaylar sayfasında karar ver" : "Onay isteğini gör"}
          </Link>
        </>
      ) : (
        <span className="text-muted-foreground">Henüz oluşturulmadı</span>
      )}
    </div>
  );
}

function ReviewBlock({ review }: { review: ApiImplementationReview }) {
  return (
    <li className="flex flex-col gap-2 rounded-md border p-3">
      <p className="text-sm">
        <span className="font-medium">{HARNESS_AGENT_LABELS[review.agent] ?? review.agent}</span>
        {" · "}
        {REVIEW_STATUS_LABELS[review.status] ?? review.status}
        {review.verdict ? (
          <>
            {" · Karar: "}
            <span
              className={cn(
                "font-medium",
                review.verdict === "changes_requested" && "text-destructive",
              )}
            >
              {REVIEW_VERDICT_LABELS[review.verdict] ?? review.verdict}
            </span>
          </>
        ) : null}
      </p>
      {review.summary || review.findings.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          Özet ve bulgular: Friday&apos;nin kendi özeti (model üretimi).
        </p>
      ) : null}
      {review.summary ? <p className="whitespace-pre-wrap text-sm">{review.summary}</p> : null}
      {review.findings.length > 0 ? (
        <ul className="list-inside list-disc text-sm">
          {review.findings.map((finding, index) => (
            <li key={index}>{finding}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Route `/harness/implementations/[jobId]`: the detail under the active workspace. */
export function ImplementationJobDetailPage({ jobId }: { jobId: string }) {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  return (
    <ImplementationJobDetail key={activeWorkspace.id} jobId={jobId} workspaceId={activeWorkspace.id} />
  );
}

/**
 * Detail view for one implementation job (route
 * `/harness/implementations/[jobId]`). Polls silently while the job is
 * non-terminal -- every 3s while a container is working, every 30s while
 * it only waits at a gate, paused while the tab is hidden -- and stops on
 * its own at succeeded/failed/cancelled (query layer; the job id is part
 * of the query key, so a response for another job never lands here).
 *
 * Header (status, pipeline stepper, cancel) stays put; the rest is split
 * into Plan / İnceleme / Diff / Transkriptler tabs.
 *
 * Trust layering (R2): `diff_summary`, review `summary`/`findings` are
 * Friday's own gateway output; both CLIs' `raw_transcript`s are low-trust,
 * live only on the Transkriptler tab and are only ever rendered via
 * `LowTrustTranscript`. `changed_files` and the commit/branch are
 * host-derived facts, shown verbatim.
 */
export function ImplementationJobDetail({
  jobId,
  workspaceId,
}: {
  jobId: string;
  workspaceId: string;
}) {
  const queryClient = useQueryClient();

  const detailKey = queryKeys.implementationJob(workspaceId, jobId);
  const query = useQuery({
    queryKey: detailKey,
    queryFn: () =>
      apiGetJson<ApiImplementationJobDetail>(`/api/harness/implementation-jobs/${jobId}`),
    refetchInterval: pollWhile<ApiImplementationJobDetail>((job) => implPollInterval(job.status)),
  });
  const job = query.data ?? null;

  // Runs only from the confirmation dialog, which reports a failure.
  async function handleCancel() {
    await apiPostJson<ApiImplementationJob>(`/api/harness/implementation-jobs/${jobId}/cancel`);
    await queryClient.invalidateQueries({ queryKey: detailKey });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.implementationJobs(workspaceId),
      exact: true,
    });
  }

  const canCancel = job != null && !isImplTerminal(job.status) && !job.cancel_requested;
  const testResult = job && "ran" in job.test_result ? job.test_result : null;
  const pushResult = job && "pushed" in job.push_result ? job.push_result : null;
  const reviewTranscripts = job ? job.reviews.filter((review) => review.raw_transcript) : [];

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")}>
      <Button variant="outline" size="sm" className="self-start" asChild>
        <Link href="/harness?tab=implementation">&larr; {copy.backToImplementations}</Link>
      </Button>

      {!job ? (
        query.isPending ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <ErrorState
            error={query.error}
            title="Bu uygulama işi yüklenemedi."
            onRetry={() => void query.refetch()}
            retrying={query.isFetching}
          />
        )
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-1">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {copy.implementationJob}
              </p>
              <h1 className="text-xl font-semibold tracking-tight whitespace-pre-wrap break-words">
                {job.goal}
              </h1>
              <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <StatusPill
                  domain="implementation"
                  status={job.status}
                  label={IMPL_STATUS_LABELS[job.status] ?? job.status}
                />
                {job.failure_reason ? (
                  <span>{harnessFailureReasonLabel(job.failure_reason)}</span>
                ) : null}
              </p>
              <p className="text-sm text-muted-foreground">
                Depo: {job.repo_name} · Uygulayıcı: {HARNESS_AGENT_LABELS[job.agent]} · İnceleyici:{" "}
                {(job.reviewer_agent && HARNESS_AGENT_LABELS[job.reviewer_agent]) || "—"}
              </p>
              {job.skills && job.skills.length > 0 ? (
                <p className="text-sm text-muted-foreground" data-testid="impl-job-skills">
                  Beceriler (salt okunur):{" "}
                  {job.skills
                    .map((skill) => `${skill.name} · sha256 ${skill.sha256.slice(0, 12)}…`)
                    .join(", ")}
                </p>
              ) : null}
              {job.cancel_requested && !isImplTerminal(job.status) ? (
                <p className="text-sm text-muted-foreground">
                  İptal istendi — çalışan adım durduruluyor…
                </p>
              ) : null}
            </div>
            {canCancel ? (
              <ConfirmDialog
                trigger={<Button variant="outline">İşi iptal et</Button>}
                title={tr.confirm.implementationJob.title}
                description={tr.confirm.implementationJob.description}
                confirmLabel={tr.confirm.implementationJob.confirm}
                busyLabel={tr.confirm.implementationJob.busy}
                errorFallback={tr.confirm.implementationJob.failed}
                onConfirm={handleCancel}
              />
            ) : null}
          </div>

          <ol aria-label="İş hattı" className="flex flex-wrap items-center gap-2">
            {pipelineStepStates(job, job.reviews.length).map(({ step, state }, index) => (
              <li
                key={step}
                aria-current={state === "current" ? "step" : undefined}
                data-state={state}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-medium",
                  STEP_STATE_CLASSES[state],
                )}
              >
                {index + 1}. {IMPL_PIPELINE_STEP_LABELS[step]}
                <span className="sr-only"> ({STEP_STATE_SR[state]})</span>
              </li>
            ))}
          </ol>

          <Tabs defaultValue="plan">
            <TabsList aria-label={copy.detailTabs}>
              <TabsTrigger value="plan">{copy.tabs.plan}</TabsTrigger>
              <TabsTrigger value="review">
                {copy.tabs.review}
                {job.reviews.length > 0 ? ` (${job.reviews.length})` : ""}
              </TabsTrigger>
              <TabsTrigger value="diff">{copy.tabs.diff}</TabsTrigger>
              <TabsTrigger value="transcripts">{copy.tabs.transcripts}</TabsTrigger>
            </TabsList>

            <TabsContent value="plan" className="flex flex-col gap-4 pt-2">
              <section className="flex flex-col gap-2">
                <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                  Sahip onayları
                </h2>
                <div className="flex flex-col gap-2 md:flex-row">
                  <GateChip label="Kapı A — plan onayı" gate={job.gate_a} />
                  <GateChip label="Kapı B — gönderim onayı" gate={job.gate_b} />
                </div>
              </section>

              {/* Phase 5: a pushed job can get a PR (its own gate, github_open_pr). */}
              {job.status === "succeeded" ? (
                <PullRequestPanel jobId={job.id} workspaceId={workspaceId} />
              ) : null}

              <section className="flex flex-col gap-1 text-sm">
                <h2 className="text-sm font-medium tracking-tight text-muted-foreground">Dal</h2>
                <p>
                  Dal: <code className="font-mono">{job.branch_name || "—"}</code>
                </p>
                {job.base_commit_sha ? (
                  <p>
                    Temel commit:{" "}
                    <code className="font-mono" title={job.base_commit_sha}>
                      {shortSha(job.base_commit_sha)}
                    </code>
                  </p>
                ) : null}
                {job.worktree_expires_at && job.status === "pending_gate_b" ? (
                  <p className="text-muted-foreground">
                    Çalışma ağacı şu zamana kadar saklanır:{" "}
                    {new Date(job.worktree_expires_at).toLocaleString()} — bu süre dolmadan Kapı B
                    kararı verilmezse iş başarısız olur.
                  </p>
                ) : null}
              </section>
            </TabsContent>

            <TabsContent value="review" className="flex flex-col gap-2 pt-2">
              {job.reviews.length > 0 ? (
                <>
                  <ul className="flex flex-col gap-2">
                    {job.reviews.map((review) => (
                      <ReviewBlock key={review.id} review={review} />
                    ))}
                  </ul>
                  {reviewTranscripts.length > 0 ? (
                    <p className="text-xs text-muted-foreground">{copy.rawOnTranscriptsTab}</p>
                  ) : null}
                </>
              ) : (
                <p className="text-sm text-muted-foreground">{copy.noReviews}</p>
              )}
            </TabsContent>

            <TabsContent value="diff" className="flex flex-col gap-4 pt-2">
              <section className="flex flex-col gap-1 text-sm">
                <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                  Test sonucu
                </h2>
                {testResult ? (
                  testResult.ran ? (
                    <p className={cn(!testResult.passed && "text-destructive")}>
                      {testResult.passed ? "Testler geçti" : "Testler başarısız"}
                      {testResult.exit_code != null ? ` (çıkış kodu ${testResult.exit_code})` : ""}
                      {" — "}
                      <code className="font-mono">{testResult.command}</code>
                    </p>
                  ) : (
                    <p className="text-destructive">Testler çalıştırılamadı.</p>
                  )
                ) : (
                  <p className="text-muted-foreground">Henüz test sonucu yok.</p>
                )}
              </section>

              <section className="flex flex-col gap-1 text-sm">
                <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                  Commit ve gönderim
                </h2>
                {job.produced_commit_sha ? (
                  <p>
                    Üretilen commit:{" "}
                    <code className="font-mono" title={job.produced_commit_sha}>
                      {shortSha(job.produced_commit_sha)}
                    </code>
                  </p>
                ) : (
                  <p className="text-muted-foreground">{copy.noCommitYet}</p>
                )}
                {pushResult ? (
                  pushResult.pushed ? (
                    <p>Dal uzak depoya gönderildi.</p>
                  ) : (
                    <div className="flex flex-col gap-1">
                      <p className="text-destructive">Gönderim başarısız.</p>
                      {pushResult.error ? <TechnicalDetail text={pushResult.error} /> : null}
                    </div>
                  )
                ) : null}
              </section>

              {job.changed_files.length > 0 ? (
                <section className="flex flex-col gap-1">
                  <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                    Değişen dosyalar ({job.changed_files.length})
                  </h2>
                  <ul className="max-h-64 overflow-auto rounded-md border p-2 font-mono text-xs">
                    {job.changed_files.map((file) => (
                      <li key={file} className="break-all">
                        {file}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {job.diff_summary ? (
                <section className="flex flex-col gap-1">
                  <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                    Değişiklik özeti
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    Model tarafından üretilmiş bir özettir, diff’in kendisi değildir — kesin kapsam
                    için değişen dosya listesine bakın.
                  </p>
                  <p className="whitespace-pre-wrap text-sm">{job.diff_summary}</p>
                </section>
              ) : null}
            </TabsContent>

            <TabsContent value="transcripts" className="flex flex-col gap-4 pt-2">
              {job.raw_transcript || reviewTranscripts.length > 0 ? (
                <>
                  {job.raw_transcript ? (
                    <section className="flex flex-col gap-2">
                      <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                        Uygulayıcı çıktısı
                      </h2>
                      <LowTrustTranscript
                        text={job.raw_transcript}
                        showLabel="Uygulayıcının ham çıktısını göster"
                        hideLabel="Uygulayıcının ham çıktısını gizle"
                        warning="Bu, uygulayıcı ajanın (Claude Code) kendi ham çıktısıdır ve güvenilmeyen içerik olarak ele alınmalıdır. Test sonucu ve değişen dosyalar Friday'nin çalıştırıcısından gelir; değişiklik ve inceleme özetleri Friday'nin kendi özetidir (model üretimi)."
                      />
                    </section>
                  ) : null}
                  {reviewTranscripts.map((review) => (
                    <section key={review.id} className="flex flex-col gap-2">
                      <h2 className="text-sm font-medium tracking-tight text-muted-foreground">
                        İnceleyici çıktısı ({HARNESS_AGENT_LABELS[review.agent] ?? review.agent})
                      </h2>
                      <LowTrustTranscript
                        text={review.raw_transcript}
                        showLabel="İnceleyicinin ham çıktısını göster"
                        hideLabel="İnceleyicinin ham çıktısını gizle"
                        warning="Bu, inceleyici ajanın (Codex CLI) kendi ham çıktısıdır ve güvenilmeyen içerik olarak ele alınmalıdır. İnceleme sekmesindeki karar, özet ve bulgular ise Friday'nin kendi özetidir (model üretimi); ikisini birbirine karıştırma."
                      />
                    </section>
                  ))}
                </>
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
