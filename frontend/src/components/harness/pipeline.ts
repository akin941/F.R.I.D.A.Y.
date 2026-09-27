import { IMPL_PIPELINE_STEPS } from "@/components/harness/labels";
import type { ApiImplementationJob, ImplFailureReason, ImplStatus } from "@/types/api";

export type PipelineStepState = "done" | "current" | "stopped" | "upcoming";

// Where in the pipeline each failure reason stops a job. `gate_expired`
// and `killed` depend on which gate/phase was live, so they're resolved
// from the gates below instead.
const FAILURE_STAGE: Partial<Record<ImplFailureReason, ImplStatus>> = {
  gate_a_rejected: "pending_gate_a",
  timeout: "implementing",
  runner_error: "implementing",
  budget_exceeded: "implementing",
  test_command_not_configured: "implementing",
  test_result_unavailable: "implementing",
  tests_failed: "implementing",
  no_changes: "implementing",
  secret_leak: "implementing",
  review_changes_requested: "pending_review",
  review_unavailable: "pending_review",
  gate_b_rejected: "pending_gate_b",
  worktree_expired: "pending_gate_b",
  push_failed: "pushing",
};

/**
 * The pipeline step a failed/cancelled job stopped at: the failure reason
 * when it pins one down, otherwise inferred from how far the gates got.
 */
export function stoppedAtStep(
  job: Pick<ApiImplementationJob, "failure_reason" | "gate_a" | "gate_b">,
): ImplStatus {
  const fromReason = FAILURE_STAGE[job.failure_reason];
  if (fromReason) return fromReason;
  if (job.gate_b) return job.gate_b.status === "approved" ? "pushing" : "pending_gate_b";
  if (job.gate_a?.status === "approved") return "implementing";
  return "pending_gate_a";
}

/** Per-step state for the stepper over the linear happy path. */
export function pipelineStepStates(
  job: Pick<ApiImplementationJob, "status" | "failure_reason" | "gate_a" | "gate_b">,
): { step: ImplStatus; state: PipelineStepState }[] {
  const terminalStop = job.status === "failed" || job.status === "cancelled";
  const anchor = terminalStop ? stoppedAtStep(job) : job.status;
  const anchorIndex = IMPL_PIPELINE_STEPS.indexOf(anchor);
  return IMPL_PIPELINE_STEPS.map((step, index) => {
    let state: PipelineStepState;
    if (job.status === "succeeded") state = "done";
    else if (index < anchorIndex) state = "done";
    else if (index === anchorIndex) state = terminalStop ? "stopped" : "current";
    else state = "upcoming";
    return { step, state };
  });
}
