import { NoSummary, SummaryRow, toolTitle } from "@/components/approvals/renderers/shared";
import type { ApprovalRenderer, Decision } from "@/components/approvals/renderers/types";
import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/ui/status-pill";
import { tr } from "@/lib/i18n/tr";
import { POLL_FAST_MS } from "@/lib/query/polling";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import type {
  ApiApprovalRequest,
  ApiConnectorWriteDisplaySummary,
  ApiConnectorWriteExecution,
} from "@/types/api";

const copy = tr.connectors;

/** Generic connector writes are gated as `mcp_<connector>_<tool>` (brief D6). */
export const GENERIC_CONNECTOR_WRITE_PREFIX = "mcp_";

export function connectorWriteSummary(
  request: ApiApprovalRequest,
): ApiConnectorWriteDisplaySummary | null {
  const summary = request.display_summary;
  return summary?.kind === "connector_write" ? summary : null;
}

function row(summary: ApiConnectorWriteDisplaySummary, label: string): string {
  return summary.rows.find((r) => r.label === label)?.value ?? "";
}

/**
 * Every connector write's decision content, from the snapshot only (brief
 * D6): the tool renderer's rows in full (raw addresses, exact entity ids,
 * the whole body -- nothing paraphrased or truncated), the extra context
 * the task checks, and the exact canonical JSON that will be sent with its
 * sha256. Values are rendered as text (React escapes them), pre-wrapped.
 */
function ConnectorWriteSummary({ request }: { request: ApiApprovalRequest }) {
  const summary = connectorWriteSummary(request);
  if (!summary) return <NoSummary />;
  return (
    <div className="flex flex-col gap-2" data-testid="connector-write-summary">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{summary.connector_name || summary.connector}</span>
        <code className="font-mono text-xs text-muted-foreground">{summary.tool}</code>
        {summary.data_class === "personal" ? (
          <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
            {copy.dataClass.personal}
          </Badge>
        ) : null}
      </p>
      {summary.tool_description ? (
        <p className="text-sm text-muted-foreground">{summary.tool_description}</p>
      ) : null}
      <dl className="flex flex-col gap-1 text-sm">
        {summary.rows.map((r, index) => (
          <SummaryRow key={`${r.label}-${index}`} label={r.label}>
            <span className="whitespace-pre-wrap break-all">{r.value}</span>
          </SummaryRow>
        ))}
        {summary.context_rows.map((r, index) => (
          <SummaryRow key={`ctx-${r.label}-${index}`} label={`${r.label} (kontrol)`}>
            <code className="font-mono text-xs break-all">{r.value}</code>
          </SummaryRow>
        ))}
      </dl>
      <details className="text-xs">
        <summary className="cursor-pointer select-none text-muted-foreground">
          Gönderilecek argümanlar (tam olarak, JSON)
        </summary>
        <pre
          data-testid="connector-write-arguments"
          className="mt-1 max-h-64 overflow-auto rounded-md border bg-muted p-2 font-mono whitespace-pre-wrap break-all"
        >
          {summary.arguments_json}
        </pre>
        <p className="mt-1 break-all text-muted-foreground">sha256: {summary.arguments_sha256}</p>
      </details>
    </div>
  );
}

function writeExecution(request: ApiApprovalRequest): ApiConnectorWriteExecution | null {
  const execution = request.execution as Partial<ApiConnectorWriteExecution> | null;
  if (!execution || typeof execution.status !== "string" || !("error_type" in execution)) {
    return null;
  }
  return execution as ApiConnectorWriteExecution;
}

function ConnectorWriteExecutionLine({ request }: { request: ApiApprovalRequest }) {
  const execution = writeExecution(request);
  if (!execution) return null;
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        Gönderim durumu:
        <StatusPill
          domain="connectorWrite"
          status={execution.status}
          label={tr.status.connectorWrite[execution.status] ?? execution.status}
        />
      </p>
      {execution.error_type ? (
        <p className="text-destructive">
          {copy.writeError[execution.error_type] ?? execution.error_type}
        </p>
      ) : null}
      {execution.status === "unknown_outcome" ? (
        <p role="alert" className="font-medium text-danger">
          {copy.unknownOutcomeNote}
        </p>
      ) : null}
    </div>
  );
}

function pollInterval(request: ApiApprovalRequest): number | null {
  const status = writeExecution(request)?.status;
  return status === "pending" || status === "running" ? POLL_FAST_MS : null;
}

function connectorWriteRenderer(
  toolName: string | null,
  consequence: (request: ApiApprovalRequest, decision: Decision) => string,
  title: (request: ApiApprovalRequest) => string = (request) => toolTitle(request.tool_name),
): ApprovalRenderer {
  return {
    toolName,
    title,
    Summary: ConnectorWriteSummary,
    Execution: ConnectorWriteExecutionLine,
    consequence,
    pollInterval,
  };
}

const consequences = tr.approvals.consequence;

export const githubOpenPrRenderer = connectorWriteRenderer("github_open_pr", (request, decision) => {
  const summary = connectorWriteSummary(request);
  if (decision === "approve" && summary) {
    const repo = row(summary, "Depo");
    const head = row(summary, "Dal (head)");
    const base = row(summary, "Hedef dal (base)");
    if (repo && head && base) return consequences.github_open_pr.approveWith(repo, head, base);
  }
  return consequences.github_open_pr[decision];
});

export const haCallServiceRenderer = connectorWriteRenderer(
  "ha_call_service",
  (request, decision) => {
    const summary = connectorWriteSummary(request);
    const service = summary ? row(summary, "Hizmet") : "";
    if (decision === "approve" && service) return consequences.ha_call_service.approveWith(service);
    return consequences.ha_call_service[decision];
  },
);

export const mailSendRenderer = connectorWriteRenderer(
  "mail_send",
  (_request, decision) => consequences.mail_send[decision],
);

export const calendarCreateEventRenderer = connectorWriteRenderer(
  "calendar_create_event",
  (_request, decision) => consequences.calendar_create_event[decision],
);

/** Any `mcp_<connector>_<tool>` write without its own renderer. */
export const genericConnectorWriteRenderer = connectorWriteRenderer(
  null,
  (_request, decision) => consequences.connector_write[decision],
  (request) => {
    const summary = connectorWriteSummary(request);
    return summary
      ? tr.approvals.connectorWriteTitle(summary.connector_name || summary.connector, summary.tool)
      : toolTitle(request.tool_name);
  },
);
