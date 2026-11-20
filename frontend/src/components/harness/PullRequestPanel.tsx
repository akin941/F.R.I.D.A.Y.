"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { approvalHref } from "@/components/approvals/links";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { GATE_STATUS_LABELS } from "@/components/harness/labels";
import { apiGetJson, apiPostJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, POLL_SLOW_MS, pollWhile } from "@/lib/query/polling";
import type { ApiPullRequestState } from "@/types/api";

const copy = tr.pullRequest;

/** Fast while the PR write runs or CI is pending; slow while the gate waits. */
function prPollInterval(state: ApiPullRequestState): number | null {
  const execution = state.execution?.status;
  if (execution === "pending" || execution === "running") return POLL_FAST_MS;
  if (state.ci?.state === "pending") return POLL_SLOW_MS;
  if (state.request?.status === "pending") return POLL_SLOW_MS;
  return null;
}

function safeGithubUrl(url: string): string | null {
  // The backend keeps only https://github.com/ links; re-checked here.
  return url.startsWith("https://github.com/") ? url : null;
}

/**
 * The implementation job's "PR aç" button and PR/CI panel (brief §9,
 * D7). Requesting a PR only mints the `github_open_pr` gate, from a
 * confirmation dialog with the (editable) title; GitHub is written to only
 * after the separate decision on Onaylar. CI rows are GitHub's own data.
 */
export function PullRequestPanel({ jobId, workspaceId }: { jobId: string; workspaceId: string }) {
  const queryClient = useQueryClient();
  const key = queryKeys.pullRequest(workspaceId, jobId);
  const [title, setTitle] = useState("");
  const query = useQuery({
    queryKey: key,
    queryFn: () =>
      apiGetJson<ApiPullRequestState>(`/api/harness/implementation-jobs/${jobId}/pull-request`),
    refetchInterval: pollWhile<ApiPullRequestState>(prPollInterval),
  });
  const state = query.data;

  async function requestPr() {
    const body = title.trim() ? { title: title.trim() } : {};
    const next = await apiPostJson<ApiPullRequestState>(
      `/api/harness/implementation-jobs/${jobId}/pull-request`,
      body,
    );
    queryClient.setQueryData(key, next);
    void queryClient.invalidateQueries({ queryKey: queryKeys.allApprovals() });
  }

  if (!state) {
    return query.isPending ? (
      <Skeleton className="h-16 w-full" />
    ) : (
      <ErrorState error={query.error} title={copy.loadFailed} onRetry={() => void query.refetch()} />
    );
  }

  const prUrl = state.pull_request ? safeGithubUrl(state.pull_request.url) : null;
  return (
    <section className="flex flex-col gap-2 rounded-md border p-3" data-testid="pull-request-panel">
      <h2 className="text-sm font-medium tracking-tight text-muted-foreground">{copy.title}</h2>
      {state.head ? (
        <p className="text-sm">
          <code className="font-mono text-xs break-all">{copy.branches(state.head, state.base)}</code>
        </p>
      ) : null}

      {state.pull_request ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{copy.opened(state.pull_request.number)}</span>
          {prUrl ? (
            <a href={prUrl} target="_blank" rel="noreferrer noopener" className="underline underline-offset-4">
              {copy.openOnGithub}
            </a>
          ) : null}
        </p>
      ) : null}

      {state.request ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span>{copy.request}:</span>
          <StatusPill domain="gate" status={state.request.status} label={GATE_STATUS_LABELS[state.request.status]} />
          <Link href={approvalHref(state.request.id)} className="text-xs underline underline-offset-4">
            {state.request.status === "pending" ? copy.decide : copy.viewRequest}
          </Link>
        </p>
      ) : null}

      {state.execution ? (
        <div className="flex flex-col gap-0.5 text-sm">
          <StatusPill
            domain="connectorWrite"
            status={state.execution.status}
            label={tr.status.connectorWrite[state.execution.status] ?? state.execution.status}
            className="self-start"
          />
          {state.execution.error_type ? (
            <p className="text-destructive">
              {tr.connectors.writeError[state.execution.error_type] ?? state.execution.error_type}
            </p>
          ) : null}
          {state.execution.status === "unknown_outcome" ? (
            <p className="font-medium text-danger">{tr.connectors.unknownOutcomeNote}</p>
          ) : null}
        </div>
      ) : null}

      {state.can_request ? (
        <ConfirmDialog
          trigger={
            <Button size="sm" className="self-start">
              {copy.open}
            </Button>
          }
          title={copy.dialogTitle}
          description={copy.dialogDescription}
          confirmLabel={copy.confirm}
          busyLabel={copy.busy}
          destructive={false}
          errorFallback={copy.failed}
          onConfirm={requestPr}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor={`pr-title-${jobId}`}>{copy.titleLabel}</Label>
            <Input
              id={`pr-title-${jobId}`}
              value={title}
              maxLength={256}
              onChange={(event) => setTitle(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">{copy.titleHint}</p>
          </div>
        </ConfirmDialog>
      ) : !state.request && state.job_status !== "succeeded" ? (
        <p className="text-sm text-muted-foreground">{copy.notReady}</p>
      ) : null}

      <div className="flex flex-col gap-1 text-sm">
        <h3 className="text-xs font-medium text-muted-foreground">{copy.ci}</h3>
        {state.ci ? (
          <>
            <StatusPill
              domain="ci"
              status={state.ci.state}
              label={tr.status.ci[state.ci.state] ?? state.ci.state}
              className="self-start"
            />
            {state.ci.error ? (
              <p className="text-xs text-muted-foreground">
                {copy.ciUnavailable}: {tr.connectors.readOutcome[state.ci.error] ?? state.ci.error}
              </p>
            ) : null}
            {state.ci.runs.length > 0 ? (
              <ul className="flex flex-col gap-0.5 text-xs">
                {state.ci.runs.map((run, index) => (
                  <li key={`${run.name}-${index}`} className="flex flex-wrap gap-2">
                    <span className="break-all">{run.name}</span>
                    <span className="text-muted-foreground">
                      {run.status}
                      {run.conclusion ? ` · ${run.conclusion}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="text-xs text-muted-foreground">{copy.noCi}</p>
        )}
      </div>
    </section>
  );
}
