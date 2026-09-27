import { implPollInterval } from "@/components/harness/labels";
import { POLL_FAST_MS } from "@/lib/use-polling";
import type { ApiApprovalRequest, ApprovalStatus } from "@/types/api";

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: "Beklemede",
  approved: "Onaylandı",
  rejected: "Reddedildi",
  expired: "Süresi doldu",
};

export const APPROVAL_STATUSES: ApprovalStatus[] = ["pending", "approved", "rejected", "expired"];

// vault_export's (and any generic tool's) execution statuses. Harness tools
// report the implementation job's own status instead -- see
// IMPL_STATUS_LABELS in components/harness/labels.ts.
export const EXECUTION_STATUS_LABELS: Record<string, string> = {
  pending: "Çalışması bekleniyor",
  running: "Çalışıyor",
  succeeded: "Başarılı",
  failed: "Başarısız",
};

export const HARNESS_TOOL_NAMES = ["harness_implement", "harness_push"] as const;

export function isHarnessTool(toolName: string): boolean {
  return (HARNESS_TOOL_NAMES as readonly string[]).includes(toolName);
}

// Human title per tool; a tool without one keeps its raw `tool_name` title.
export const TOOL_TITLES: Record<string, string> = {
  harness_implement: "Uygulama planı onayı (Kapı A)",
  harness_push: "Gönderim onayı (Kapı B)",
};

// What deciding means, shown in the confirmation step before the
// decision is sent.
export function decisionConsequence(toolName: string, action: "approve" | "reject"): string {
  if (toolName === "harness_implement") {
    return action === "approve"
      ? "Onaylarsan ajan bu plana göre ayrı bir çalışma ağacında değişiklik yapmaya başlar. Dal, ayrı bir gönderim onayı (Kapı B) olmadan uzak depoya gönderilmez."
      : "Reddedersen uygulama işi başarısız olarak kapanır; depoda hiçbir değişiklik yapılmaz.";
  }
  if (toolName === "harness_push") {
    return action === "approve"
      ? "Onaylarsan bu dal uzak depoya gönderilir (push). Gönderim geri alınamaz."
      : "Reddedersen değişiklikler gönderilmez ve uygulama işi başarısız olarak kapanır.";
  }
  return action === "approve"
    ? "Onaylarsan bu istek çalıştırılır."
    : "Reddedersen bu istek çalıştırılmaz.";
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
