"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Rocket } from "lucide-react";
import { toast } from "sonner";
import { DeployDialog } from "@/components/deploys/DeployDialog";
import { DeployRunBanner } from "@/components/deploys/DeployRunBanner";
import { DeployTargetForm } from "@/components/deploys/DeployTargetForm";
import {
  DEPLOY_RUN_STATUS_LABELS,
  deployErrorLabel,
  deployPollInterval,
  deployRunHref,
  shortSha,
} from "@/components/deploys/labels";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPatchJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, fastestPollInterval, pollWhile } from "@/lib/query/polling";
import { cn } from "@/lib/utils";
import type { ApiDeployRun, ApiDeployTarget } from "@/types/api";

const copy = tr.deploys;

export function useDeployTargets(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.deployTargets(workspaceId),
    queryFn: () =>
      apiGetJson<ApiDeployTarget[]>(
        `/api/deploys/targets?workspace=${encodeURIComponent(workspaceId)}`,
      ),
    // A busy target clears when its run ends.
    refetchInterval: pollWhile<ApiDeployTarget[]>((list) =>
      list.some((t) => t.active_run) ? POLL_FAST_MS : null,
    ),
  });
}

export function useDeployRuns(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.deployRuns(workspaceId),
    queryFn: () =>
      apiGetJson<ApiDeployRun[]>(`/api/deploys/runs?workspace=${encodeURIComponent(workspaceId)}`),
    refetchInterval: pollWhile<ApiDeployRun[]>((list) =>
      fastestPollInterval(list.map((run) => deployPollInterval(run.status))),
    ),
  });
}

function RunRow({ run }: { run: ApiDeployRun }) {
  return (
    <li className="flex flex-col gap-1.5 px-3 py-2" data-testid={`run-${run.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <code className="font-mono text-xs" title={run.sha}>
          {shortSha(run.sha)}
        </code>
        <StatusPill
          domain="deployRun"
          status={run.status}
          label={DEPLOY_RUN_STATUS_LABELS[run.status] ?? run.status}
        />
        <span className="text-xs text-muted-foreground" title={formatDateTime(run.created_at)}>
          {formatRelativeTime(run.created_at)}
        </span>
        <Link
          href={deployRunHref(run)}
          className="ml-auto text-xs underline underline-offset-4"
        >
          {copy.openRun}
        </Link>
      </div>
      {run.needs_attention ? (
        <DeployRunBanner status={run.status} rollbackSha={run.rollback_sha} errorCode={run.error_code} />
      ) : run.error_code ? (
        <p className="text-xs text-muted-foreground">{deployErrorLabel(run.error_code)}</p>
      ) : null}
    </li>
  );
}

function TargetCard({ target, runs }: { target: ApiDeployTarget; runs: ApiDeployRun[] }) {
  const queryClient = useQueryClient();
  const targetsKey = queryKeys.deployTargets(target.workspace);

  async function toggle() {
    await apiPatchJson<ApiDeployTarget>(`/api/deploys/targets/${target.id}`, {
      enabled: !target.enabled,
    });
    await queryClient.invalidateQueries({ queryKey: targetsKey });
  }

  async function refreshStatus() {
    try {
      await apiPostJson(`/api/deploys/targets/${target.id}/status`);
      toast.success(copy.refreshQueued);
      void queryClient.invalidateQueries({ queryKey: targetsKey });
    } catch (err) {
      toast.error(errorMessage(err, copy.refreshFailed));
    }
  }

  const busy = target.active_run != null;
  return (
    <Card data-testid={`target-${target.id}`}>
      <CardHeader className="gap-1">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <h2 className="font-medium">{target.name}</h2>
          <Badge variant="outline">{target.environment}</Badge>
          <StatusPill
            domain="harnessJob"
            status={target.enabled ? "succeeded" : "cancelled"}
            label={target.enabled ? copy.enabled : copy.disabled}
          />
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <dl className="grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">{copy.liveSha}</dt>
          <dd className="min-w-0">
            {target.current_sha ? (
              <code className="font-mono text-xs break-all">{target.current_sha}</code>
            ) : (
              <span className="text-warning">{copy.liveUnknown}</span>
            )}
            {target.last_status_at ? (
              <span className="block text-xs text-muted-foreground">
                {copy.lastStatus(formatRelativeTime(target.last_status_at))}
              </span>
            ) : null}
            {target.last_status_error ? (
              <span className="block text-xs text-danger">
                {copy.lastStatusError(deployErrorLabel(target.last_status_error))}
              </span>
            ) : null}
          </dd>
          <dt className="text-muted-foreground">{copy.branch}</dt>
          <dd>
            <code className="font-mono text-xs">{target.tracked_branch}</code>
          </dd>
          <dt className="text-muted-foreground">{copy.destination}</dt>
          <dd className="min-w-0">
            <code className="font-mono text-xs break-all">{target.ssh_destination}</code> ·{" "}
            <code className="font-mono text-xs">{target.remote_app}</code>
          </dd>
          <dt className="text-muted-foreground">{copy.repo}</dt>
          <dd className="min-w-0">
            <code className="font-mono text-xs break-all">{target.repo_git_url}</code>
          </dd>
          <dt className="text-muted-foreground">{copy.health}</dt>
          <dd className="text-xs">
            {copy.healthWindow(target.health_timeout_seconds)}
            {target.health_url ? (
              <code className="block font-mono break-all">{target.health_url}</code>
            ) : null}
          </dd>
          <dt className="text-muted-foreground">{copy.requireCi}</dt>
          <dd>{target.require_ci_success ? copy.yes : copy.no}</dd>
          <dt className="text-muted-foreground">{copy.autoRollback}</dt>
          <dd>{target.auto_rollback ? copy.yes : copy.no}</dd>
        </dl>

        {busy ? <p className="text-sm text-info">{copy.busyTarget}</p> : null}

        <div className="flex flex-wrap gap-2">
          <DeployDialog target={target} />
          <Button size="sm" variant="outline" onClick={() => void refreshStatus()} disabled={busy}>
            {copy.refreshStatus}
          </Button>
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="outline">
                {target.enabled ? copy.disable : copy.enable}
              </Button>
            }
            title={target.enabled ? copy.disableTitle(target.name) : copy.enableTitle(target.name)}
            description={target.enabled ? copy.disableDescription : copy.enableDescription}
            confirmLabel={target.enabled ? copy.disable : copy.enable}
            destructive={target.enabled}
            errorFallback={copy.toggleFailed}
            onConfirm={toggle}
          />
        </div>

        <section className="flex flex-col gap-1">
          <h3 className="text-sm font-medium text-muted-foreground">{copy.runs}</h3>
          {runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">{copy.runsEmpty}</p>
          ) : (
            <ul className="flex flex-col divide-y rounded-md border">
              {runs.slice(0, 5).map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function TargetsForWorkspace({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const targets = useDeployTargets(workspaceId);
  const runs = useDeployRuns(workspaceId);

  function handleCreated() {
    setCreating(false);
    toast.success(copy.form.createdDisabled);
    void queryClient.invalidateQueries({ queryKey: queryKeys.deployTargets(workspaceId) });
  }

  const runsByTarget = new Map<string, ApiDeployRun[]>();
  for (const run of runs.data ?? []) {
    runsByTarget.set(run.target, [...(runsByTarget.get(run.target) ?? []), run]);
  }

  return (
    <>
      {creating ? (
        <DeployTargetForm
          workspaceId={workspaceId}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : (
        <Button className="self-start" variant="outline" onClick={() => setCreating(true)}>
          {copy.newTarget}
        </Button>
      )}
      {targets.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : targets.isError ? (
        <ErrorState
          error={targets.error}
          title={copy.loadFailed}
          onRetry={() => void targets.refetch()}
        />
      ) : targets.data.length === 0 ? (
        <EmptyState icon={Rocket} title={copy.empty} description={copy.emptyHint} />
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {targets.data.map((target) => (
            <TargetCard key={target.id} target={target} runs={runsByTarget.get(target.id) ?? []} />
          ))}
        </div>
      )}
      {runs.isError ? (
        <ErrorState error={runs.error} title={copy.runsFailed} onRetry={() => void runs.refetch()} />
      ) : null}
    </>
  );
}

/**
 * Firma → "Deploy hedefleri" (Phase 5 branch 9): the workspace's deploy
 * targets, each with its live SHA, the last five runs (attention states
 * as distinct banners), a status refresh, the enable toggle and the
 * "Deploy" dialog. Everything that touches a target goes through a
 * confirmation; a deploy itself is a separate decision on Onaylar.
 */
export function DeployTargetsView() {
  const { activeWorkspace } = useWorkspace();
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      {activeWorkspace ? (
        <TargetsForWorkspace key={activeWorkspace.id} workspaceId={activeWorkspace.id} />
      ) : (
        <p className="text-sm text-muted-foreground">{tr.common.noWorkspace}</p>
      )}
    </div>
  );
}

