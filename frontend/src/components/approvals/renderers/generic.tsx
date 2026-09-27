import { EXECUTION_STATUS_LABELS } from "@/components/approvals/labels";
import { toolTitle } from "@/components/approvals/renderers/shared";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { tr } from "@/lib/i18n/tr";
import { POLL_FAST_MS } from "@/lib/query/polling";

function GenericExecution({ status }: { status: unknown }) {
  if (typeof status !== "string") return null;
  return <p className="text-sm">Çalışma durumu: {EXECUTION_STATUS_LABELS[status] ?? status}</p>;
}

/**
 * The fallback for any tool without its own renderer: the raw tool name,
 * no summary (an unknown `display_summary` shape is never guessed at), a
 * status-only execution line and the generic consequence.
 */
export const genericRenderer: ApprovalRenderer = {
  toolName: null,
  title: (request) => toolTitle(request.tool_name),
  Summary: () => null,
  Execution: ({ request }) =>
    request.execution ? (
      <GenericExecution status={(request.execution as { status?: unknown }).status} />
    ) : null,
  consequence: (_request, decision) => tr.approvals.consequence.generic[decision],
  pollInterval: (request) => {
    const status = (request.execution as { status?: unknown } | null)?.status;
    return status === "pending" || status === "running" ? POLL_FAST_MS : null;
  },
};
