import type { AgentStatus, AgentStepKind, AgentStepStatus } from "@/types/api";

export const AGENT_STATUS_LABELS: Record<AgentStatus, string> = {
  planning: "Planlanıyor",
  running: "Çalışıyor",
  blocked_on_approval: "Onay bekliyor",
  blocked_on_confirmation: "Onay bekliyor",
  completed: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

export const AGENT_STATUSES: AgentStatus[] = [
  "planning",
  "running",
  "blocked_on_approval",
  "blocked_on_confirmation",
  "completed",
  "failed",
  "cancelled",
];

// An Agent still in one of these statuses may still change on its own
// (the execution engine advancing in the background) -- the detail view
// polls silently while true, same reasoning as ApprovalView's run-status
// poll.
export const AGENT_IN_FLIGHT_STATUSES: AgentStatus[] = [
  "planning",
  "running",
  "blocked_on_approval",
  "blocked_on_confirmation",
];

export const AGENT_STEP_STATUS_LABELS: Record<AgentStepStatus, string> = {
  pending: "Beklemede",
  running: "Çalışıyor",
  succeeded: "Başarılı",
  failed: "Başarısız",
  blocked_on_approval: "Onay bekliyor",
  blocked_on_confirmation: "Onay bekliyor",
  skipped: "Atlandı",
};

export const AGENT_STEP_KIND_LABELS: Record<AgentStepKind, string> = {
  read: "Oku",
  tool_request: "Araç isteği",
  task_draft: "Görev taslağı",
};
