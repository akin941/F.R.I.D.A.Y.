import Link from "next/link";
import { NoSummary, SummaryRow, toolTitle } from "@/components/approvals/renderers/shared";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { DeployRunBanner } from "@/components/deploys/DeployRunBanner";
import {
  DEPLOY_RUN_STATUS_LABELS,
  deployPollInterval,
  deployRunHref,
  shortSha,
} from "@/components/deploys/labels";
import { StatusPill } from "@/components/ui/status-pill";
import { tr } from "@/lib/i18n/tr";
import type { ApiApprovalRequest, ApiDeployDisplaySummary, ApiDeployExecution } from "@/types/api";

const copy = tr.deploys;

export function deploySummary(request: ApiApprovalRequest): ApiDeployDisplaySummary | null {
  const summary = request.display_summary;
  return summary?.kind === "deploy" && request.tool_name === "deploy" ? summary : null;
}

function Sha({ sha }: { sha: string }) {
  if (!sha) return <span className="text-muted-foreground">—</span>;
  // The full SHA, never only an abbreviation: it is what will be deployed.
  return <code className="font-mono text-xs break-all">{sha}</code>;
}

function yesNo(value: boolean) {
  return value ? copy.yes : copy.no;
}

/**
 * The whole decided plan (brief D10), from the server's snapshot only:
 * target, SSH destination, app, repo, full SHAs (new, live, rollback),
 * the commit title labelled as repo text, CI at request time, the health
 * plan, whether a failure rolls back -- and the verb sequence.
 */
function DeploySummary({ request }: { request: ApiApprovalRequest }) {
  const summary = deploySummary(request);
  if (!summary) return <NoSummary />;
  const port = summary.ssh_port && summary.ssh_port !== 22 ? `:${summary.ssh_port}` : "";
  const health = summary.health;
  return (
    <div className="flex flex-col gap-2" data-testid="deploy-summary">
      <p className="text-sm font-medium">{summary.text}</p>
      <dl className="flex flex-col gap-1 text-sm">
        <SummaryRow label="Hedef">
          {summary.target_name} · {summary.environment}
        </SummaryRow>
        <SummaryRow label={copy.destination}>
          <code className="font-mono text-xs break-all">
            {summary.ssh_destination}
            {port}
          </code>
        </SummaryRow>
        <SummaryRow label={copy.app}>
          <code className="font-mono text-xs">{summary.remote_app}</code>
        </SummaryRow>
        <SummaryRow label={copy.repo}>
          <code className="font-mono text-xs break-all">{summary.repo_git_url}</code>
        </SummaryRow>
        <SummaryRow label={copy.branch}>
          <code className="font-mono text-xs">{summary.tracked_branch}</code>
        </SummaryRow>
        <SummaryRow label="Dağıtılacak commit">
          <Sha sha={summary.sha} />
        </SummaryRow>
        <SummaryRow label="Şu an canlı">
          <Sha sha={summary.current_sha} />
        </SummaryRow>
        <SummaryRow label={copy.dialog.commitTitle}>
          {summary.commit_title ? (
            <span className="whitespace-pre-wrap">{summary.commit_title}</span>
          ) : (
            <span className="text-muted-foreground">— (elle girilen SHA)</span>
          )}
        </SummaryRow>
        <SummaryRow label="CI (istek anında)">
          <span className="flex flex-wrap items-center gap-2">
            <StatusPill
              domain="ci"
              status={summary.ci_conclusion}
              label={tr.status.ci[summary.ci_conclusion as keyof typeof tr.status.ci] ?? summary.ci_conclusion}
            />
            <span className="text-xs text-muted-foreground">
              {copy.requireCi}: {yesNo(summary.require_ci_success)} · kaynak: {summary.commit_source}
            </span>
          </span>
        </SummaryRow>
        <SummaryRow label={copy.health}>
          {health.window_seconds != null && health.checks != null && health.fail_threshold != null
            ? copy.healthPlan(health.checks, health.window_seconds, health.fail_threshold)
            : "—"}
          {health.health_url ? (
            <code className="block font-mono text-xs break-all">{health.health_url}</code>
          ) : null}
        </SummaryRow>
        <SummaryRow label={copy.autoRollback}>
          {summary.auto_rollback ? (
            <span>
              {copy.yes} — yalnızca <Sha sha={summary.rollback_sha} />
            </span>
          ) : (
            <span className="font-medium">{copy.no} — başarısızlıkta hiçbir şey geri alınmaz</span>
          )}
        </SummaryRow>
        <SummaryRow label="Adımlar">
          <code className="font-mono text-xs">{summary.verbs.join(" → ")}</code>
        </SummaryRow>
      </dl>
    </div>
  );
}

function deployExecution(request: ApiApprovalRequest): ApiDeployExecution | null {
  if (request.tool_name !== "deploy" || !request.execution) return null;
  const execution = request.execution as Partial<ApiDeployExecution>;
  return typeof execution.run_id === "string" && typeof execution.status === "string"
    ? (execution as ApiDeployExecution)
    : null;
}

function DeployExecution({ request }: { request: ApiApprovalRequest }) {
  const execution = deployExecution(request);
  if (!execution) return null;
  const summary = deploySummary(request);
  return (
    <div className="flex flex-col gap-2">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        Deploy durumu:
        <StatusPill
          domain="deployRun"
          status={execution.status}
          label={DEPLOY_RUN_STATUS_LABELS[execution.status] ?? execution.status}
        />
      </p>
      <DeployRunBanner
        status={execution.status}
        rollbackSha={summary?.rollback_sha ?? ""}
        errorCode={execution.error_code}
      />
      <Link
        href={deployRunHref({ id: execution.run_id, workspace: request.workspace })}
        className="self-start text-xs underline underline-offset-4"
      >
        Çalıştırmayı aç
      </Link>
    </div>
  );
}

/** `deploy` (ADR-010 decision 7): the third owner decision. */
export const deployRenderer: ApprovalRenderer = {
  toolName: "deploy",
  title: (request) => toolTitle(request.tool_name),
  Summary: DeploySummary,
  Execution: DeployExecution,
  consequence: (request, decision) => {
    const consequence = tr.approvals.consequence.deploy;
    const summary = deploySummary(request);
    if (decision === "approve" && summary) {
      return consequence.approveWith(
        shortSha(summary.sha),
        `${summary.target_name} (${summary.environment})`,
        summary.auto_rollback,
        shortSha(summary.rollback_sha),
      );
    }
    return consequence[decision];
  },
  pollInterval: (request) => {
    const execution = deployExecution(request);
    return execution ? deployPollInterval(execution.status) : null;
  },
};
