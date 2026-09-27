import { tr } from "@/lib/i18n/tr";
import type { ApprovalStatus } from "@/types/api";

// Strings live in the Turkish catalog (lib/i18n/tr.ts); re-exported here
// under their established names next to the approval logic.
export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = tr.status.approval;

export const APPROVAL_STATUSES: ApprovalStatus[] = ["pending", "approved", "rejected", "expired"];

// vault_export's (and any generic tool's) execution statuses. Harness tools
// report the implementation job's own status instead -- see
// IMPL_STATUS_LABELS in components/harness/labels.ts.
export const EXECUTION_STATUS_LABELS: Record<string, string> = tr.status.execution;

// Human title per tool; a tool without one keeps its raw `tool_name` title.
export const TOOL_TITLES: Record<string, string> = tr.approvals.toolTitles;

/** Only owner/admin roles decide (approvals.services._CURATOR_ROLES). */
export function canDecideInRole(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin";
}

