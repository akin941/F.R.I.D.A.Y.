import type { HarnessAgent, HarnessJobStatus } from "@/types/api";

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
