// Shared fixtures for the approvals component tests (not imported by app code).
import type {
  ApiApprovalRequest,
  ApiHarnessImplementDisplaySummary,
  ApiHarnessPushDisplaySummary,
} from "@/types/api";

export const W1 = { id: "w1", name: "Kişisel", type: "personal" as const, role: "owner" };
export const W2 = { id: "w2", name: "Acme", type: "company" as const, role: "owner" };

export function approvalRequest(overrides: Partial<ApiApprovalRequest> = {}): ApiApprovalRequest {
  return {
    id: "r1",
    workspace: "w1",
    tool_name: "vault_export",
    status: "pending",
    origin: "human",
    requested_channel: "web",
    requested_by: "u2",
    decided_by: null,
    decided_at: null,
    created_at: "2026-07-09T00:00:00Z",
    updated_at: "2026-07-09T00:00:00Z",
    expires_at: null,
    execution: null,
    ...overrides,
  };
}

export function implementSummary(
  overrides: Partial<ApiHarnessImplementDisplaySummary> = {},
): ApiHarnessImplementDisplaySummary {
  return {
    kind: "harness_implement",
    job_id: "ij1",
    goal: "Add a health endpoint",
    repo_name: "jarvis",
    repo_git_url: "git@example.test:me/jarvis.git",
    branch_name: "harness/p3-ij1",
    agent: "claude_code",
    reviewer_agent: "",
    ...overrides,
  };
}

export function pushSummary(
  overrides: Partial<ApiHarnessPushDisplaySummary> = {},
): ApiHarnessPushDisplaySummary {
  return {
    kind: "harness_push",
    job_id: "ij1",
    branch_name: "harness/p3-ij1",
    produced_commit_sha: "0123456789abcdef0123456789abcdef01234567",
    changed_files: ["a.ts"],
    changed_files_total: 1,
    diff_summary: "Adds a /health endpoint.",
    test_result: { passed: true, command: "make test", exit_code: 0 },
    review: { verdict: "approved", summary: "Looks correct." },
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
