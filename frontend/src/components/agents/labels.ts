import type { AgentStatus, AgentStepKind, AgentStepStatus } from "@/types/api";

export const AGENT_STATUS_LABELS: Record<AgentStatus, string> = {
  planning: "Planning",
  running: "Running",
  blocked_on_approval: "Waiting for approval",
  blocked_on_confirmation: "Waiting for confirmation",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
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
  pending: "Pending",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  blocked_on_approval: "Waiting for approval",
  blocked_on_confirmation: "Waiting for confirmation",
  skipped: "Skipped",
};

export const AGENT_STEP_KIND_LABELS: Record<AgentStepKind, string> = {
  read: "Read",
  tool_request: "Tool request",
  task_draft: "Task draft",
};
