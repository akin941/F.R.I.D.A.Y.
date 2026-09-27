import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImplementationJobDetail } from "@/components/harness/ImplementationJobDetail";
import { HARNESS_FAILURE_REASON_LABELS } from "@/components/harness/labels";
import { implDetail, jsonResponse, review } from "@/components/harness/test-fixtures";
import type { ApiImplementationJobDetail, ImplFailureReason } from "@/types/api";

function stubDetail(body: ApiImplementationJobDetail) {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(body)));
}

function renderDetail() {
  return render(<ImplementationJobDetail jobId="ij1" onBack={() => undefined} />);
}

const FULL = implDetail({
  status: "pending_gate_b",
  gate_a: { id: "ga1", status: "approved", decided_at: "2026-09-27T00:01:00Z" },
  gate_b: { id: "gb1", status: "pending", decided_at: null },
  base_commit_sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  produced_commit_sha: "0123456789abcdef0123456789abcdef01234567",
  changed_files: ["backend/health.py", "backend/tests/test_health.py"],
  diff_summary: "Adds a /health endpoint and its test.",
  test_result: { ran: true, exit_code: 0, passed: true, command: "make test" },
  worktree_expires_at: "2026-09-28T00:00:00Z",
  raw_transcript: "implementer raw output",
  reviews: [review()],
});

describe("ImplementationJobDetail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows a skeleton while loading", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));

    const { container } = renderDetail();

    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();
  });

  it("shows the error with a retry (no lingering skeleton) after a failed load", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return calls === 1 ? jsonResponse({ detail: "no" }, 404) : jsonResponse(implDetail());
      }),
    );

    const { container } = renderDetail();

    expect(await screen.findByText("Bu uygulama işi yüklenemedi.")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="skeleton"]')).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(await screen.findByText("Add a health endpoint")).toBeInTheDocument();
  });

  it("renders the stepper, gates with approval links, branch, tests, files and reviews", async () => {
    stubDetail(FULL);

    renderDetail();

    expect(await screen.findByText("Add a health endpoint")).toBeInTheDocument();
    expect(screen.getByText("Gönderim onayı bekleniyor (Kapı B)")).toBeInTheDocument();
    expect(screen.getByText(/Uygulayıcı: Claude Code · İnceleyici: Codex CLI/)).toBeInTheDocument();

    const stepper = screen.getByRole("list", { name: "İş hattı" });
    const steps = within(stepper).getAllByRole("listitem");
    expect(steps.map((s) => s.getAttribute("data-state"))).toEqual([
      "done",
      "done",
      "done",
      "current",
      "upcoming",
      "upcoming",
    ]);
    expect(steps[3]).toHaveAttribute("aria-current", "step");

    expect(screen.getByRole("link", { name: "Onay isteğini gör" })).toHaveAttribute(
      "href",
      "/approvals?request=ga1",
    );
    expect(screen.getByRole("link", { name: "Onaylar sayfasında karar ver" })).toHaveAttribute(
      "href",
      "/approvals?request=gb1",
    );

    expect(screen.getByText("jarvis/impl-ij1")).toBeInTheDocument();
    expect(screen.getByText("0123456789ab")).toHaveAttribute(
      "title",
      "0123456789abcdef0123456789abcdef01234567",
    );
    expect(screen.getByText(/Testler geçti \(çıkış kodu 0\)/)).toBeInTheDocument();
    expect(screen.getByText("Değişen dosyalar (2)")).toBeInTheDocument();
    expect(screen.getByText("backend/tests/test_health.py")).toBeInTheDocument();
    expect(screen.getByText(/Model tarafından üretilmiş bir özettir/)).toBeInTheDocument();
    expect(screen.getByText("Adds a /health endpoint and its test.")).toBeInTheDocument();
    expect(screen.getByText("Onaylandı", { selector: "span.font-medium" })).toBeInTheDocument();
    expect(screen.getByText("Looks correct.")).toBeInTheDocument();
    expect(screen.getByText("Consider a test for the 404 path")).toBeInTheDocument();
  });

  it("keeps both raw transcripts collapsed behind disclosures with a low-trust warning", async () => {
    stubDetail(FULL);

    renderDetail();
    await screen.findByText("Add a health endpoint");

    expect(screen.queryByText("implementer raw output")).not.toBeInTheDocument();
    expect(screen.queryByText("reviewer raw output")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Uygulayıcının ham çıktısını göster" }));
    const implementerPre = await screen.findByText("implementer raw output");
    expect(implementerPre.tagName).toBe("PRE");
    expect(screen.getByText(/uygulayıcı ajanın \(Claude Code\) kendi ham çıktısıdır/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "İnceleyicinin ham çıktısını göster" }));
    expect((await screen.findByText("reviewer raw output")).tagName).toBe("PRE");
    expect(screen.getByText(/inceleyici ajanın \(Codex CLI\) kendi ham çıktısıdır/)).toBeInTheDocument();
  });

  it("shows gates that don't exist yet and no test result yet", async () => {
    stubDetail(implDetail({ gate_a: null }));

    renderDetail();

    await screen.findByText("Add a health endpoint");
    expect(screen.getAllByText("Henüz oluşturulmadı")).toHaveLength(2);
    expect(screen.getByText("Henüz test sonucu yok.")).toBeInTheDocument();
  });

  it("marks the step a failed job stopped at and shows a failed test run", async () => {
    stubDetail(
      implDetail({
        status: "failed",
        failure_reason: "tests_failed",
        gate_a: { id: "ga1", status: "approved", decided_at: "t" },
        test_result: { ran: true, exit_code: 2, passed: false, command: "make test" },
      }),
    );

    renderDetail();

    expect(await screen.findByText("Başarısız — Testler başarısız")).toBeInTheDocument();
    expect(screen.getByText(/Testler başarısız \(çıkış kodu 2\)/)).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "İş hattı" })).getAllByRole("listitem");
    expect(steps[1]).toHaveAttribute("data-state", "stopped");
  });

  it.each(Object.keys(HARNESS_FAILURE_REASON_LABELS) as Exclude<ImplFailureReason, "">[])(
    "renders a Turkish label for failure reason %s",
    async (reason) => {
      stubDetail(implDetail({ status: "failed", failure_reason: reason }));

      renderDetail();

      expect(
        await screen.findByText(`Başarısız — ${HARNESS_FAILURE_REASON_LABELS[reason]}`),
      ).toBeInTheDocument();
    },
  );

  it("covers all 16 non-empty failure reasons from the contract", () => {
    expect(Object.keys(HARNESS_FAILURE_REASON_LABELS)).toHaveLength(16);
  });

  it("polls silently while non-terminal and stops once terminal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return jsonResponse(
          calls < 3 ? implDetail({ status: "implementing" }) : implDetail({ status: "succeeded" }),
        );
      }),
    );

    renderDetail();
    expect(await screen.findByText("Uygulanıyor")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await screen.findByText("Tamamlandı", { selector: "p" })).toBeInTheDocument();
    const callsAtTerminal = calls;

    await vi.advanceTimersByTimeAsync(9000);
    expect(calls).toBe(callsAtTerminal);
  });

  it("asks for confirmation before cancelling, and can back out", async () => {
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () =>
      jsonResponse(implDetail()),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();
    await userEvent.click(await screen.findByRole("button", { name: "İşi iptal et" }));

    expect(screen.getByText(/Bu uygulama işi iptal edilsin mi\?/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Vazgeç" }));

    expect(screen.queryByText(/Bu uygulama işi iptal edilsin mi\?/)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("cancels after confirmation and shows a pending cancel for a running phase", async () => {
    let current = implDetail({ status: "implementing" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/harness/implementation-jobs/ij1/cancel") {
          current = { ...current, cancel_requested: true };
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    renderDetail();
    await userEvent.click(await screen.findByRole("button", { name: "İşi iptal et" }));
    await userEvent.click(screen.getByRole("button", { name: "Evet, iptal et" }));

    expect(
      await screen.findByText("İptal istendi — çalışan adım durduruluyor…"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "İşi iptal et" })).not.toBeInTheDocument(),
    );
  });

  it("shows the backend error when cancelling fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) =>
        init?.method === "POST"
          ? jsonResponse({ detail: "Job already finished." }, 400)
          : jsonResponse(implDetail()),
      ),
    );

    renderDetail();
    await userEvent.click(await screen.findByRole("button", { name: "İşi iptal et" }));
    await userEvent.click(screen.getByRole("button", { name: "Evet, iptal et" }));

    expect(await screen.findByText("Job already finished.")).toBeInTheDocument();
  });

  it("never offers cancel for a terminal job", async () => {
    stubDetail(implDetail({ status: "cancelled", failure_reason: "killed" }));

    renderDetail();
    await screen.findByText("İptal edildi — Durduruldu");

    expect(screen.queryByRole("button", { name: "İşi iptal et" })).not.toBeInTheDocument();
  });

  it("shows a failed push's error", async () => {
    stubDetail(
      implDetail({
        status: "failed",
        failure_reason: "push_failed",
        push_result: { pushed: false, error: "remote rejected" },
      }),
    );

    renderDetail();

    expect(await screen.findByText("Gönderim başarısız: remote rejected")).toBeInTheDocument();
  });
});
