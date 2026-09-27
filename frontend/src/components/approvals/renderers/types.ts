import type { ApiApprovalRequest } from "@/types/api";

export type Decision = "approve" | "reject";

/**
 * How one gated tool's approval request is shown and decided (Phase 4
 * branch 5). Every field that reads tool-specific data (`display_summary`,
 * `execution`) lives here, keyed by `tool_name` -- so e.g. a harness
 * execution can never be printed with vault-export counters.
 *
 * `display_summary` is the server's whitelisted `describe_request` output
 * (curator + web channel only; `null` otherwise) -- a renderer must cope
 * with it being absent and never read `payload` (never serialized).
 */
export interface ApprovalRenderer {
  /** The `tool_name` this renderer is for; `null` for the generic fallback. */
  toolName: string | null;
  /** Human title; the raw `tool_name` when the tool has none. */
  title: (request: ApiApprovalRequest) => string;
  /** What is being decided (from `display_summary`). */
  Summary: (props: { request: ApiApprovalRequest }) => React.ReactNode;
  /** Run status after approval (from `execution`). */
  Execution: (props: { request: ApiApprovalRequest }) => React.ReactNode;
  /** The decision dialog's consequence sentence. */
  consequence: (request: ApiApprovalRequest, decision: Decision) => string;
  /** Poll cadence while the execution can still change, else null. */
  pollInterval: (request: ApiApprovalRequest) => number | null;
}
