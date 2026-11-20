import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentsView } from "@/components/agents/AgentsView";
import { ApprovalInbox } from "@/components/approvals/ApprovalInbox";
import { ALL_WORKSPACES, queryKeys } from "@/lib/query/keys";
import { renderWithClient } from "@/test/render";

const { holder } = vi.hoisted(() => ({
  holder: {
    current: { id: "w1", name: "Kişisel", type: "personal", role: "owner" } as {
      id: string;
      name: string;
      type: "personal" | "company";
      role: string;
    },
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: holder.current,
    workspaces: [
      { id: "w1", name: "Kişisel", type: "personal", role: "owner" },
      { id: "w2", name: "Acme", type: "company", role: "owner" },
    ],
  }),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

const W1 = { id: "w1", name: "Kişisel", type: "personal" as const, role: "owner" };
const W2 = { id: "w2", name: "Acme", type: "company" as const, role: "owner" };

function agentRow(workspace: string, goal: string) {
  return {
    id: `${workspace}-a`,
    workspace,
    goal,
    status: "completed",
    failure_reason: "",
    created_by: "u1",
    gateway_calls_used: 0,
    consecutive_failures: 0,
    skill_name: "",
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  holder.current = W1;
});

describe("query keys", () => {
  it("start with the workspace id for every workspace-scoped key", () => {
    // The deliberate cross-workspace aggregates live under ALL_WORKSPACES.
    const crossWorkspace = new Set([
      "activitySummary",
      "allApprovals",
      "approvalInbox",
      "approvalDetail",
      // Phase 8A branch 6: owner-wide aggregates the backend labels per workspace.
      "learningDigest",
      "codingSessions",
      "selfSources",
    ]);
    const scoped = Object.entries(queryKeys).filter(([name]) => !crossWorkspace.has(name));
    for (const [name, key] of scoped) {
      const built = (key as (...args: string[]) => readonly unknown[])("ws-x", "id-y");
      expect(built[0], name).toBe("ws-x");
    }
    expect(queryKeys.activitySummary()[0]).toBe(ALL_WORKSPACES);
    expect(queryKeys.allApprovals()[0]).toBe(ALL_WORKSPACES);
    expect(queryKeys.approvalInbox({})[0]).toBe(ALL_WORKSPACES);
    expect(queryKeys.approvalDetail("id-y")[0]).toBe(ALL_WORKSPACES);
    expect(queryKeys.learningDigest()[0]).toBe(ALL_WORKSPACES);
    expect(queryKeys.codingSessions()[0]).toBe(ALL_WORKSPACES);
    expect(queryKeys.selfSources()[0]).toBe(ALL_WORKSPACES);
  });
});

describe("workspace switch never renders another workspace's cached rows", () => {
  it("AgentsView: w1 rows vanish while w2 loads, and w1's cache is only shown for w1", async () => {
    let releaseW2: (() => void) | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("workspace=w1")) {
          return new Response(JSON.stringify([agentRow("w1", "Kişisel hedef")]));
        }
        await new Promise<void>((resolve) => {
          releaseW2 = resolve;
        });
        return new Response(JSON.stringify([agentRow("w2", "Acme hedefi")]));
      }),
    );

    const { rerender } = renderWithClient(<AgentsView />);
    expect(await screen.findByText("Kişisel hedef")).toBeInTheDocument();

    holder.current = W2;
    rerender(<AgentsView />);
    // w2 is still loading: w1's cached rows must NOT stand in for it.
    expect(screen.queryByText("Kişisel hedef")).not.toBeInTheDocument();
    await waitFor(() => expect(releaseW2).not.toBeNull());
    releaseW2!();
    expect(await screen.findByText("Acme hedefi")).toBeInTheDocument();
    expect(screen.queryByText("Kişisel hedef")).not.toBeInTheDocument();

    // Back to w1: its own cache (not w2's rows) renders immediately.
    holder.current = W1;
    rerender(<AgentsView />);
    expect(screen.getByText("Kişisel hedef")).toBeInTheDocument();
    expect(screen.queryByText("Acme hedefi")).not.toBeInTheDocument();
  });

  it("ApprovalInbox: a late response for the previous filter never lands under the new one", async () => {
    let releaseAll: (() => void) | null = null;
    const request = (workspace: string, tool: string) => ({
      id: `${workspace}-r`,
      workspace,
      tool_name: tool,
      status: "pending",
      origin: "human",
      requested_by: "u1",
      requested_channel: "web",
      decided_by: null,
      decided_at: null,
      created_at: "2026-09-27T00:00:00Z",
      updated_at: "2026-09-27T00:00:00Z",
      expires_at: null,
      execution: null,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (!input.includes("workspace=")) {
          await new Promise<void>((resolve) => {
            releaseAll = resolve;
          });
          return new Response(JSON.stringify([request("w1", "w1_tool")]));
        }
        return new Response(JSON.stringify([request("w2", "w2_tool")]));
      }),
    );

    renderWithClient(<ApprovalInbox />);
    await waitFor(() => expect(releaseAll).not.toBeNull());

    await userEvent.click(screen.getByRole("combobox", { name: "Çalışma alanı filtresi" }));
    await userEvent.click(await screen.findByRole("option", { name: "Acme" }));
    expect(await screen.findByText("w2_tool")).toBeInTheDocument();

    releaseAll!();
    // Give the stale unfiltered response a chance to (wrongly) render.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("w1_tool")).not.toBeInTheDocument();
    expect(screen.getByText("w2_tool")).toBeInTheDocument();
  });
});
