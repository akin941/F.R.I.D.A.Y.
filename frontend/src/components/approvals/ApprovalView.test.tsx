import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalView } from "@/components/approvals/ApprovalView";
import type {
  ApiApprovalRequest,
  ApiHarnessImplementDisplaySummary,
  ApiHarnessPushDisplaySummary,
} from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: {
    current: { id: "w1", name: "Alice", type: "personal", role: "owner" },
  } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

const { searchParamsHolder } = vi.hoisted(() => ({
  searchParamsHolder: { current: new URLSearchParams() },
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParamsHolder.current,
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
    searchParamsHolder.current = new URLSearchParams();
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
    await userEvent.click(screen.getByRole("button", { name: "Evet, onayla" }));

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
    await userEvent.click(screen.getByRole("button", { name: "Evet, reddet" }));

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

  it("asks for confirmation before deciding, and can back out without a request", async () => {
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => jsonResponse([approvalRequest()]),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ApprovalView />);
    await screen.findByText("vault_export");
    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));

    expect(screen.getByText("Onaylarsan bu istek çalıştırılır.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Vazgeç" }));

    expect(screen.getByRole("button", { name: "Onayla" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("renders a Gate A (harness_implement) request from its display summary", async () => {
    const summary: ApiHarnessImplementDisplaySummary = {
      kind: "harness_implement",
      job_id: "ij1",
      goal: "Add a health endpoint",
      repo_name: "jarvis",
      repo_git_url: "git@example.test:me/jarvis.git",
      branch_name: "jarvis/impl-ij1",
      agent: "claude_code",
      reviewer_agent: "",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({ tool_name: "harness_implement", display_summary: summary }),
        ]),
      ),
    );

    render(<ApprovalView />);

    expect(await screen.findByText("Uygulama planı onayı (Kapı A)")).toBeInTheDocument();
    expect(screen.getByText("Add a health endpoint")).toBeInTheDocument();
    expect(screen.getByText("jarvis")).toBeInTheDocument();
    // The repo name is only a label -- the real clone URL is shown too.
    expect(screen.getByText("git@example.test:me/jarvis.git")).toBeInTheDocument();
    expect(screen.getByText("jarvis/impl-ij1")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    // No selectable reviewer ("") renders as a dash, never blank.
    expect(screen.getByText("—")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));
    expect(screen.getByText(/ayrı bir gönderim onayı \(Kapı B\) olmadan/)).toBeInTheDocument();
  });

  it("renders a Gate B (harness_push) request: files (first 20 + more), diff summary, tests, review", async () => {
    const files = Array.from({ length: 25 }, (_, i) => `src/file${i}.ts`);
    const summary: ApiHarnessPushDisplaySummary = {
      kind: "harness_push",
      job_id: "ij1",
      branch_name: "jarvis/impl-ij1",
      produced_commit_sha: "0123456789abcdef0123456789abcdef01234567",
      changed_files: files,
      changed_files_total: 30,
      diff_summary: "Adds a /health endpoint.",
      test_result: { passed: true, command: "make test", exit_code: 0 },
      review: { verdict: "approved", summary: "Looks correct." },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([approvalRequest({ tool_name: "harness_push", display_summary: summary })]),
      ),
    );

    render(<ApprovalView />);

    expect(await screen.findByText("Gönderim onayı (Kapı B)")).toBeInTheDocument();
    expect(screen.getByText("Değişen dosyalar (30)")).toBeInTheDocument();
    expect(screen.getByText("src/file19.ts")).toBeInTheDocument();
    expect(screen.queryByText("src/file20.ts")).not.toBeInTheDocument();
    expect(screen.getByText("+10 daha")).toBeInTheDocument();
    expect(screen.getByText(/Model tarafından üretilmiş özet/)).toBeInTheDocument();
    expect(screen.getByText("Adds a /health endpoint.")).toBeInTheDocument();
    expect(screen.getByText(/Geçti \(çıkış kodu 0\)/)).toBeInTheDocument();
    expect(screen.getByText("Onaylandı", { selector: "span.font-medium" })).toBeInTheDocument();
    expect(screen.getByText("Looks correct.")).toBeInTheDocument();
    expect(screen.getByText("Jarvis'in kendi özeti (model üretimi).")).toBeInTheDocument();
    expect(screen.getByText("0123456789ab")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));
    expect(screen.getByText(/Gönderim geri alınamaz/)).toBeInTheDocument();
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
          approvalRequest({
            id: "r2",
            tool_name: "harness_implement",
            status: "approved",
            decided_by: "u1",
            execution: { status: "succeeded", failure_reason: "", branch_name: "b" },
          }),
        ]),
      ),
    );

    render(<ApprovalView />);

    expect(
      await screen.findByText("İş durumu: Başarısız — Gönderim (push) başarısız"),
    ).toBeInTheDocument();
    // The raw git error sits in a collapsed "Teknik ayrıntı" disclosure as
    // preformatted text, not inline prose.
    expect(screen.getByText("Gönderim başarısız.")).toBeInTheDocument();
    const detailText = screen.getByText("remote rejected");
    expect(detailText.tagName).toBe("PRE");
    expect(detailText.closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText(/Gönderim hatası: remote rejected/)).not.toBeInTheDocument();
    expect(screen.getByText("İş durumu: Tamamlandı")).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    expect(screen.queryByText(/yazıldı/)).not.toBeInTheDocument();
    // No display_summary on these rows -> an explicit "no summary" note.
    expect(screen.getAllByText("Bu istek için özet bilgisi yok.")).toHaveLength(2);
  });

  function harnessExecution(status: string) {
    return approvalRequest({
      tool_name: "harness_implement",
      status: "approved",
      decided_by: "u1",
      execution: {
        status: status as "implementing",
        failure_reason: "",
        branch_name: "b",
      },
    });
  }

  it("polls fast while a harness job is working, then stops once terminal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        // `implementing` is not a vault-export status -- the old
        // pending/running check would never have polled on it.
        return jsonResponse([harnessExecution(callCount < 3 ? "implementing" : "succeeded")]);
      }),
    );

    render(<ApprovalView />);
    expect(await screen.findByText("İş durumu: Uygulanıyor")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await screen.findByText("İş durumu: Tamamlandı")).toBeInTheDocument();
    const callsAtTerminal = callCount;

    await vi.advanceTimersByTimeAsync(60_000);
    expect(callCount).toBe(callsAtTerminal);
  });

  it("polls only slowly while a harness job just waits at a gate for the owner", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        return jsonResponse([harnessExecution("pending_gate_b")]);
      }),
    );

    render(<ApprovalView />);
    expect(
      await screen.findByText("İş durumu: Gönderim onayı bekleniyor (Kapı B)"),
    ).toBeInTheDocument();
    expect(callCount).toBe(1);

    // No 3s fast polling while nothing can change without a human...
    await vi.advanceTimersByTimeAsync(29_000);
    expect(callCount).toBe(1);
    // ...but a slow refresh still picks up a decision made elsewhere.
    await vi.advanceTimersByTimeAsync(1_000);
    expect(callCount).toBe(2);
  });

  it("pauses polling while the tab is hidden and refreshes on return", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        return jsonResponse([harnessExecution("implementing")]);
      }),
    );
    const setHidden = (hidden: boolean) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event("visibilitychange"));
    };

    try {
      render(<ApprovalView />);
      await screen.findByText("İş durumu: Uygulanıyor");

      act(() => setHidden(true));
      await vi.advanceTimersByTimeAsync(30_000);
      expect(callCount).toBe(1);

      act(() => setHidden(false));
      await waitFor(() => expect(callCount).toBe(2));
    } finally {
      setHidden(false);
    }
  });

  it("scrolls to and highlights the card named by ?request=<id>", async () => {
    searchParamsHolder.current = new URLSearchParams("request=r2");
    const scrollSpy = vi.fn();
    const original = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = scrollSpy;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({ id: "r1" }),
          approvalRequest({ id: "r2", status: "approved", decided_by: "u1" }),
        ]),
      ),
    );

    try {
      const { container } = render(<ApprovalView />);
      await screen.findAllByText("vault_export");

      const highlighted = container.querySelector('[data-highlighted="true"]');
      expect(highlighted).toHaveAttribute("id", "approval-r2");
      expect(container.querySelectorAll('[data-highlighted="true"]')).toHaveLength(1);
      await waitFor(() => expect(scrollSpy).toHaveBeenCalledTimes(1));
      expect(highlighted).toHaveFocus();
    } finally {
      window.HTMLElement.prototype.scrollIntoView = original;
    }
  });

  it("says so when the requested card isn't in this workspace", async () => {
    searchParamsHolder.current = new URLSearchParams("request=missing");
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([approvalRequest()])));

    render(<ApprovalView />);

    expect(
      await screen.findByText(/İstenen onay isteği bu çalışma alanında bulunamadı/),
    ).toBeInTheDocument();
  });

  it("moves focus into the confirmation and back to the trigger on Vazgeç", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([approvalRequest()])));

    render(<ApprovalView />);
    await screen.findByText("vault_export");

    await userEvent.click(screen.getByRole("button", { name: "Reddet" }));
    expect(screen.getByRole("button", { name: "Evet, reddet" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Vazgeç" }));
    expect(screen.getByRole("button", { name: "Reddet" })).toHaveFocus();

    await userEvent.click(screen.getByRole("button", { name: "Onayla" }));
    expect(screen.getByRole("button", { name: "Evet, onayla" })).toHaveFocus();
    await userEvent.keyboard("{Shift>}{Tab}{/Shift}{Enter}");
    expect(screen.getByRole("button", { name: "Onayla" })).toHaveFocus();
  });

  it("does not crash when a harness request's display_summary is null (non-curator)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          approvalRequest({ tool_name: "harness_push", display_summary: null, execution: null }),
          approvalRequest({ id: "r2", tool_name: "harness_implement", display_summary: null }),
        ]),
      ),
    );
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "member" };

    render(<ApprovalView />);

    expect(await screen.findByText("Gönderim onayı (Kapı B)")).toBeInTheDocument();
    expect(screen.getAllByText("Bu istek için özet bilgisi yok.")).toHaveLength(2);
  });
});
