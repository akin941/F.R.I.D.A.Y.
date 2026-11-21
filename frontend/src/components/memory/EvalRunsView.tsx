"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { LearningSubnav } from "@/components/memory/LearningSubnav";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { apiGetJson } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TONE_PILL_CLASSES, type StatusTone } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type { ApiEvalRun, EvalVerdict } from "@/types/api";

const copy = tr.learning.evals;

const VERDICT_TONES: Record<EvalVerdict, StatusTone> = {
  pass: "success",
  fail: "danger",
  inconclusive: "warning",
};

/** A rate in [0, 1] as a percentage, or "—" when the arm did not measure it. */
export function percent(value: number | null): string {
  return value === null || value === undefined ? copy.notMeasured : `%${(value * 100).toFixed(1)}`;
}

function tokens(run: ApiEvalRun): string {
  if (run.pre_tokens === null && run.post_tokens === null) return copy.notMeasured;
  return `${run.pre_tokens ?? copy.notMeasured} → ${run.post_tokens ?? copy.notMeasured}`;
}

function runLabel(run: ApiEvalRun): string {
  const parts = [copy.set[run.set] ?? run.set, copy.kind[run.kind] ?? run.kind, copy.mode(run.retrieval_mode)];
  if (run.compression_purpose) parts.push(copy.compression(run.compression_purpose));
  if (run.arm) parts.push(copy.arm(run.arm));
  return parts.join(" · ");
}

/**
 * `/memory/evals` (Phase 8B branch 9, D9): the last eval runs' metrics,
 * read-only, from branch 3's owner-only `GET /api/v1/memory/evals/runs`.
 * Renders an explicit list of metric fields only: no case text, question,
 * answer or case id ever reaches this page (the API carries none, and an
 * unexpected field would not be rendered either).
 */
export function EvalRunsView() {
  const runs = useQuery({
    queryKey: queryKeys.evalRuns(),
    queryFn: () => apiGetJson<ApiEvalRun[]>("/api/memory/evals/runs"),
  });
  const h = copy.headers;
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      <LearningSubnav />
      {runs.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : runs.isError ? (
        <ErrorState error={runs.error} title={copy.loadFailed} onRetry={() => void runs.refetch()} />
      ) : runs.data.length === 0 ? (
        <EmptyState icon={FlaskConical} title={copy.empty} />
      ) : (
        <>
          {!runs.data.some((r) => r.set === "private" && r.kind === "baseline") ? (
            <p className="text-sm text-muted-foreground" data-testid="eval-private-pending">
              {copy.pendingPrivate}
            </p>
          ) : null}
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full min-w-[48rem] text-left text-xs" data-testid="eval-runs">
              <thead className="bg-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="px-2 py-1 font-medium">{h.run}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.when}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.cases}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.recall}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.leak}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.temporal}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.accuracy}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.tokens}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.spend}</th>
                  <th scope="col" className="px-2 py-1 font-medium">{h.verdict}</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {runs.data.map((run) => (
                  <tr key={run.id} className="border-t align-top" data-testid={`eval-run-${run.id}`}>
                    <th scope="row" className="px-2 py-1 font-normal">
                      {runLabel(run)}
                      {run.budget_stopped ? (
                        <span className="block text-muted-foreground">{copy.budgetStopped(run.budget_stopped)}</span>
                      ) : null}
                      {run.no_provider ? (
                        <span className="block text-muted-foreground">{copy.noProvider(run.no_provider)}</span>
                      ) : null}
                    </th>
                    <td className="px-2 py-1">{formatDateTime(run.started_at)}</td>
                    <td className="px-2 py-1">
                      {run.answered} / {run.total}
                    </td>
                    <td className="px-2 py-1">{percent(run.recall_at_10)}</td>
                    <td className="px-2 py-1">{percent(run.leak_rate)}</td>
                    <td className="px-2 py-1">{percent(run.temporal_correctness)}</td>
                    <td className="px-2 py-1">{percent(run.answer_accuracy)}</td>
                    <td className="px-2 py-1">{tokens(run)}</td>
                    <td className="px-2 py-1">${Number(run.spend_usd).toFixed(2)}</td>
                    <td className="px-2 py-1">
                      {run.verdict ? (
                        <Badge variant="outline" className={TONE_PILL_CLASSES[VERDICT_TONES[run.verdict] ?? "neutral"]}>
                          {copy.verdict[run.verdict] ?? run.verdict}
                        </Badge>
                      ) : (
                        copy.notMeasured
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
