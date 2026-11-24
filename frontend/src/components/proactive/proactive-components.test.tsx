import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HudNoticesPanel } from "@/components/hud/HudNoticesPanel";
import { NoticeBell } from "@/components/proactive/NoticeBell";
import { NoticesPanel } from "@/components/proactive/NoticesPanel";
import { PauseToggle } from "@/components/proactive/PauseToggle";
import { ScheduleEditor } from "@/components/proactive/ScheduleEditor";
import { maskFromWeekdays, safeLink, weekdaysFromMask } from "@/lib/api/proactive";
import { applyHudFrame, parseHudFrame } from "@/lib/hud/events";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: { id: "w1", name: "Kişisel", type: "personal", role: "owner" } }),
}));

type Handler = (init?: RequestInit) => Response;

function stub(routes: Record<string, Handler>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
      const key = `${init?.method ?? "GET"} ${url}`;
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected ${key}`);
      return handler(init);
    }),
  );
  return calls;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

describe("proactive api helpers", () => {
  it("round-trips the weekday mask and keeps links in the closed set", () => {
    expect(weekdaysFromMask(0b0011111)).toEqual([true, true, true, true, true, false, false]);
    expect(maskFromWeekdays([true, false, false, false, false, false, true])).toBe(0b1000001);
    expect(safeLink("/approvals/abc-1")).toBe("/approvals/abc-1");
    expect(safeLink("/harness/implementations/j1")).toBe("/harness/implementations/j1");
    expect(safeLink("https://evil.example")).toBeNull();
    expect(safeLink("//evil.example")).toBeNull();
    expect(safeLink("/approvals/../admin")).toBeNull();
  });
});

describe("ScheduleEditor", () => {
  it("loads the schedules and saves the morning digest", async () => {
    const calls = stub({
      "GET /api/proactive/schedules?workspace=w1": json({
        workspace: "w1",
        paused: false,
        schedules: [
          {
            id: "s1",
            kind: "morning_digest",
            enabled: false,
            local_time: "07:45:00",
            weekdays: 31,
            speak_on_open: false,
            interval_minutes: null,
            quiet_start: null,
            quiet_end: null,
            last_run_at: null,
            next_run_at: null,
          },
        ],
      }),
      "PUT /api/proactive/schedules/morning_digest?workspace=w1": json({ id: "s1" }),
      "PUT /api/proactive/schedules/heartbeat?workspace=w1": json({ id: "s2" }),
    });
    render(<ScheduleEditor workspaceId="w1" />);
    const enabled = await screen.findAllByLabelText("Açık");
    await waitFor(() => expect(enabled[0]).not.toBeDisabled());
    await userEvent.click(enabled[0]!);
    await userEvent.click(screen.getByLabelText("HUD açılınca bir kez sesli oku"));
    await userEvent.click(screen.getByLabelText("Cmt"));
    await userEvent.click(screen.getByTestId("save-digest"));
    await screen.findByText("Brifing zamanlaması kaydedildi.");
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({ enabled: true, local_time: "07:45", weekdays: 63, speak_on_open: true });

    await userEvent.click(screen.getByTestId("save-heartbeat"));
    await screen.findByText("Nabız kaydedildi.");
    expect(calls.at(-1)!.body).toEqual({ enabled: false, interval_minutes: 30, quiet_start: null, quiet_end: null });
  });
});

describe("PauseToggle", () => {
  it("reads the state and pauses / resumes", async () => {
    const calls = stub({
      "GET /api/proactive/pause?workspace=w1": json({ paused: false }),
      "POST /api/proactive/pause?workspace=w1": json({ paused: true }),
      "DELETE /api/proactive/pause?workspace=w1": () => new Response(JSON.stringify({ paused: false })),
    });
    render(<PauseToggle workspaceId="w1" />);
    const box = screen.getByLabelText("Proaktif çalışmaları duraklat");
    await waitFor(() => expect(box).not.toBeDisabled());
    await userEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    await userEvent.click(box);
    await waitFor(() => expect(box).not.toBeChecked());
    expect(calls.map((c) => c.method)).toEqual(["GET", "POST", "DELETE"]);
  });
});

describe("NoticeBell and NoticesPanel", () => {
  const NOTICES = {
    workspace: "w1",
    unread: 2,
    notices: [
      {
        id: "n1",
        kind: "tasks.due_today",
        title: "Bugün: Fatura",
        count: 1,
        link: "/tasks",
        data_class: "personal",
        created_at: "2026-11-24T07:00:00Z",
        read_at: null,
        dismissed_at: null,
      },
      {
        id: "n2",
        kind: "approvals.pending",
        title: "Onay: x bekliyor",
        count: 1,
        link: "https://evil.example",
        data_class: "personal",
        created_at: "2026-11-24T07:00:00Z",
        read_at: null,
        dismissed_at: null,
      },
    ],
  };

  it("the bell shows the unread count and links to /notices", async () => {
    stub({ "GET /api/proactive/notices?workspace=w1&state=unread": json(NOTICES) });
    render(<NoticeBell />);
    expect(await screen.findByTestId("notice-bell-count")).toHaveTextContent("2");
    expect(screen.getByTestId("notice-bell")).toHaveAttribute("href", "/notices");
    expect(screen.getByTestId("notice-bell")).toHaveAccessibleName("Bildirimler, 2 okunmamış");
  });

  it("the panel marks a notice read on open, never follows a foreign link, and dismisses all", async () => {
    const calls = stub({
      "GET /api/proactive/notices?workspace=w1&state=all": json(NOTICES),
      "POST /api/proactive/notices/n2/read": json({ ...NOTICES.notices[1], read_at: "2026-11-24T08:00:00Z" }),
      "POST /api/proactive/notices/dismiss-all?workspace=w1": json({ dismissed: 1 }),
    });
    render(<NoticesPanel />);
    const rows = await screen.findAllByTestId("notice-row");
    expect(within(rows[0]!).getByRole("link")).toHaveAttribute("href", "/tasks");
    // The foreign link is not a link at all.
    expect(within(rows[1]!).queryByRole("link")).toBeNull();
    await userEvent.click(within(rows[1]!).getByRole("button"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/proactive/notices/n2/read")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Tümünü kapat" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("dismiss-all"))).toBe(true));
  });
});

describe("HUD notices", () => {
  it("parses the notices and digest sections, including a digest turning null", () => {
    const snapshot = parseHudFrame(
      `data: ${JSON.stringify({
        type: "snapshot",
        workspace: "w1",
        interval_seconds: 5,
        approvals: { pending_count: 0, items: [], tools_running: 0 },
        tasks: { date: "2026-11-24", open_count: 0, items: [] },
        cost: {},
        health: { providers: [], providers_down: 0, budgets: [], budgets_exhausted: 0, preflight: null },
        notices: { unread: 1, latest: [{ title: "Bugün: Fatura", link: "/tasks" }] },
        digest: { id: "d1", created_at: null, spoken_text: "Günaydın.", opened: false, spoken: false, speak_on_open: false },
      })}`,
    )!;
    let data = applyHudFrame(null, snapshot);
    expect(data?.notices?.unread).toBe(1);
    expect(data?.digest?.id).toBe("d1");
    data = applyHudFrame(data, parseHudFrame(`data: {"type":"delta","notices":{"unread":0,"latest":[]}}`)!);
    expect(data?.notices?.unread).toBe(0);
    expect(data?.digest?.id).toBe("d1");
    data = applyHudFrame(data, parseHudFrame(`data: {"type":"delta","digest":null}`)!);
    expect(data?.digest).toBeNull();
  });

  it("the panel shows the count and titles only, with safe links", () => {
    render(
      <HudNoticesPanel
        notices={{
          unread: 2,
          latest: [
            { title: "Bugün: Fatura", link: "/tasks" },
            { title: "Dış", link: "javascript:alert(1)" },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("hud-notices-count")).toHaveTextContent("2 okunmamış");
    expect(screen.getByRole("link", { name: "Bugün: Fatura" })).toHaveAttribute("href", "/tasks");
    expect(screen.queryByRole("link", { name: "Dış" })).toBeNull();
  });
});
