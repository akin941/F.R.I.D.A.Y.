import { isImplTerminal } from "@/components/harness/labels";
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
 * Whether a request's execution may still change on its own, so the
 * approvals view should keep polling. Harness executions report the
 * implementation job's status (non-terminal until succeeded/failed/
 * cancelled); every other tool uses the vault-export vocabulary.
 */
export function isExecutionInFlight(request: ApiApprovalRequest): boolean {
  const status = request.execution?.status;
  if (typeof status !== "string") return false;
  if (isHarnessTool(request.tool_name)) return !isImplTerminal(status);
  return status === "pending" || status === "running";
}
