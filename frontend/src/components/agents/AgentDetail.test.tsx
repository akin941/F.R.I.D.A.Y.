import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentDetail } from "@/components/agents/AgentDetail";
import type { ApiAgentDetail, ApiAgentStep } from "@/types/api";

const { workspace } = vi.hoisted(() => ({
  workspace: { id: "w1", name: "Alice", type: "personal" as const, role: "owner" },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspace }),
}));
import { renderWithClient } from "@/test/render";

function step(overrides: Partial<ApiAgentStep> = {}): ApiAgentStep {
  return {
    id: "s1",
    agent: "a1",
    order: 0,
    kind: "read",
    description: "Read something",
    status: "succeeded",
    payload: {},
    result: {},
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:00:00Z",
    ...overrides,
  };
}

function detail(overrides: Partial<ApiAgentDetail> = {}): ApiAgentDetail {
  return {
    id: "a1",
    workspace: "w1",
    goal: "Summarize my notes",
    status: "running",
    failure_reason: "",
    created_by: "u1",
    gateway_calls_used: 2,
    consecutive_failures: 0,
    skill_name: "",
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:00:00Z",
    steps: [step()],
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("AgentDetail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the goal, status, and steps", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail())),
    );

    renderWithClient(<AgentDetail agentId="a1" />);

    expect(await screen.findByText("Summarize my notes")).toBeInTheDocument();
    expect(screen.getByText("Read something")).toBeInTheDocument();
  });

  it("shows which skill produced the plan when skill_name is set", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ skill_name: "weekly_export" }))),
    );

    renderWithClient(<AgentDetail agentId="a1" />);

    expect(await screen.findByText("Şu beceriyle çalıştı: weekly_export")).toBeInTheDocument();
  });

  it("shows no skill line for a freeform (non-skill) plan", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ skill_name: "" }))),
    );

    renderWithClient(<AgentDetail agentId="a1" />);
    await screen.findByText("Summarize my notes");

    expect(screen.queryByText(/Ran via skill/)).not.toBeInTheDocument();
  });

  it("cancels a running agent", async () => {
    let current = detail({ status: "running" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/agents/a1/cancel") {
          current = { ...current, status: "cancelled" };
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    renderWithClient(<AgentDetail agentId="a1" />);
    await screen.findByText("Summarize my notes");
    await userEvent.click(screen.getByRole("button", { name: "Ajanı iptal et" }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Ajanı iptal et" })).not.toBeInTheDocument();
    });
  });

  it("never shows a cancel button for a terminal agent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ status: "completed" }))),
    );

    renderWithClient(<AgentDetail agentId="a1" />);
    await screen.findByText("Summarize my notes");

    expect(screen.queryByRole("button", { name: "Ajanı iptal et" })).not.toBeInTheDocument();
  });

  it("links a blocked_on_approval step to /approvals", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          detail({
            status: "blocked_on_approval",
            steps: [
              step({
                kind: "tool_request",
                description: "Export the vault",
                status: "blocked_on_approval",
              }),
            ],
          }),
        ),
      ),
    );

    renderWithClient(<AgentDetail agentId="a1" />);

    const link = await screen.findByRole("link", { name: "Onaylar sayfasında incele" });
    expect(link).toHaveAttribute("href", "/approvals");
  });

  it("confirms a blocked task_draft step", async () => {
    const blocked = detail({
      status: "blocked_on_confirmation",
      steps: [
        step({
          kind: "task_draft",
          description: "Draft a task",
          status: "blocked_on_confirmation",
        }),
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/agents/steps/s1/confirm") {
          return jsonResponse({ id: "t1", title: "Buy milk" }, 201);
        }
        return jsonResponse(blocked);
      }),
    );

    renderWithClient(<AgentDetail agentId="a1" />);
    await screen.findByText("Draft a task");
    await userEvent.click(screen.getByRole("button", { name: "Görevi onayla" }));

    await waitFor(() => {
      expect(
        vi
          .mocked(fetch)
          .mock.calls.some(([input]) => input === "/api/agents/steps/s1/confirm"),
      ).toBe(true);
    });
  });

  it("discards a blocked task_draft step", async () => {
    const blocked = detail({
      status: "blocked_on_confirmation",
      steps: [
        step({
          kind: "task_draft",
          description: "Draft a task",
          status: "blocked_on_confirmation",
        }),
      ],
    });
    const discarded = { ...blocked.steps[0], status: "skipped" as const };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/agents/steps/s1/discard") {
          return jsonResponse(discarded);
        }
        return jsonResponse(blocked);
      }),
    );

    renderWithClient(<AgentDetail agentId="a1" />);
    await screen.findByText("Draft a task");
    await userEvent.click(screen.getByRole("button", { name: "Vazgeç" }));

    await waitFor(() => {
      expect(
        vi
          .mocked(fetch)
          .mock.calls.some(([input]) => input === "/api/agents/steps/s1/discard"),
      ).toBe(true);
    });
  });
});
