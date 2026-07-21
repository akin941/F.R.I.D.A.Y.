import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TodayView, localDateString } from "@/components/tasks/TodayView";
import type { ApiTask } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function task(overrides: Partial<ApiTask> = {}): ApiTask {
  return {
    id: "t1",
    workspace: "w1",
    title: "Buy milk",
    description: "",
    status: "open",
    priority: "normal",
    due_date: "2026-07-08",
    source: "user_created",
    created_by: "u1",
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("localDateString", () => {
  const originalTz = process.env.TZ;

  afterEach(() => {
    process.env.TZ = originalTz;
  });

  it("reports the local calendar day, not the UTC day, near a day boundary", () => {
    // 2026-07-08T02:00:00Z is 2026-07-07T19:00:00 in America/Los_Angeles
    // (UTC-7, PDT) -- a UTC-day-based computation (e.g. `toISOString().slice
    // (0, 10)`) would wrongly report the 8th here.
    process.env.TZ = "America/Los_Angeles";
    const instant = new Date("2026-07-08T02:00:00Z");
    expect(localDateString(instant)).toBe("2026-07-07");
  });

  it("matches the instant's own getFullYear/getMonth/getDate regardless of timezone", () => {
    process.env.TZ = "Pacific/Kiritimati"; // UTC+14
    const instant = new Date("2026-07-07T23:00:00Z");
    // In UTC+14 this instant is already 2026-07-08 local.
    expect(localDateString(instant)).toBe("2026-07-08");
  });
});

describe("TodayView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders tasks due today or overdue", async () => {
    const row = task({ title: "Buy milk" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    render(<TodayView />);

    expect(await screen.findByText("Buy milk")).toBeInTheDocument();
  });

  it("requests the open status and a client-local due_before filter", async () => {
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => jsonResponse([]),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<TodayView />);
    await screen.findByText("Bugün vadesi gelen bir şey yok. Keyfini çıkar.");

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain("status=open");
    expect(calledUrl).toContain(`due_before=${localDateString(new Date())}`);
  });

  it("shows an empty state when nothing is due", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    render(<TodayView />);

    expect(await screen.findByText("Bugün vadesi gelen bir şey yok. Keyfini çıkar.")).toBeInTheDocument();
  });

  it("completing a task removes it from the list without a re-fetch", async () => {
    const row = task();
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async (input) => {
        if (String(input).includes("/complete")) {
          return jsonResponse({ ...row, status: "done" });
        }
        return jsonResponse([row]);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<TodayView />);
    await screen.findByText("Buy milk");
    await userEvent.click(screen.getByRole("button", { name: "Tamamla" }));

    await waitFor(() => {
      expect(screen.queryByText("Buy milk")).not.toBeInTheDocument();
    });
    // Only the initial GET fired, plus the complete POST -- no follow-up GET.
    const getCalls = fetchMock.mock.calls.filter(([, init]) => (init?.method ?? "GET") === "GET");
    expect(getCalls).toHaveLength(1);
  });
});
