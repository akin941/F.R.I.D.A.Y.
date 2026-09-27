import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentsView } from "@/components/agents/AgentsView";
import type { ApiAgent } from "@/types/api";
import { renderWithClient } from "@/test/render";

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

    renderWithClient(<AgentsView />);

    expect(await screen.findByText("Summarize my notes")).toBeInTheDocument();
  });

  it("shows an empty state when there are no agents", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    renderWithClient(<AgentsView />);

    expect(await screen.findByText("Henüz ajan yok.")).toBeInTheDocument();
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

    renderWithClient(<AgentsView />);
    await screen.findByText("Henüz ajan yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni ajan" }));
    await userEvent.type(screen.getByLabelText("Hedef"), "Draft a report");
    await userEvent.click(screen.getByRole("button", { name: "Ajanı başlat" }));

    expect(await screen.findByText("Draft a report")).toBeInTheDocument();
  });

  it("links each agent to its routed detail page, carrying the workspace", async () => {
    const row = agent({ id: "a1", goal: "Summarize my notes" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    renderWithClient(<AgentsView />);

    const link = await screen.findByRole("link", { name: /Summarize my notes/ });
    expect(link).toHaveAttribute("href", "/agents/a1?workspace=w1");
  });

  it("polls while an agent is in flight and stops once all are terminal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return jsonResponse([agent({ status: calls < 3 ? "running" : "completed" })]);
      }),
    );

    renderWithClient(<AgentsView />);
    expect(await screen.findByText("Çalışıyor")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await screen.findByText("Tamamlandı")).toBeInTheDocument();
    const callsAtTerminal = calls;
    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls).toBe(callsAtTerminal);
    vi.useRealTimers();
  });

  it("renders a Turkish label for the failure reason, never the raw value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([agent({ status: "failed", failure_reason: "gateway_budget_exceeded" })]),
      ),
    );

    renderWithClient(<AgentsView />);

    expect(await screen.findByText(/Model çağrı bütçesi aşıldı/)).toBeInTheDocument();
    expect(screen.queryByText(/gateway_budget_exceeded/)).not.toBeInTheDocument();
  });
});
