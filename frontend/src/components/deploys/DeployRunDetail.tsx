"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { approvalHref } from "@/components/approvals/links";
import { DeployRunBanner } from "@/components/deploys/DeployRunBanner";
import {
  DEPLOYS_HREF,
  DEPLOY_RUN_STATUS_LABELS,
  deployErrorLabel,
  deployPollInterval,
  shortSha,
} from "@/components/deploys/labels";
import { LowTrustTranscript } from "@/components/harness/LowTrustTranscript";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { pollWhile } from "@/lib/query/polling";
import { cn } from "@/lib/utils";
import type { ApiDeployRunDetail } from "@/types/api";

const copy = tr.deploys;

// Cancellable only before the deploy verb (backend cancel_deploy_run).
const CANCELLABLE = new Set(["pending", "checking"]);

// A check row (deploys/execution.py): {phase, at, verb_ok, url_ok}; the
// `health_url` probe (url_ok) only counts when one was configured.
function healthCheckPassed(check: unknown): boolean | null {
  if (!check || typeof check !== "object") return null;
  const { verb_ok: verbOk, url_ok: urlOk } = check as { verb_ok?: unknown; url_ok?: unknown };
  if (typeof verbOk !== "boolean") return null;
  return verbOk && urlOk !== false;
}

function healthCheckPhase(check: unknown): string {
  const phase = (check as { phase?: unknown } | null)?.phase;
  return phase === "rollback" ? " (geri alma)" : "";
}

function When({ value }: { value: string | null }) {
  return value ? <span title={value}>{formatDateTime(value)}</span> : <span>—</span>;
}

/** Route `/deploys/runs/[runId]` under the active workspace. */
export function DeployRunDetailPage({ runId }: { runId: string }) {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return <p className="p-6 text-sm text-muted-foreground">{tr.common.noWorkspace}</p>;
  }
  return <DeployRunDetail key={activeWorkspace.id} runId={runId} workspaceId={activeWorkspace.id} />;
}

/**
 * One deploy run: its state banner (distinct per attention state), the
 * SHAs, timestamps, the health checks, the closed-vocabulary reason, and
 * the remote step log -- low-trust output fenced behind a disclosure and
 * shown only as preformatted text (`LowTrustTranscript`).
 */
export function DeployRunDetail({ runId, workspaceId }: { runId: string; workspaceId: string }) {
  const queryClient = useQueryClient();
  const key = queryKeys.deployRun(workspaceId, runId);
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiGetJson<ApiDeployRunDetail>(`/api/deploys/runs/${runId}`),
    refetchInterval: pollWhile<ApiDeployRunDetail>((run) => deployPollInterval(run.status)),
  });
  const run = query.data;

  async function cancel() {
    const updated = await apiPostJson<ApiDeployRunDetail>(`/api/deploys/runs/${runId}/cancel`);
    queryClient.setQueryData(key, updated);
    void queryClient.invalidateQueries({ queryKey: queryKeys.deployRuns(workspaceId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.deployTargets(workspaceId) });
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-4 md:p-6")}>
      <Button variant="outline" size="sm" className="self-start" asChild>
        <Link href={DEPLOYS_HREF}>&larr; {copy.backToTargets}</Link>
      </Button>
      {!run ? (
        query.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <ErrorState error={query.error} title={copy.runFailed} onRetry={() => void query.refetch()} />
        )
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <h1 className="text-xl font-semibold tracking-tight break-words">
                {copy.runTitle(`${run.target_name} (${run.environment})`, shortSha(run.sha))}
              </h1>
              <StatusPill
                domain="deployRun"
                status={run.status}
                label={DEPLOY_RUN_STATUS_LABELS[run.status] ?? run.status}
                className="self-start"
              />
            </div>
            {CANCELLABLE.has(run.status) ? (
              <ConfirmDialog
                trigger={<Button variant="outline">{copy.cancel}</Button>}
                title={copy.cancelTitle}
                description={copy.cancelDescription}
                confirmLabel={copy.cancelConfirm}
                busyLabel={copy.cancelBusy}
                errorFallback={copy.cancelFailed}
                onConfirm={cancel}
              />
            ) : null}
          </div>

          <DeployRunBanner status={run.status} rollbackSha={run.rollback_sha} errorCode={run.error_code} />

          <dl className="grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
            <dt className="text-muted-foreground">{copy.sha}</dt>
            <dd className="min-w-0">
              <code className="font-mono text-xs break-all">{run.sha}</code>
            </dd>
            <dt className="text-muted-foreground">{copy.previousSha}</dt>
            <dd className="min-w-0">
              <code className="font-mono text-xs break-all">{run.expected_current_sha || "—"}</code>
            </dd>
            <dt className="text-muted-foreground">{copy.rollbackSha}</dt>
            <dd className="min-w-0">
              {run.auto_rollback ? (
                <code className="font-mono text-xs break-all">{run.rollback_sha || "—"}</code>
              ) : (
                <span>{copy.no}</span>
              )}
            </dd>
            <dt className="text-muted-foreground">{copy.startedAt}</dt>
            <dd>
              <When value={run.deploy_started_at} />
            </dd>
            <dt className="text-muted-foreground">{copy.finishedAt}</dt>
            <dd>
              <When value={run.finished_at} />
            </dd>
            {run.error_code ? (
              <>
                <dt className="text-muted-foreground">{copy.errorCode}</dt>
                <dd>
                  {deployErrorLabel(run.error_code)}{" "}
                  <code className="font-mono text-xs text-muted-foreground">{run.error_code}</code>
                </dd>
              </>
            ) : null}
            <dt className="text-muted-foreground">{copy.approval}</dt>
            <dd>
              <Link href={approvalHref(run.approval_request)} className="underline underline-offset-4">
                {copy.dialog.openApproval}
              </Link>
            </dd>
          </dl>

          <section className="flex flex-col gap-1">
            <h2 className="text-sm font-medium text-muted-foreground">
              {copy.healthChecks} ({run.health_checks.length})
            </h2>
            {run.health_plan.checks != null &&
            run.health_plan.window_seconds != null &&
            run.health_plan.fail_threshold != null ? (
              <p className="text-xs text-muted-foreground">
                {copy.healthPlan(
                  run.health_plan.checks,
                  run.health_plan.window_seconds,
                  run.health_plan.fail_threshold,
                )}
              </p>
            ) : null}
            {run.health_checks.length > 0 ? (
              <ol className="flex flex-wrap gap-1" aria-label={copy.healthChecks}>
                {run.health_checks.map((check, index) => {
                  const passed = healthCheckPassed(check);
                  return (
                    <li
                      key={index}
                      className={cn(
                        "rounded border px-1.5 py-0.5 text-xs",
                        passed === true && "border-success/30 bg-success-muted text-success",
                        passed === false && "border-danger/30 bg-danger-muted text-danger",
                      )}
                    >
                      {index + 1}:{" "}
                      {passed == null ? "?" : passed ? copy.healthCheckPass : copy.healthCheckFail}
                      {healthCheckPhase(check)}
                    </li>
                  );
                })}
              </ol>
            ) : null}
          </section>

          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-medium text-muted-foreground">{copy.output}</h2>
            {run.output ? (
              <LowTrustTranscript
                text={run.output}
                showLabel={copy.showOutput}
                hideLabel={copy.hideOutput}
                warning={copy.outputWarning}
              />
            ) : (
              <p className="text-sm text-muted-foreground">{copy.noOutput}</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
