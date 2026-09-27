import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImplementationJobsPanel } from "@/components/harness/ImplementationJobsPanel";
import { implJob, jsonResponse, repo } from "@/components/harness/test-fixtures";

function renderPanel(overrides: Partial<Parameters<typeof ImplementationJobsPanel>[0]> = {}) {
  const props = {
    workspaceId: "w1",
    prefill: null,
    onPrefillConsumed: vi.fn(),
    onSelect: vi.fn(),
    ...overrides,
  };
  return { ...render(<ImplementationJobsPanel {...props} />), props };
}

describe("ImplementationJobsPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows a skeleton while loading, then the rows with Turkish status and gate labels", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([
          implJob({ status: "failed", failure_reason: "secret_leak" }),
        ]),
      ),
    );

    const { container } = renderPanel();
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();

    expect(await screen.findByText("Add a health endpoint")).toBeInTheDocument();
    expect(
      screen.getByText("Başarısız — Değişikliklerde gizli bilgi sızıntısı tespit edildi"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Depo: jarvis · Kapı A: Karar bekliyor/)).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));

    renderPanel();

    expect(await screen.findByText("Henüz uygulama işi yok.")).toBeInTheDocument();
  });

  it("shows an error (not the empty state) when the list fails to load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "no" }, 403)));

    renderPanel();

    expect(await screen.findByText("Uygulama işleri yüklenemedi.")).toBeInTheDocument();
    expect(screen.queryByText("Henüz uygulama işi yok.")).not.toBeInTheDocument();
  });

  it("selects a row by click or keyboard", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([implJob({ status: "succeeded" })])));

    const { props } = renderPanel();
    const row = await screen.findByRole("button", { name: /Add a health endpoint/ });
    await userEvent.click(row);
    row.focus();
    await userEvent.keyboard("{Enter}");

    expect(props.onSelect).toHaveBeenCalledTimes(2);
    expect(props.onSelect).toHaveBeenCalledWith("ij1");
  });

  it("polls silently while a job is non-terminal and stops once it is terminal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let listCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        listCalls += 1;
        return jsonResponse([
          implJob(listCalls === 1 ? { status: "implementing" } : { status: "succeeded" }),
        ]);
      }),
    );

    renderPanel();
    expect(await screen.findByText("Uygulanıyor")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    expect(await screen.findByText("Tamamlandı")).toBeInTheDocument();
    const callsAtTerminal = listCalls;

    await vi.advanceTimersByTimeAsync(9000);
    expect(listCalls).toBe(callsAtTerminal);
  });

  it("opens the form prefilled from a discovery job and adds the created job", async () => {
    const created = implJob({ id: "ij9", goal: "Fix the flaky test", discovery_job: "d1" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/harness/repos")) return jsonResponse([repo()]);
        if (init?.method === "POST") return jsonResponse(created, 201);
        return jsonResponse([]);
      }),
    );

    const { props } = renderPanel({
      prefill: { repo: repo().id, goal: "Fix the flaky test", discoveryJobId: "d1" },
    });

    expect(screen.getByLabelText("Uygulama hedefi")).toHaveValue("Fix the flaky test");
    await screen.findByRole("combobox", { name: "Depo" });
    // While the form is open the header toggle reads "İptal", so the only
    // "Uygulama iste" button is the form's submit.
    await userEvent.click(screen.getByRole("button", { name: "Uygulama iste" }));

    expect(await screen.findByRole("button", { name: /Fix the flaky test/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Uygulama hedefi")).not.toBeInTheDocument();
    expect(props.onPrefillConsumed).toHaveBeenCalled();
  });
});
