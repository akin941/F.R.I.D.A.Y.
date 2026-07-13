import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentsView } from "@/components/agents/AgentsView";
import type { ApiAgent } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function agent(overrides: Partial<ApiAgent> = {}): ApiAgent {
  return {
    id: "a1",
    workspace: "w1",
    goal: "Summarize my notes",
    status: "completed",
    failure_reason: "",
    created_by: "u1",
    gateway_calls_used: 2,
    consecutive_failures: 0,
    skill_name: "",
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("AgentsView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders the agent list", async () => {
    const row = agent({ goal: "Summarize my notes" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    render(<AgentsView />);

    expect(await screen.findByText("Summarize my notes")).toBeInTheDocument();
  });

  it("shows an empty state when there are no agents", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    render(<AgentsView />);

    expect(await screen.findByText("No agents yet.")).toBeInTheDocument();
  });

  it("starts a new agent and shows it in the list", async () => {
    const created = agent({ id: "new1", goal: "Draft a report", status: "running" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/agents") {
          return jsonResponse(created, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<AgentsView />);
    await screen.findByText("No agents yet.");

    await userEvent.click(screen.getByRole("button", { name: "New agent" }));
    await userEvent.type(screen.getByLabelText("Goal"), "Draft a report");
    await userEvent.click(screen.getByRole("button", { name: "Start agent" }));

    expect(await screen.findByText("Draft a report")).toBeInTheDocument();
  });

  it("opens an agent's detail view on click", async () => {
    const row = agent({ id: "a1", goal: "Summarize my notes" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input === "/api/agents/a1") {
          return jsonResponse({ ...row, steps: [] });
        }
        return jsonResponse([row]);
      }),
    );

    render(<AgentsView />);
    await userEvent.click(await screen.findByText("Summarize my notes"));

    expect(await screen.findByRole("button", { name: /Back to agents/ })).toBeInTheDocument();
  });
});
