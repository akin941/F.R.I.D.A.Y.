import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { EvalRunsView, percent } from "@/components/memory/EvalRunsView";
import { renderWithClient } from "@/test/render";
import type { ApiEvalRun } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/memory/evals" }));

function run(overrides: Partial<ApiEvalRun> = {}): ApiEvalRun {
  return {
    id: "r1",
    run_id: "11111111-1111-4111-8111-111111111111",
    set: "public",
    kind: "baseline",
    retrieval_mode: "legacy",
    compression_purpose: null,
    arm: null,
    started_at: "2026-11-20T08:00:00Z",
    finished_at: "2026-11-20T08:05:00Z",
    total: 40,
    answered: 38,
    no_provider: 0,
    budget_stopped: 0,
    spend_usd: "0.123400",
    recall_at_10: 0.825,
    leak_rate: 0,
    temporal_correctness: 0.75,
    answer_accuracy: null,
    pre_tokens: null,
    post_tokens: null,
    verdict: null,
    ...overrides,
  };
}

function stub(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("percent", () => {
  it("formats rates and marks unmeasured ones", () => {
    expect(percent(0.825)).toBe("%82.5");
    expect(percent(0)).toBe("%0.0");
    expect(percent(null)).toBe("—");
  });
});

describe("EvalRunsView", () => {
  it("shows each run's metrics", async () => {
    stub([
      run(),
      run({
        id: "r2",
        kind: "compare",
        arm: "b",
        retrieval_mode: "hybrid",
        verdict: "fail",
        pre_tokens: 1200,
        post_tokens: 800,
        budget_stopped: 2,
      }),
    ]);
    renderWithClient(<EvalRunsView />);
    const first = await screen.findByTestId("eval-run-r1");
    expect(first).toHaveTextContent("Açık küme · Temel ölçüm · Getirme: legacy");
    expect(first).toHaveTextContent("38 / 40");
    expect(first).toHaveTextContent("%82.5");
    expect(first).toHaveTextContent("%75.0");
    expect(first).toHaveTextContent("$0.12");
    const second = screen.getByTestId("eval-run-r2");
    expect(second).toHaveTextContent("Kol B");
    expect(second).toHaveTextContent("1200 → 800");
    expect(within(second).getByText("Gerileme")).toBeInTheDocument();
    expect(second).toHaveTextContent("Bütçe nedeniyle duran: 2");
    // No private baseline yet: pending owner evaluation.
    expect(screen.getByTestId("eval-private-pending")).toBeInTheDocument();
  });

  it("never renders case text, even if a row carried some", async () => {
    stub([
      {
        ...run({ set: "private" }),
        question: "SECRET-QUESTION what did we decide about acme",
        answer: "SECRET-ANSWER",
        cases: [{ id: "case-7", text: "SECRET-CASE" }],
      },
    ]);
    renderWithClient(<EvalRunsView />);
    await screen.findByTestId("eval-run-r1");
    expect(document.body.textContent).not.toMatch(/SECRET|case-7/);
  });

  it("phone width: the metrics table scrolls inside its own box", async () => {
    stub([run()]);
    renderWithClient(<EvalRunsView />);
    const table = await screen.findByTestId("eval-runs");
    expect(table.parentElement).toHaveClass("overflow-x-auto");
  });

  it("a recorded private baseline clears the pending notice", async () => {
    stub([run({ set: "private" })]);
    renderWithClient(<EvalRunsView />);
    await screen.findByTestId("eval-run-r1");
    expect(screen.queryByTestId("eval-private-pending")).not.toBeInTheDocument();
  });

  it("empty and error states", async () => {
    stub([]);
    const { unmount } = renderWithClient(<EvalRunsView />);
    expect(await screen.findByText(/Henüz kayıtlı çalışma yok/)).toBeInTheDocument();
    unmount();
    stub({ code: "owner_required" }, 403);
    renderWithClient(<EvalRunsView />);
    expect(await screen.findByText("Değerlendirmeler yüklenemedi.")).toBeInTheDocument();
  });
});
