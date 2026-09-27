import { implPollInterval } from "@/components/harness/labels";
import { tr } from "@/lib/i18n/tr";
import { POLL_FAST_MS } from "@/lib/query/polling";
import type { ApiApprovalRequest, ApprovalStatus } from "@/types/api";

// Strings live in the Turkish catalog (lib/i18n/tr.ts); re-exported here
// under their established names next to the approval logic.
export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = tr.status.approval;

export const APPROVAL_STATUSES: ApprovalStatus[] = ["pending", "approved", "rejected", "expired"];

// vault_export's (and any generic tool's) execution statuses. Harness tools
// report the implementation job's own status instead -- see
// IMPL_STATUS_LABELS in components/harness/labels.ts.
export const EXECUTION_STATUS_LABELS: Record<string, string> = tr.status.execution;

export const HARNESS_TOOL_NAMES = ["harness_implement", "harness_push"] as const;

export function isHarnessTool(toolName: string): boolean {
  return (HARNESS_TOOL_NAMES as readonly string[]).includes(toolName);
}

// Human title per tool; a tool without one keeps its raw `tool_name` title.
export const TOOL_TITLES: Record<string, string> = tr.approvals.toolTitles;

// What deciding means, shown in the confirmation step before the
// decision is sent.
export function decisionConsequence(toolName: string, action: "approve" | "reject"): string {
  const consequences = tr.approvals.consequence;
  if (toolName === "harness_implement" || toolName === "harness_push") {
    return consequences[toolName][action];
  }
  return consequences.generic[action];
}

/**
 * How often the approvals view should refresh for this request's
 * execution, or null when it can no longer change. Harness executions
 * report the implementation job's own status: fast while a container is
 * working, slow while the job merely waits at a gate for a human (it can
 * sit there for hours), none once terminal. Every other tool uses the
 * vault-export vocabulary (pending/running -> fast).
 */
export function executionPollInterval(request: ApiApprovalRequest): number | null {
  const status = request.execution?.status;
  if (typeof status !== "string") return null;
  if (isHarnessTool(request.tool_name)) return implPollInterval(status);
  return status === "pending" || status === "running" ? POLL_FAST_MS : null;
}
