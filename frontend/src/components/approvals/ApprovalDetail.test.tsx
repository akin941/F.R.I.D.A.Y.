import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalDetail } from "@/components/approvals/ApprovalDetail";
import {
  W1,
  W2,
  approvalRequest,
  jsonResponse,
  pushSummary,
} from "@/components/approvals/test-fixtures";
import { calledWith, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: W1, workspaces: [W1, W2] }),
}));

const ID = "11111111-1111-1111-1111-111111111111";

describe("ApprovalDetail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads one request of any membership, labeled with its workspace", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        approvalRequest({
          id: ID,
          workspace: "w2",
          tool_name: "harness_push",
          display_summary: pushSummary(),
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalDetail requestId={ID} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Gönderim onayı (Kapı B)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Adds a /health endpoint.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(`/api/approvals/requests/${ID}`, expect.anything());
    expect(screen.getByRole("link", { name: /Onaylara dön/ })).toHaveAttribute(
      "href",
      "/approvals",
    );
  });

  it("decides through the consequence dialog", async () => {
    let current = approvalRequest({ id: ID });
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        current = { ...current, status: "approved", decided_by: "u1" };
      }
      return jsonResponse(current);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalDetail requestId={ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "Onayla" }));
    expect(await findConfirmDialog()).toHaveTextContent("Obsidian vault'a");
    expect(calledWith(fetchMock, "POST", `/api/approvals/requests/${ID}/approve`)).toBe(false);
    await confirmInDialog("Evet, onayla");

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Onayla" })).not.toBeInTheDocument(),
    );
    expect(calledWith(fetchMock, "POST", `/api/approvals/requests/${ID}/approve`)).toBe(true);
  });

  it("shows a load error (e.g. another user's request: 404) with a retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ code: "not_found" }, 404)));
    renderWithClient(<ApprovalDetail requestId={ID} />);
    expect(await screen.findByText("Bu onay isteği yüklenemedi.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
  });
});
