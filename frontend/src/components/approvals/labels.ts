import type { ApprovalStatus } from "@/types/api";

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: "Beklemede",
  approved: "Onaylandı",
  rejected: "Reddedildi",
  expired: "Süresi doldu",
};

export const APPROVAL_STATUSES: ApprovalStatus[] = ["pending", "approved", "rejected", "expired"];

export const EXECUTION_STATUS_LABELS: Record<string, string> = {
  pending: "Çalışması bekleniyor",
  running: "Çalışıyor",
  succeeded: "Başarılı",
  failed: "Başarısız",
};
