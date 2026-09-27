import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HarnessJobDetail } from "@/components/harness/HarnessJobDetail";
import type { ApiHarnessJobDetail } from "@/types/api";

function detail(overrides: Partial<ApiHarnessJobDetail> = {}): ApiHarnessJobDetail {
  return {
    id: "j1",
    workspace: "w1",
    repo: "r1",
    goal: "Find the bug",
    agent: "claude_code",
    sensitivity: "normal",
    status: "pending",
    failure_reason: "",
    created_by: "u1",
    started_at: null,
    elapsed_seconds: 0,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    briefing: {},
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("HarnessJobDetail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the goal, status, and agent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail())),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByText("Find the bug")).toBeInTheDocument();
    expect(screen.getByText("Ajan: Claude Code")).toBeInTheDocument();
  });

  it("renders a pending job with an empty briefing object without crashing", async () => {
    // Regression test: HarnessJob.briefing defaults to `{}` at the model
    // level for any job that hasn't reached finalization yet -- an
    // earlier version of this component assumed `briefing.findings` was
    // always an array and crashed reading `.length` on `undefined`.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ status: "pending", briefing: {} }))),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByText("Find the bug")).toBeInTheDocument();
    expect(screen.queryByText("Özet")).not.toBeInTheDocument();
    expect(screen.queryByText("Bulgular")).not.toBeInTheDocument();
  });

  it("shows the summary and findings distinctly from the collapsed raw transcript", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          detail({
            status: "succeeded",
            briefing: {
              summary: "All clear.",
              findings: ["Minor typo in README"],
              raw_transcript: "the CLI's own raw output",
            },
          }),
        ),
      ),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByText("All clear.")).toBeInTheDocument();
    expect(screen.getByText("Minor typo in README")).toBeInTheDocument();
    // The raw transcript is collapsed behind an explicit disclosure, not
    // shown by default alongside the trustworthy summary/findings.
    expect(screen.queryByText("the CLI's own raw output")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ham çıktıyı göster" }));

    expect(await screen.findByText("the CLI's own raw output")).toBeInTheDocument();
  });

  it("shows a no-briefing message for a terminal job with nothing produced", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ status: "failed", briefing: {} }))),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByText("Bu iş için brifing yok.")).toBeInTheDocument();
  });

  it("still renders the raw transcript when summarization failed but a transcript was preserved", async () => {
    // Codex code-review P2, branch 4 round 1: harness/execution.py's own
    // _finalize() deliberately persists raw_transcript with an empty
    // summary/findings when the briefing gateway call fails -- the
    // disclosure must not be hidden just because there's no summary.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          detail({
            status: "succeeded",
            briefing: { summary: "", findings: [], raw_transcript: "raw CLI output only" },
          }),
        ),
      ),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByRole("button", { name: "Ham çıktıyı göster" })).toBeInTheDocument();
    expect(screen.queryByText("Özet")).not.toBeInTheDocument();
    expect(screen.queryByText("Bu iş için brifing yok.")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ham çıktıyı göster" }));

    expect(await screen.findByText("raw CLI output only")).toBeInTheDocument();
  });

  it("cancels a pending job", async () => {
    let current = detail({ status: "pending" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/harness/jobs/j1/cancel") {
          current = { ...current, status: "cancelled", failure_reason: "killed" };
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);
    await screen.findByText("Find the bug");
    await userEvent.click(screen.getByRole("button", { name: "İşi iptal et" }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "İşi iptal et" })).not.toBeInTheDocument();
    });
  });

  it("never shows a cancel button for a terminal job", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ status: "succeeded" }))),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);
    await screen.findByText("Find the bug");

    expect(screen.queryByRole("button", { name: "İşi iptal et" })).not.toBeInTheDocument();
  });

  it("shows the error with a retry and no lingering skeleton after a failed load", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) return jsonResponse({ detail: "boom" }, 500);
        return jsonResponse(detail());
      }),
    );

    const { container } = render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByText("Bu iş yüklenemedi.")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="skeleton"]')).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));

    expect(await screen.findByText("Find the bug")).toBeInTheDocument();
    expect(screen.queryByText("Bu iş yüklenemedi.")).not.toBeInTheDocument();
  });

  it("renders a Turkish label for the failure reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail({ status: "failed", failure_reason: "timeout" }))),
    );

    render(<HarnessJobDetail jobId="j1" onBack={() => undefined} />);

    expect(await screen.findByText(/Zaman aşımı/)).toBeInTheDocument();
    expect(screen.queryByText(/— timeout/)).not.toBeInTheDocument();
  });
});
