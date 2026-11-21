import { EXECUTION_STATUS_LABELS } from "@/components/approvals/labels";
import { genericRenderer } from "@/components/approvals/renderers/generic";
import { toolTitle } from "@/components/approvals/renderers/shared";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { tr } from "@/lib/i18n/tr";
import type { ApiVaultExportExecution } from "@/types/api";

function VaultExportExecutionLine({ execution }: { execution: ApiVaultExportExecution }) {
  const hasCounters =
    typeof execution.written_count === "number" &&
    typeof execution.updated_count === "number" &&
    typeof execution.removed_count === "number";
  return (
    <p className="text-sm">
      Çalışma durumu: {EXECUTION_STATUS_LABELS[execution.status] ?? execution.status}
      {execution.status === "failed" && execution.error_message
        ? ` — ${execution.error_message}`
        : null}
      {execution.status === "succeeded" && hasCounters
        ? ` (${execution.written_count} yazıldı, ${execution.updated_count} güncellendi, ${execution.removed_count} silindi)`
        : null}
      {execution.status === "succeeded" ? <ConflictLine execution={execution} /> : null}
    </p>
  );
}

/** Phase 8B branch 9 (D12): how many owner-edited notes the run saved and where. */
export function ConflictLine({ execution }: { execution: ApiVaultExportExecution }) {
  const count = execution.conflicts_saved;
  if (typeof count !== "number") return null;
  const copy = tr.approvals.vaultExport;
  const folders = (execution.conflicts_folders ?? []).map((f) => `\`${f}\``).join(" ve ");
  return (
    <span className="block text-xs text-muted-foreground" data-testid="vault-conflicts">
      {count === 0 ? copy.noConflicts : folders ? copy.conflicts(count, folders) : copy.conflictsNoFolder(count)}
    </span>
  );
}

/** `vault_export` (filesystem export, gate-class): no request summary, a run line with counters. */
export const vaultExportRenderer: ApprovalRenderer = {
  toolName: "vault_export",
  title: (request) => toolTitle(request.tool_name),
  Summary: () => null,
  // Approval and actual completion are two separate async moments
  // (dispatch is async) -- this never implies "approved" means "done".
  Execution: ({ request }) =>
    request.execution ? (
      <VaultExportExecutionLine execution={request.execution as ApiVaultExportExecution} />
    ) : null,
  consequence: (_request, decision) => tr.approvals.consequence.vault_export[decision],
  pollInterval: genericRenderer.pollInterval,
};
