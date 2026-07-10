import type { ApprovalStatus } from "@/types/api";

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  expired: "Expired",
};

export const APPROVAL_STATUSES: ApprovalStatus[] = ["pending", "approved", "rejected", "expired"];

export const EXECUTION_STATUS_LABELS: Record<string, string> = {
  pending: "Waiting to run",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};
