import { tr } from "@/lib/i18n/tr";
import type {
  AgentFailureReason,
  AgentStatus,
  AgentStepKind,
  AgentStepStatus,
} from "@/types/api";

// Strings live in the Turkish catalog (lib/i18n/tr.ts); re-exported here
// under their established names next to the agent status logic.
export const AGENT_STATUS_LABELS: Record<AgentStatus, string> = tr.status.agent;

// `""` (no failure) is never rendered -- callers only show a reason when set.
export const AGENT_FAILURE_REASON_LABELS: Record<Exclude<AgentFailureReason, "">, string> =
  tr.agent.failureReason;

/** Turkish label for a raw failure_reason; unknown values fall back verbatim. */
export function agentFailureReasonLabel(reason: string): string {
  return AGENT_FAILURE_REASON_LABELS[reason as Exclude<AgentFailureReason, "">] ?? reason;
}

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
// (the execution engine advancing in the background) -- list and detail
// poll while any row is in flight.
export const AGENT_IN_FLIGHT_STATUSES: AgentStatus[] = [
  "planning",
  "running",
  "blocked_on_approval",
  "blocked_on_confirmation",
];

export const AGENT_STEP_STATUS_LABELS: Record<AgentStepStatus, string> = tr.status.agentStep;

export const AGENT_STEP_KIND_LABELS: Record<AgentStepKind, string> = tr.agent.stepKind;
