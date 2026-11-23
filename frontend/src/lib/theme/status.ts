import type {
  AgentStatus,
  ApiNodeStatus,
  AgentStepStatus,
  ApprovalStatus,
  CiConclusion,
  CodingSessionStatus,
  ConnectorWriteStatus,
  DeployRunStatus,
  HarnessJobStatus,
  ImplStatus,
  KnowledgeStatus,
  MemoryStatus,
  TaskStatus,
} from "@/types/api";

/**
 * Semantic tone of a status. Every domain status maps to exactly one tone;
 * a tone maps to the semantic color tokens in globals.css
 * (`--success`/`--warning`/`--info`/`--danger`, each with `-foreground`
 * and `-muted`). `neutral` uses the muted surface.
 *
 * - success: reached the goal (approved, succeeded, ready, done)
 * - info: moving on its own (running, processing, implementing)
 * - warning: waiting for a human (pending approval, a gate)
 * - danger: failed or refused
 * - neutral: inert / withdrawn (cancelled, archived, expired, skipped)
 */
export type StatusTone = "success" | "warning" | "info" | "danger" | "neutral";

export const STATUS_TONES = {
  approval: {
    pending: "warning",
    approved: "success",
    rejected: "danger",
    expired: "neutral",
  },
  agent: {
    planning: "info",
    running: "info",
    blocked_on_approval: "warning",
    blocked_on_confirmation: "warning",
    completed: "success",
    failed: "danger",
    cancelled: "neutral",
  },
  agentStep: {
    pending: "neutral",
    running: "info",
    succeeded: "success",
    failed: "danger",
    blocked_on_approval: "warning",
    blocked_on_confirmation: "warning",
    skipped: "neutral",
  },
  harnessJob: {
    pending: "neutral",
    running: "info",
    succeeded: "success",
    failed: "danger",
    cancelled: "neutral",
  },
  // The 8 implementation pipeline statuses.
  implementation: {
    pending_gate_a: "warning",
    implementing: "info",
    pending_review: "info",
    pending_gate_b: "warning",
    pushing: "info",
    succeeded: "success",
    failed: "danger",
    cancelled: "neutral",
  },
  gate: {
    pending: "warning",
    approved: "success",
    rejected: "danger",
    expired: "neutral",
  },
  memory: {
    pending_approval: "warning",
    approved: "success",
    rejected: "danger",
    archived: "neutral",
  },
  knowledge: {
    pending: "neutral",
    processing: "info",
    ready: "success",
    failed: "danger",
  },
  task: {
    open: "info",
    done: "success",
    cancelled: "neutral",
  },
  // Phase 5 branch 9. A degraded deploy is a warning, never success; an
  // unknown outcome or a failed rollback is danger.
  deployRun: {
    pending: "neutral",
    checking: "info",
    deploying: "info",
    health_checking: "info",
    rolling_back: "warning",
    succeeded: "success",
    succeeded_degraded: "warning",
    rolled_back: "warning",
    rollback_failed: "danger",
    health_failed: "danger",
    unknown_outcome: "danger",
    refused: "neutral",
    failed: "danger",
    cancelled: "neutral",
  },
  connectorWrite: {
    pending: "neutral",
    running: "info",
    succeeded: "success",
    failed: "danger",
    unknown_outcome: "danger",
  },
  ci: {
    success: "success",
    failure: "danger",
    pending: "info",
    unknown: "neutral",
  },
  pin: {
    ok: "success",
    mismatch: "danger",
    missing: "danger",
    unchecked: "neutral",
  },
  // Phase 8A branch 6: a coding session waiting on the owner is a warning.
  codingSession: {
    running: "info",
    waiting_for_owner: "warning",
    stopped: "neutral",
    stale: "neutral",
  },
  // Phase 9 D11: a Mac node's link. The kill switch is danger, an ageing
  // credential a warning; revoked / never paired are inert.
  node: {
    connected: "success",
    disconnected: "neutral",
    kill_switch: "danger",
    revoked: "neutral",
    credential_expiring: "warning",
    never_paired: "neutral",
  },
  // Phase 9 D8: a chain row's terminal status.
  nodeInvocation: {
    ok: "success",
    refused: "warning",
    error: "danger",
    timeout: "danger",
    node_disconnected: "danger",
    server_refused: "neutral",
    node_offline: "neutral",
    busy: "warning",
    bus_unavailable: "danger",
    failed: "danger",
    denied: "neutral",
    confirm_timeout: "neutral",
    stale_dispatch: "neutral",
  },
} as const satisfies {
  approval: Record<ApprovalStatus, StatusTone>;
  agent: Record<AgentStatus, StatusTone>;
  agentStep: Record<AgentStepStatus, StatusTone>;
  harnessJob: Record<HarnessJobStatus, StatusTone>;
  implementation: Record<ImplStatus, StatusTone>;
  gate: Record<"pending" | "approved" | "rejected" | "expired", StatusTone>;
  memory: Record<MemoryStatus, StatusTone>;
  knowledge: Record<KnowledgeStatus, StatusTone>;
  task: Record<TaskStatus, StatusTone>;
  deployRun: Record<DeployRunStatus, StatusTone>;
  connectorWrite: Record<ConnectorWriteStatus, StatusTone>;
  ci: Record<CiConclusion, StatusTone>;
  pin: Record<"ok" | "mismatch" | "missing" | "unchecked", StatusTone>;
  codingSession: Record<CodingSessionStatus, StatusTone>;
  node: Record<ApiNodeStatus, StatusTone>;
  nodeInvocation: Record<string, StatusTone>;
};

export type StatusDomain = keyof typeof STATUS_TONES;

/** Tone for `status` in `domain`; an unknown status (a newer backend) is neutral. */
export function statusTone(domain: StatusDomain, status: string): StatusTone {
  const tones = STATUS_TONES[domain] as Record<string, StatusTone>;
  return tones[status] ?? "neutral";
}

/** Pill classes per tone: tinted surface + AA text (checked in tokens.test.ts). */
export const TONE_PILL_CLASSES: Record<StatusTone, string> = {
  success: "border-success/30 bg-success-muted text-success",
  warning: "border-warning/30 bg-warning-muted text-warning",
  info: "border-info/30 bg-info-muted text-info",
  danger: "border-danger/30 bg-danger-muted text-danger",
  neutral: "border-border bg-muted text-muted-foreground",
};

/** Solid dot/marker per tone. */
export const TONE_DOT_CLASSES: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  info: "bg-info",
  danger: "bg-danger",
  neutral: "bg-muted-foreground",
};
