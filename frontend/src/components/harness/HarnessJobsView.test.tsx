import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HarnessJobsView } from "@/components/harness/HarnessJobsView";
import type { ApiHarnessJob } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function job(overrides: Partial<ApiHarnessJob> = {}): ApiHarnessJob {
  return {
    id: "j1",
    workspace: "w1",
    repo: "r1",
    goal: "Find the bug",
    agent: "claude_code",
    sensitivity: "normal",
    status: "succeeded",
    failure_reason: "",
    created_by: "u1",
    started_at: null,
    elapsed_seconds: 0,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("HarnessJobsView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders the job list", async () => {
    const row = job({ goal: "Find the bug" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    render(<HarnessJobsView />);

    expect(await screen.findByText("Find the bug")).toBeInTheDocument();
  });

  it("shows an empty state when there are no jobs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    render(<HarnessJobsView />);

    expect(await screen.findByText("Henüz harness işi yok.")).toBeInTheDocument();
  });

  it("starts a new job and shows it in the list", async () => {
    const created = job({ id: "new1", goal: "Audit the auth module", status: "pending" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/harness/jobs") {
          return jsonResponse(created, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni iş" }));
    await userEvent.type(
      screen.getByLabelText("Depo kimliği"),
      "11111111-1111-1111-1111-111111111111",
    );
    await userEvent.type(screen.getByLabelText("Hedef"), "Audit the auth module");
    await userEvent.click(screen.getByRole("button", { name: "İşi başlat" }));

    expect(await screen.findByText("Audit the auth module")).toBeInTheDocument();
  });

  it("opens a job's detail view on click", async () => {
    const row = job({ id: "j1", goal: "Find the bug" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input === "/api/harness/jobs/j1") {
          return jsonResponse({ ...row, briefing: {} });
        }
        return jsonResponse([row]);
      }),
    );

    render(<HarnessJobsView />);
    await userEvent.click(await screen.findByText("Find the bug"));

    expect(await screen.findByRole("button", { name: /Harness işlerine dön/ })).toBeInTheDocument();
  });
});
