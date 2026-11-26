import Link from "next/link";
import {
  NoSummary,
  SummaryRow,
  toolTitle,
} from "@/components/approvals/renderers/shared";
import { PLATFORM_LABELS } from "@/components/nodes/NodeLabelDialog";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { tr } from "@/lib/i18n/tr";
import type {
  ApiApprovalRequest,
  ApiNodeActionApprovalExecution,
  ApiNodeActionDisplaySummary,
} from "@/types/api";

const TERMINAL = new Set([
  "ok",
  "failed",
  "refused",
  "denied",
  "confirm_timeout",
  "timeout",
  "node_disconnected",
  "node_offline",
  "server_refused",
  "stale_dispatch",
]);

function nodeSummary(
  request: ApiApprovalRequest,
): ApiNodeActionDisplaySummary | null {
  const summary = request.display_summary;
  return summary?.kind === "node_action" && request.tool_name === "node_action"
    ? summary
    : null;
}

function nodeExecution(
  request: ApiApprovalRequest,
): ApiNodeActionApprovalExecution | null {
  if (request.tool_name !== "node_action" || !request.execution) return null;
  const execution = request.execution as ApiNodeActionApprovalExecution;
  return typeof execution.status === "string" ? execution : null;
}

/**
 * Phase 13 D9: the target PC as the owner knows it -- "Masaüstü (Çalışma
 * odası, Linux)" -- so a gate-class action is approved for a named PC,
 * never an id.
 */
export function nodeHeader(summary: ApiNodeActionDisplaySummary): string {
  const platform = summary.node_platform
    ? (PLATFORM_LABELS[summary.node_platform] ?? summary.node_platform)
    : "";
  const details = [summary.node_room ?? "", platform].filter(Boolean);
  return details.length
    ? `${summary.node_name} (${details.join(", ")})`
    : summary.node_name;
}

export function statusLabel(status: string): string {
  return tr.hud.terminal.statuses[status] ?? status;
}

/**
 * The D6 summary of a gated Mac action: the server's whitelisted
 * `describe_request` fields only, rendered as text. The Mac's own window
 * renders its own sentence from the args; the owner compares the two.
 */
export function NodeActionSummary({
  summary,
}: {
  summary: ApiNodeActionDisplaySummary;
}) {
  const copy = tr.approvals.nodeAction;
  return (
    <div className="flex flex-col gap-1" data-testid="node-action-summary">
      <dl className="flex flex-col gap-1 text-sm">
        <SummaryRow label={copy.capability}>{summary.title}</SummaryRow>
        {summary.node_name ? (
          <SummaryRow label={copy.node}>
            <span data-testid="node-action-node">{nodeHeader(summary)}</span>
          </SummaryRow>
        ) : null}
        {summary.repo_name ? (
          <SummaryRow label={copy.repo}>{summary.repo_name}</SummaryRow>
        ) : null}
        {summary.command_kind ? (
          <SummaryRow label={copy.command}>
            <code className="font-mono">{summary.command_kind}</code>
          </SummaryRow>
        ) : null}
        {summary.recipe ? (
          <SummaryRow label={copy.recipe}>{summary.recipe}</SummaryRow>
        ) : null}
        {summary.bundle_id ? (
          <SummaryRow label={copy.app}>
            <code className="font-mono">{summary.bundle_id}</code>
          </SummaryRow>
        ) : null}
        {summary.element_title ? (
          <SummaryRow label={copy.element}>
            {summary.role ? `${summary.role}: ` : ""}
            {summary.element_title}
          </SummaryRow>
        ) : null}
        {summary.text !== undefined ? (
          <SummaryRow label={copy.text}>
            <span className="whitespace-pre-wrap">{summary.text}</span>
            {summary.submit ? (
              <span className="block text-xs">{copy.submit}</span>
            ) : null}
          </SummaryRow>
        ) : null}
        {summary.trust_verdict ? (
          <SummaryRow label={copy.trust}>
            {copy.trustVerdicts[summary.trust_verdict] ?? summary.trust_verdict}
            {summary.trust_reason ? (
              <code className="ml-1 font-mono text-xs text-muted-foreground">
                {summary.trust_reason}
              </code>
            ) : null}
          </SummaryRow>
        ) : null}
      </dl>
      {summary.voice && summary.voice_label ? (
        <p className="text-xs font-medium" data-testid="node-action-voice">
          {summary.voice_label}
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">{summary.note}</p>
      <p className="text-xs text-muted-foreground">
        {copy.expires(Math.round(summary.expires_in_seconds / 60))}
      </p>
    </div>
  );
}

function Summary({ request }: { request: ApiApprovalRequest }) {
  const summary = nodeSummary(request);
  if (!summary) return <NoSummary />;
  return <NodeActionSummary summary={summary} />;
}

function Execution({ request }: { request: ApiApprovalRequest }) {
  const execution = nodeExecution(request);
  if (!execution) return null;
  return (
    <div
      className="flex flex-col gap-1 text-sm"
      data-testid="node-action-execution"
    >
      <p>
        {tr.approvals.nodeAction.execution(statusLabel(execution.status))}
        {execution.exit_code !== null
          ? ` (${tr.approvals.nodeAction.exitCode(execution.exit_code)})`
          : ""}
      </p>
      <Link
        href="/hud"
        className="self-start text-xs underline underline-offset-4"
      >
        {tr.approvals.nodeAction.openTerminal}
      </Link>
    </div>
  );
}

/** `node_action` (Phase 10 D6): a gate-class action on the owner's Mac. */
export const nodeActionRenderer: ApprovalRenderer = {
  toolName: "node_action",
  title: (request) => toolTitle(request.tool_name),
  Summary,
  Execution,
  consequence: (_request, decision) =>
    tr.approvals.consequence.node_action[decision],
  pollInterval: (request) => {
    if (request.status !== "approved") return null;
    const execution = nodeExecution(request);
    return execution && TERMINAL.has(execution.status) ? null : 2000;
  },
};
