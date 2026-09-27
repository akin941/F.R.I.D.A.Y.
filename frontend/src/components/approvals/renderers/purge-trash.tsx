import { NoSummary, toolTitle } from "@/components/approvals/renderers/shared";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { tr } from "@/lib/i18n/tr";
import type { ApiApprovalRequest, ApiPurgeTrashDisplaySummary } from "@/types/api";

function purgeSummary(request: ApiApprovalRequest): ApiPurgeTrashDisplaySummary | null {
  const summary = request.display_summary;
  return summary?.kind === "purge_trash" && request.tool_name === "purge_trash" ? summary : null;
}

function PurgeSummary({ request }: { request: ApiApprovalRequest }) {
  const summary = purgeSummary(request);
  if (!summary) return <NoSummary />;
  const total = summary.task_count + summary.document_count;
  const more = total - summary.titles.length;
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="font-medium">
        {tr.approvals.purgeSummary(summary.task_count, summary.document_count)}
      </p>
      {summary.titles.length > 0 ? (
        <ul className="list-inside list-disc">
          {summary.titles.map((title, index) => (
            <li key={index} className="break-words">
              {title}
            </li>
          ))}
          {more > 0 ? (
            <li className="list-none text-muted-foreground">{tr.approvals.more(more)}</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * `purge_trash` (ADR-009): the gate-class permanent delete of trashed
 * tasks/documents. The purge commits with the decision itself -- there is
 * no separate execution to follow.
 */
export const purgeTrashRenderer: ApprovalRenderer = {
  toolName: "purge_trash",
  title: (request) => toolTitle(request.tool_name),
  Summary: PurgeSummary,
  Execution: () => null,
  consequence: (request, decision) => {
    const copy = tr.approvals.consequence.purge_trash;
    const summary = purgeSummary(request);
    if (decision === "approve" && summary) {
      return copy.approveWith(summary.task_count, summary.document_count);
    }
    return copy[decision];
  },
  pollInterval: () => null,
};
