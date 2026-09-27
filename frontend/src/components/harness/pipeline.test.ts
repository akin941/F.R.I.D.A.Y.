import { describe, expect, it } from "vitest";
import { pipelineStepStates, stoppedAtStep } from "@/components/harness/pipeline";
import { implJob } from "@/components/harness/test-fixtures";

function states(overrides: Parameters<typeof implJob>[0]) {
  return pipelineStepStates(implJob(overrides)).map((s) => s.state);
}

describe("pipelineStepStates", () => {
  it("marks earlier steps done and the live status current", () => {
    expect(states({ status: "pending_review" })).toEqual([
      "done",
      "done",
      "current",
      "upcoming",
      "upcoming",
      "upcoming",
    ]);
  });

  it("marks every step done for a succeeded job", () => {
    expect(states({ status: "succeeded" })).toEqual(Array(6).fill("done"));
  });

  it("stops a failed job at the step its failure reason belongs to", () => {
    expect(states({ status: "failed", failure_reason: "tests_failed" })).toEqual([
      "done",
      "stopped",
      "upcoming",
      "upcoming",
      "upcoming",
      "upcoming",
    ]);
    expect(states({ status: "failed", failure_reason: "push_failed" })[4]).toBe("stopped");
    expect(states({ status: "failed", failure_reason: "gate_a_rejected" })[0]).toBe("stopped");
  });

  it("infers the stop step for a cancelled job from how far its gates got", () => {
    expect(stoppedAtStep(implJob({ status: "cancelled", failure_reason: "killed" }))).toBe(
      "pending_gate_a",
    );
    expect(
      stoppedAtStep(
        implJob({
          status: "cancelled",
          failure_reason: "killed",
          gate_a: { id: "ga1", status: "approved", decided_at: "t" },
        }),
      ),
    ).toBe("implementing");
    expect(
      stoppedAtStep(
        implJob({
          status: "failed",
          failure_reason: "gate_expired",
          gate_a: { id: "ga1", status: "approved", decided_at: "t" },
          gate_b: { id: "gb1", status: "expired", decided_at: null },
        }),
      ),
    ).toBe("pending_gate_b");
    // A review had started (detail view knows): cancelled during review.
    expect(
      stoppedAtStep(
        implJob({
          status: "cancelled",
          failure_reason: "killed",
          gate_a: { id: "ga1", status: "approved", decided_at: "t" },
        }),
        1,
      ),
    ).toBe("pending_review");
  });
});
