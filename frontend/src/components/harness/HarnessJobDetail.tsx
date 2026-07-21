"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  HARNESS_AGENT_LABELS,
  HARNESS_JOB_IN_FLIGHT_STATUSES,
  HARNESS_JOB_STATUS_LABELS,
} from "@/components/harness/labels";
import { apiGetJson, apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiHarnessJobDetail } from "@/types/api";

/**
 * Detail view for one `HarnessJob` (brief §4). Polls silently while the
 * job is still in flight (pending/running), mirroring `AgentDetail`'s
 * identical run-status poll -- stops on its own once the job reaches a
 * terminal status.
 *
 * R2 mitigation (prompt injection, brief §3/§4): `summary`/`findings` are
 * Jarvis's own gateway-produced, trustworthy content; `raw_transcript` is
 * the CLI agent's own low-trust output. This is the presentation-layer
 * half of that structural guarantee -- the two are rendered in visibly
 * distinct blocks, never merged into one undifferentiated wall of text,
 * and the raw transcript is collapsed behind an explicit disclosure
 * rather than shown by default.
 */
export function HarnessJobDetail({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const [job, setJob] = useState<ApiHarnessJobDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [showRawTranscript, setShowRawTranscript] = useState(false);

  const loadJob = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!options?.silent) setLoading(true);
      setLoadError(null);
      try {
        const data = await apiGetJson<ApiHarnessJobDetail>(`/api/harness/jobs/${jobId}`);
        setJob(data);
      } catch {
        if (!options?.silent) setLoadError("Bu iş yüklenemedi.");
      } finally {
        if (!options?.silent) setLoading(false);
      }
    },
    [jobId],
  );

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount, the standard data-fetching effect pattern */
  useEffect(() => {
    void loadJob();
  }, [loadJob]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!job || !HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status)) return;
    const interval = setInterval(() => {
      void loadJob({ silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [job, loadJob]);

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await apiPostJson(`/api/harness/jobs/${jobId}/cancel`);
      await loadJob({ silent: true });
    } catch (err) {
      setCancelError(
        err instanceof ApiError
          ? (err.body.detail ?? "İş iptal edilemedi.")
          : "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
      );
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
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
      <Button variant="outline" size="sm" className="self-start" onClick={onBack}>
        &larr; Harness işlerine dön
      </Button>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {loading || !job ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold tracking-tight">{job.goal}</h1>
              <p className="text-sm text-muted-foreground">
                {HARNESS_JOB_STATUS_LABELS[job.status]}
                {job.failure_reason ? ` — ${job.failure_reason}` : ""}
              </p>
              <p className="text-sm text-muted-foreground">
                Ajan: {HARNESS_AGENT_LABELS[job.agent]}
              </p>
            </div>
            {canCancel ? (
              <Button variant="outline" onClick={() => void handleCancel()} disabled={cancelling}>
                {cancelling ? "İptal ediliyor…" : "İşi iptal et"}
              </Button>
            ) : null}
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
                <div className="flex flex-col gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="self-start"
                    onClick={() => setShowRawTranscript((v) => !v)}
                  >
                    {showRawTranscript ? "Ham çıktıyı gizle" : "Ham çıktıyı göster"}
                  </Button>
                  {showRawTranscript ? (
                    <div className="flex flex-col gap-1">
                      <Alert>
                        <AlertDescription>
                          Bu, ajanın kendi ham çıktısıdır — güvenilmeyen içerik olarak ele alınmalı,
                          yukarıdaki özet ve bulgular gibi doğrulanmış kabul edilmemelidir.
                        </AlertDescription>
                      </Alert>
                      <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border bg-muted p-3 text-xs">
                        {rawTranscript}
                      </pre>
                    </div>
                  ) : null}
                </div>
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
