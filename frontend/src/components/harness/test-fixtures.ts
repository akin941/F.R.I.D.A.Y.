// Shared fixtures for the harness component tests (not imported by app code).
import type {
  ApiHarnessRepo,
  ApiImplementationJob,
  ApiImplementationJobDetail,
  ApiImplementationReview,
} from "@/types/api";

export const REPO_ID = "11111111-1111-1111-1111-111111111111";
export const REPO_2_ID = "22222222-2222-2222-2222-222222222222";

export function repo(overrides: Partial<ApiHarnessRepo> = {}): ApiHarnessRepo {
  return {
    id: REPO_ID,
    workspace: "w1",
    name: "jarvis",
    git_url: "git@example.test:me/jarvis.git",
    default_branch: "main",
    runner: "docker",
    enabled: true,
    sensitive: false,
    test_command: "make test",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

export function implJob(overrides: Partial<ApiImplementationJob> = {}): ApiImplementationJob {
  return {
    id: "ij1",
    workspace: "w1",
    repo: REPO_ID,
    repo_name: "jarvis",
    discovery_job: null,
    goal: "Add a health endpoint",
    agent: "claude_code",
    agent_routing_reason: "policy:default",
    reviewer_agent: "codex_cli",
    status: "pending_gate_a",
    failure_reason: "",
    cancel_requested: false,
    branch_name: "jarvis/impl-ij1",
    created_by: "u1",
    started_at: null,
    elapsed_seconds: null,
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    gate_a: { id: "ga1", status: "pending", decided_at: null },
    gate_b: null,
    ...overrides,
  };
}

export function review(overrides: Partial<ApiImplementationReview> = {}): ApiImplementationReview {
  return {
    id: "rv1",
    agent: "codex_cli",
    agent_routing_reason: "policy:cross_vendor",
    status: "succeeded",
    verdict: "approved",
    summary: "Looks correct.",
    findings: ["Consider a test for the 404 path"],
    raw_transcript: "reviewer raw output",
    elapsed_seconds: 42,
    created_at: "2026-09-27T00:10:00Z",
    updated_at: "2026-09-27T00:10:00Z",
    ...overrides,
  };
}

export function implDetail(
  overrides: Partial<ApiImplementationJobDetail> = {},
): ApiImplementationJobDetail {
  return {
    ...implJob(),
    base_commit_sha: "",
    produced_commit_sha: "",
    changed_files: [],
    diff_summary: "",
    test_result: {},
    push_result: {},
    worktree_expires_at: null,
    raw_transcript: "",
    reviews: [],
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
