import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalInbox } from "@/components/approvals/ApprovalInbox";
import {
  W1,
  W2,
  approvalRequest,
  implementSummary,
  jsonResponse,
  pushSummary,
} from "@/components/approvals/test-fixtures";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import { setDocumentHidden } from "@/test/visibility";

const { workspacesHolder } = vi.hoisted(() => ({
  workspacesHolder: {
    current: [] as { id: string; name: string; type: "personal" | "company"; role: string }[],
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: workspacesHolder.current[0] ?? null,
    workspaces: workspacesHolder.current,
  }),
}));

function inboxCalls(fetchMock: { mock: { calls: unknown[][] } }): URL[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((input) => input.startsWith("/api/approvals/requests?"))
    .map((input) => new URL(input, "http://localhost"));
}

describe("ApprovalInbox", () => {
  beforeEach(() => {
    workspacesHolder.current = [W1, W2];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("lists every firm's requests on one screen, each labeled with its workspace", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse([
        approvalRequest({ id: "r1", workspace: "w1" }),
        approvalRequest({ id: "r2", workspace: "w2", tool_name: "harness_push" }),
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalInbox />);

    const first = await screen.findByTestId("approval-r1");
    expect(within(first).getByText("Kişisel")).toBeInTheDocument();
    expect(within(screen.getByTestId("approval-r2")).getByText("Acme")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Bekleyenler \(2\)/ })).toBeInTheDocument();
    // One cross-workspace query, not one per workspace.
    expect(inboxCalls(fetchMock).map((url) => url.search)).toEqual(["?scope=all"]);
  });

  it("labels a row of an unknown workspace instead of dropping it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([approvalRequest({ workspace: "w9" })])));
    renderWithClient(<ApprovalInbox />);
    expect(await screen.findByText("Bilinmeyen çalışma alanı")).toBeInTheDocument();
    // No membership role known -> no decision controls.
    expect(screen.queryByRole("button", { name: "Onayla" })).not.toBeInTheDocument();
  });

  it("shows decision controls per workspace role (owner/admin only)", async () => {
    workspacesHolder.current = [W1, { ...W2, role: "member" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({ id: "r1", workspace: "w1" }),
          approvalRequest({ id: "r2", workspace: "w2" }),
        ]),
      ),
    );

    renderWithClient(<ApprovalInbox />);

    const mine = await screen.findByTestId("approval-r1");
    expect(within(mine).getByRole("button", { name: "Onayla" })).toBeInTheDocument();
    const member = screen.getByTestId("approval-r2");
    expect(within(member).queryByRole("button", { name: "Onayla" })).not.toBeInTheDocument();
    expect(within(member).queryByRole("button", { name: "Reddet" })).not.toBeInTheDocument();
  });

  it("sends every filter server-side and can clear them", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalInbox />);
    await screen.findByText("Karar bekleyen bir şey yok.");

    async function pick(filter: string, option: string) {
      await userEvent.click(screen.getByRole("combobox", { name: filter }));
      await userEvent.click(await screen.findByRole("option", { name: option }));
    }
    await pick("Durum filtresi", "Onaylandı");
    await pick("Araç filtresi", "Gönderim onayı (Kapı B)");
    await pick("Çalışma alanı filtresi", "Acme");
    await pick("Kaynak filtresi", "Harness");
    await pick("Kanal filtresi", "Sistem");

    await waitFor(() => {
      const last = inboxCalls(fetchMock).at(-1)!;
      expect(Object.fromEntries(last.searchParams)).toEqual({
        scope: "all",
        status: "approved",
        tool: "harness_push",
        workspace: "w2",
        origin: "harness",
        requested_channel: "system",
      });
    });
    // A decided-status filter shows only the history section.
    expect(screen.queryByRole("heading", { name: /Bekleyenler/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Filtreleri temizle" }));
    await waitFor(() => expect(inboxCalls(fetchMock).at(-1)!.search).toBe("?scope=all"));
  });

  it("hides the workspace filter when there is only one workspace", async () => {
    workspacesHolder.current = [W1];
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));
    renderWithClient(<ApprovalInbox />);
    await screen.findByText("Karar bekleyen bir şey yok.");
    expect(
      screen.queryByRole("combobox", { name: "Çalışma alanı filtresi" }),
    ).not.toBeInTheDocument();
  });

  it("approves only through the decision dialog, which states the consequence", async () => {
    const pending = approvalRequest({
      tool_name: "harness_push",
      display_summary: pushSummary({ branch_name: "harness/p3-abc" }),
    });
    let current = pending;
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input.endsWith("/approve")) {
        current = { ...pending, status: "approved", decided_by: "u1" };
        return jsonResponse(current);
      }
      return jsonResponse([current]);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalInbox />);
    await screen.findByTestId("approval-r1");
    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));

    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("“harness/p3-abc” dalı kayıtlı uzak depoya gönderilir");
    expect(dialog).toHaveTextContent("Gönderim onayı (Kapı B) — Kişisel");
    await cancelDialog();
    expect(calledWith(fetchMock, "POST", "/api/approvals/requests/r1/approve")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));
    await confirmInDialog("Evet, onayla");
    await waitFor(() =>
      expect(
        within(screen.getByTestId("approval-r1")).getByText("Onaylandı", {
          selector: "[data-slot=badge]",
        }),
      ).toBeInTheDocument(),
    );
    expect(calledWith(fetchMock, "POST", "/api/approvals/requests/r1/approve")).toBe(true);
  });

  it("rejects only after confirmation, and reports a refusal inside the dialog", async () => {
    const pending = approvalRequest();
    let current = pending;
    let expired = true;
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input.endsWith("/reject")) {
        if (expired) {
          return jsonResponse({ detail: "This request has expired.", code: "approval.expired" }, 400);
        }
        current = { ...pending, status: "rejected", decided_by: "u1" };
        return jsonResponse(current);
      }
      return jsonResponse([current]);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalInbox />);
    await screen.findByTestId("approval-r1");
    await userEvent.click(screen.getByRole("button", { name: "Reddet" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("Reddedersen dışa aktarma çalışmaz");
    expect(calledWith(fetchMock, "POST", "/api/approvals/requests/r1/reject")).toBe(false);

    await confirmInDialog("Evet, reddet");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Bu onay isteğinin süresi doldu.",
    );
    expect(screen.queryByText("This request has expired.")).not.toBeInTheDocument();

    expired = false;
    await confirmInDialog("Evet, reddet");
    await waitFor(() =>
      expect(within(screen.getByTestId("approval-r1")).getByText("Reddedildi")).toBeInTheDocument(),
    );
  });

  it("shows an expiry countdown for a pending request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-09-27T10:00:00Z"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([approvalRequest({ expires_at: "2026-09-27T12:30:00Z" })])),
    );

    renderWithClient(<ApprovalInbox />);

    expect(await screen.findByText("Karar için kalan süre: 2 sa 30 dk")).toBeInTheDocument();
  });

  it("says a pending request past its expiry can no longer be decided", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([approvalRequest({ expires_at: "2020-01-01T00:00:00Z" })])),
    );
    renderWithClient(<ApprovalInbox />);
    expect(await screen.findByText("Süresi doldu — artık karar verilemez.")).toBeInTheDocument();
  });

  it("links every card to its routed detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([approvalRequest({ id: "r1" })])));
    renderWithClient(<ApprovalInbox />);
    expect(await screen.findByRole("link", { name: "Obsidian vault dışa aktarımı" })).toHaveAttribute(
      "href",
      "/approvals/r1",
    );
  });

  it("navigates with j/k only -- no single-key decision", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse([
        approvalRequest({ id: "r1" }),
        approvalRequest({ id: "r2", tool_name: "harness_push" }),
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ApprovalInbox />);
    await screen.findByTestId("approval-r2");
    const first = within(screen.getByTestId("approval-r1")).getByRole("link");
    const second = within(screen.getByTestId("approval-r2")).getByRole("link");

    await userEvent.keyboard("j");
    expect(first).toHaveFocus();
    await userEvent.keyboard("j");
    expect(second).toHaveFocus();
    await userEvent.keyboard("k");
    expect(first).toHaveFocus();

    await userEvent.keyboard("ayrn");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(calledWith(fetchMock, "POST", "/api/approvals/requests/r1/approve")).toBe(false);
    expect(calledWith(fetchMock, "POST", "/api/approvals/requests/r1/reject")).toBe(false);
  });

  it("renders the run status for an approved request (vault export counters)", async () => {
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

    renderWithClient(<ApprovalInbox />);

    expect(await screen.findByText(/Çalışma durumu: Başarılı/)).toBeInTheDocument();
    expect(screen.getByText(/3 yazıldı, 1 güncellendi, 0 silindi/)).toBeInTheDocument();
  });

  it("maps a harness execution to Turkish job labels and never prints vault counters", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({
            tool_name: "harness_push",
            status: "approved",
            decided_by: "u1",
            execution: {
              status: "failed",
              failure_reason: "push_failed",
              produced_commit_sha: "abc",
              push_result: { pushed: false, error: "remote rejected" },
            },
          }),
        ]),
      ),
    );

    renderWithClient(<ApprovalInbox />);

    expect(
      await screen.findByText("İş durumu: Başarısız — Gönderim (push) başarısız"),
    ).toBeInTheDocument();
    const detailText = screen.getByText("remote rejected");
    expect(detailText.tagName).toBe("PRE");
    expect(detailText.closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText(/yazıldı/)).not.toBeInTheDocument();
    expect(screen.getByText("Bu istek için özet bilgisi yok.")).toBeInTheDocument();
  });

  it("renders Gate A from its display summary with a link to the job", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({
            tool_name: "harness_implement",
            workspace: "w2",
            display_summary: implementSummary(),
          }),
        ]),
      ),
    );

    renderWithClient(<ApprovalInbox />);

    expect(await screen.findByText("Add a health endpoint")).toBeInTheDocument();
    expect(screen.getByText("git@example.test:me/jarvis.git")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Uygulama işini aç" })).toHaveAttribute(
      "href",
      "/harness/implementations/ij1?workspace=w2",
    );
  });

  it("polls silently until an in-flight execution reaches a terminal status", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const execution = {
      status: "pending" as const,
      error_type: "",
      error_message: "",
      written_count: 0,
      updated_count: 0,
      removed_count: 0,
      retry_count: 0,
    };
    const decided = approvalRequest({ status: "approved", decided_by: "u1", execution });
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return jsonResponse([
          calls === 1 ? decided : { ...decided, execution: { ...execution, status: "succeeded" } },
        ]);
      }),
    );

    renderWithClient(<ApprovalInbox />);
    expect(await screen.findByText(/Çalışma durumu: Çalışması bekleniyor/)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    expect(await screen.findByText(/Çalışma durumu: Başarılı/)).toBeInTheDocument();
    const callsAtTerminal = calls;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toBe(callsAtTerminal);
  });

  it("refreshes a pending request slowly, and never while the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return jsonResponse([approvalRequest()]);
      }),
    );

    try {
      renderWithClient(<ApprovalInbox />);
      await screen.findByTestId("approval-r1");

      await vi.advanceTimersByTimeAsync(29_000);
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(calls).toBe(2);

      act(() => setDocumentHidden(true));
      await vi.advanceTimersByTimeAsync(60_000);
      expect(calls).toBe(2);
    } finally {
      setDocumentHidden(false);
    }
  });

  it("shows the load error instead of an empty inbox", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ code: "permission_denied" }, 403)));
    renderWithClient(<ApprovalInbox />);
    expect(await screen.findByText("Onay istekleri yüklenemedi.")).toBeInTheDocument();
    expect(screen.queryByText("Karar bekleyen bir şey yok.")).not.toBeInTheDocument();
  });
});
