import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalView } from "@/components/approvals/ApprovalView";
import type { ApiApprovalRequest } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: {
    current: { id: "w1", name: "Alice", type: "personal", role: "owner" },
  } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: workspaceHolder.current,
    user: { id: "u1", email: "alice@test.dev", name: "Alice", is_agent: false },
  }),
}));

function approvalRequest(overrides: Partial<ApiApprovalRequest> = {}): ApiApprovalRequest {
  return {
    id: "r1",
    workspace: "w1",
    tool_name: "vault_export",
    status: "pending",
    requested_by: "u2",
    decided_by: null,
    decided_at: null,
    created_at: "2026-07-09T00:00:00Z",
    updated_at: "2026-07-09T00:00:00Z",
    execution: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("ApprovalView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("loads and renders a pending request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([approvalRequest()])),
    );

    render(<ApprovalView />);

    expect(await screen.findByText("vault_export")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Onayla" })).toBeInTheDocument();
  });

  it("shows the decide controls for a request the viewer made themselves (ADR-008)", async () => {
    // Single-owner decision model: the owner's explicit, separate decision
    // IS the gate -- own requests are decidable, no second human exists.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([approvalRequest({ requested_by: "u1" })])),
    );

    render(<ApprovalView />);

    expect(await screen.findByText("vault_export")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Onayla" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reddet" })).toBeInTheDocument();
  });

  it("hides the decide controls from a member role, even for someone else's request", async () => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "member" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([approvalRequest({ requested_by: "u2" })])),
    );

    render(<ApprovalView />);

    expect(await screen.findByText("vault_export")).toBeInTheDocument();
    // Only owner/admin roles may decide server-side (brief §4) -- a member
    // viewing someone else's request must not see controls that would
    // always fail with a 403 (Codex review, branch 4).
    expect(screen.queryByRole("button", { name: "Onayla" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reddet" })).not.toBeInTheDocument();
  });

  it("approves a pending request", async () => {
    const pending = approvalRequest();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/approve")) {
          return jsonResponse({ ...pending, status: "approved", decided_by: "u1" });
        }
        return jsonResponse([pending]);
      }),
    );

    render(<ApprovalView />);
    await screen.findByText("vault_export");
    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));

    await waitFor(() => {
      expect(screen.getByText("Onaylandı")).toBeInTheDocument();
    });
  });

  it("rejects a pending request", async () => {
    const pending = approvalRequest();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/reject")) {
          return jsonResponse({ ...pending, status: "rejected", decided_by: "u1" });
        }
        return jsonResponse([pending]);
      }),
    );

    render(<ApprovalView />);
    await screen.findByText("vault_export");
    await userEvent.click(screen.getByRole("button", { name: "Reddet" }));

    await waitFor(() => {
      expect(screen.getByText("Reddedildi")).toBeInTheDocument();
    });
  });

  it("shows the run-status indicator for an approved request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({
            status: "approved",
            decided_by: "u1",
            decided_at: "2026-07-09T01:00:00Z",
            execution: {
              status: "succeeded",
              error_type: "",
              error_message: "",
              written_count: 3,
              updated_count: 1,
              removed_count: 0,
              retry_count: 0,
            },
          }),
        ]),
      ),
    );

    render(<ApprovalView />);

    expect(await screen.findByText(/Çalışma durumu: Başarılı/)).toBeInTheDocument();
    expect(screen.getByText(/3 yazıldı, 1 güncellendi, 0 silindi/)).toBeInTheDocument();
  });

  it("polls silently until an in-flight execution reaches a terminal status", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pendingExecution = {
      status: "pending" as const,
      error_type: "",
      error_message: "",
      written_count: 0,
      updated_count: 0,
      removed_count: 0,
      retry_count: 0,
    };
    const decided = approvalRequest({
      status: "approved",
      decided_by: "u1",
      execution: pendingExecution,
    });
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        if (callCount === 1) return jsonResponse([decided]);
        return jsonResponse([
          { ...decided, execution: { ...pendingExecution, status: "succeeded", written_count: 2 } },
        ]);
      }),
    );

    render(<ApprovalView />);
    expect(await screen.findByText(/Çalışma durumu: Çalışması bekleniyor/)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);

    expect(await screen.findByText(/Çalışma durumu: Başarılı/)).toBeInTheDocument();
    // Polling is silent -- no skeleton flash while an in-flight execution refreshes.
    expect(screen.queryByText("Karar bekleyen bir şey yok.")).toBeInTheDocument();
  });

  it("filters the decided list by status", async () => {
    const approved = approvalRequest({
      id: "approved1",
      status: "approved",
      decided_by: "u1",
    });
    const rejected = approvalRequest({ id: "rejected1", status: "rejected", decided_by: "u1" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([approved, rejected])),
    );

    render(<ApprovalView />);
    await screen.findByText("Karara bağlananlar");
    expect(screen.getAllByText("vault_export")).toHaveLength(2);

    await userEvent.click(screen.getByRole("combobox", { name: "Durum filtresi" }));
    await userEvent.click(await screen.findByRole("option", { name: "Reddedildi" }));

    await waitFor(() => {
      expect(screen.getAllByText("vault_export")).toHaveLength(1);
    });
  });
});
