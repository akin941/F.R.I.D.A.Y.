import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { HudLearningPanel, HudSessionsPanel } from "@/components/hud/HudLearningPanels";
import { SessionsView } from "@/components/memory/SessionsView";
import { sessionsPollInterval } from "@/components/memory/learning-queries";
import { POLL_SLOW_MS } from "@/lib/query/polling";
import { renderWithClient } from "@/test/render";
import type { ApiCodingSession } from "@/types/api";

const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Kişisel", type: "personal", role: "owner" },
    workspaces: [],
  }),
}));

function session(overrides: Partial<ApiCodingSession> = {}): ApiCodingSession {
  return {
    id: "s1",
    workspace: { id: WS, name: "Kişisel" },
    tool: "claude_code",
    session_id: "0b9f6a3e",
    project: "friday",
    status: "running",
    stored_status: "running",
    started_at: "2026-11-20T08:00:00Z",
    last_event_at: "2026-11-20T08:30:00Z",
    last_event: "SessionStart",
    last_notification: "",
    event_count: 3,
    ...overrides,
  };
}

function stub({
  sessions = [] as ApiCodingSession[] | null,
  digestCount = 0,
  jobs = [] as unknown[],
} = {}) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (input === "/api/memory/sessions") {
      return sessions === null ? json({ code: "permission_denied" }, 403) : json(sessions);
    }
    if (input === "/api/memory/learning/digest") {
      return json({ since: "2026-11-19T00:00:00Z", count: digestCount, workspaces: [] });
    }
    if (input.startsWith("/api/harness/jobs?")) return json(jobs);
    if (input.startsWith("/api/harness/implementation-jobs?")) return json([]);
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SessionsView", () => {
  it("lists every session state, stale included, with the workspace name", async () => {
    stub({
      sessions: [
        session({ id: "s1", status: "running" }),
        session({ id: "s2", status: "waiting_for_owner", last_notification: "Claude needs your permission" }),
        session({ id: "s3", status: "stopped" }),
        session({ id: "s4", status: "stale", workspace: { id: "w2", name: "Acme" } }),
      ],
    });
    renderWithClient(<SessionsView />);
    const list = await screen.findByTestId("coding-sessions");
    expect(within(list).getByTestId("session-s1")).toHaveTextContent("Çalışıyor");
    expect(within(list).getByTestId("session-s2")).toHaveTextContent("Seni bekliyor");
    expect(within(list).getByTestId("session-s2")).toHaveTextContent("Claude needs your permission");
    expect(within(list).getByTestId("session-s3")).toHaveTextContent("Bitti");
    expect(within(list).getByTestId("session-s4")).toHaveTextContent("Sessiz");
    expect(within(list).getByTestId("session-s4")).toHaveTextContent("Acme");
  });

  it("renders tool text as text, never markup", async () => {
    stub({ sessions: [session({ last_notification: "<img src=x onerror=alert(1)>" })] });
    renderWithClient(<SessionsView />);
    const row = await screen.findByTestId("session-s1");
    expect(row.querySelector("img")).toBeNull();
    expect(row).toHaveTextContent("<img src=x onerror=alert(1)>");
  });

  it("shows the empty state and the harness jobs of the active workspace", async () => {
    const fetchMock = stub({
      jobs: [
        {
          id: "j1",
          workspace: WS,
          repo: "r1",
          goal: "map the memory app",
          agent: "claude_code",
          agent_routing_reason: "",
          sensitivity: "normal",
          status: "running",
          failure_reason: "",
          created_by: null,
          started_at: null,
          elapsed_seconds: 0,
          created_at: "2026-11-20T08:00:00Z",
          updated_at: "2026-11-20T08:00:00Z",
        },
      ],
    });
    renderWithClient(<SessionsView />);
    expect(await screen.findByText(/Kayıtlı oturum yok/)).toBeInTheDocument();
    expect(await screen.findByTestId("sessions-harness-jobs")).toHaveTextContent("map the memory app");
    expect(fetchMock.mock.calls.some(([url]) => url === `/api/harness/jobs?workspace=${WS}`)).toBe(true);
  });

  it("shows an error state when the sessions read is refused", async () => {
    stub({ sessions: null });
    renderWithClient(<SessionsView />);
    expect(await screen.findByText("Oturumlar yüklenemedi.")).toBeInTheDocument();
  });

  it("polls faster only while a session is live", () => {
    expect(sessionsPollInterval([session({ status: "running" })])).toBeLessThan(POLL_SLOW_MS);
    expect(sessionsPollInterval([session({ status: "stale" })])).toBe(POLL_SLOW_MS);
    expect(sessionsPollInterval([])).toBe(POLL_SLOW_MS);
  });
});

describe("HUD cards", () => {
  it("sessions panel: rows with status text, stale and empty states", async () => {
    stub({ sessions: [session({ status: "waiting_for_owner" }), session({ id: "s2", status: "stale" })] });
    renderWithClient(<HudSessionsPanel />);
    const panel = screen.getByTestId("hud-sessions");
    expect(panel).toHaveAttribute("data-slot", "sessions");
    expect(await within(panel).findByText("Seni bekliyor")).toHaveClass("text-hud-awaiting-approval");
    expect(within(panel).getByText(/Sessiz/)).toHaveClass("text-hud-caption-muted");
    expect(within(panel).getByRole("link", { name: "Tüm oturumlar" })).toHaveAttribute("href", "/sessions");
  });

  it("sessions panel: empty", async () => {
    stub();
    renderWithClient(<HudSessionsPanel />);
    expect(await screen.findByTestId("hud-sessions-empty")).toBeInTheDocument();
  });

  it("learning card: a count and a deep link, never a decide control", async () => {
    stub({ digestCount: 4 });
    renderWithClient(<HudLearningPanel />);
    expect(await screen.findByTestId("hud-learning-count")).toHaveTextContent("4 öneri");
    const panel = screen.getByTestId("hud-learning");
    expect(within(panel).queryByRole("button")).not.toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Sabah kartını aç" })).toHaveAttribute("href", "/memory/learning");
  });
});
