import type {
  HarnessAgent,
  HarnessJobFailureReason,
  HarnessJobStatus,
  ImplFailureReason,
  ImplStatus,
} from "@/types/api";

export const HARNESS_JOB_STATUS_LABELS: Record<HarnessJobStatus, string> = {
  pending: "Beklemede",
  running: "Çalışıyor",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

// A job still in one of these statuses may still change on its own (the
// worker advancing in the background) -- the detail view polls silently
// while true, same reasoning as AgentDetail's in-flight poll.
export const HARNESS_JOB_IN_FLIGHT_STATUSES: HarnessJobStatus[] = ["pending", "running"];

export const HARNESS_AGENT_LABELS: Record<HarnessAgent, string> = {
  claude_code: "Claude Code",
  codex_cli: "Codex CLI",
};

// The implementation job's failure vocabulary is a superset of the
// discovery job's (`HarnessJobFailureReason`), so one map covers both.
// `""` (no failure) is never rendered -- callers only show a set reason.
export const HARNESS_FAILURE_REASON_LABELS: Record<Exclude<ImplFailureReason, "">, string> = {
  timeout: "Zaman aşımı",
  runner_error: "Çalıştırıcı hatası",
  budget_exceeded: "Bütçe aşıldı",
  killed: "Durduruldu",
  gate_a_rejected: "Plan onayı (Kapı A) reddedildi",
  gate_b_rejected: "Gönderim onayı (Kapı B) reddedildi",
  gate_expired: "Onayın süresi doldu",
  test_command_not_configured: "Depo için test komutu tanımlı değil",
  test_result_unavailable: "Test sonucu alınamadı",
  tests_failed: "Testler başarısız",
  review_changes_requested: "İnceleme değişiklik istedi",
  review_unavailable: "İnceleme sonucu alınamadı",
  worktree_expired: "Çalışma ağacının süresi doldu",
  push_failed: "Gönderim (push) başarısız",
  no_changes: "Ajan hiçbir değişiklik üretmedi",
  secret_leak: "Değişikliklerde gizli bilgi sızıntısı tespit edildi",
};

/**
 * Turkish label for a raw harness `failure_reason` (discovery or
 * implementation). An unknown value (a backend newer than this UI) falls
 * back to the raw string rather than rendering nothing.
 */
export function harnessFailureReasonLabel(
  reason: HarnessJobFailureReason | ImplFailureReason | string,
): string {
  return HARNESS_FAILURE_REASON_LABELS[reason as Exclude<ImplFailureReason, "">] ?? reason;
}

export const IMPL_STATUS_LABELS: Record<ImplStatus, string> = {
  pending_gate_a: "Plan onayı bekleniyor (Kapı A)",
  implementing: "Uygulanıyor",
  pending_review: "İnceleniyor",
  pending_gate_b: "Gönderim onayı bekleniyor (Kapı B)",
  pushing: "Gönderiliyor",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

export const IMPL_TERMINAL_STATUSES: ImplStatus[] = ["succeeded", "failed", "cancelled"];

export function isImplTerminal(status: ImplStatus | string): boolean {
  return IMPL_TERMINAL_STATUSES.includes(status as ImplStatus);
}

// The linear happy path the pipeline stepper walks; `failed`/`cancelled`
// are rendered as a stop marker on whichever of these the job had reached.
export const IMPL_PIPELINE_STEPS: ImplStatus[] = [
  "pending_gate_a",
  "implementing",
  "pending_review",
  "pending_gate_b",
  "pushing",
  "succeeded",
];

export const IMPL_PIPELINE_STEP_LABELS: Record<ImplStatus, string> = {
  pending_gate_a: "Plan onayı",
  implementing: "Uygulama",
  pending_review: "İnceleme",
  pending_gate_b: "Gönderim onayı",
  pushing: "Gönderim",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

export const GATE_STATUS_LABELS: Record<"pending" | "approved" | "rejected" | "expired", string> = {
  pending: "Karar bekliyor",
  approved: "Onaylandı",
  rejected: "Reddedildi",
  expired: "Süresi doldu / geri alındı",
};

export const REVIEW_STATUS_LABELS: Record<"pending" | "running" | "succeeded" | "failed", string> =
  {
    pending: "Beklemede",
    running: "Çalışıyor",
    succeeded: "Tamamlandı",
    failed: "Başarısız",
  };

export const REVIEW_VERDICT_LABELS: Record<string, string> = {
  approved: "Onaylandı",
  changes_requested: "Değişiklik istendi",
};
