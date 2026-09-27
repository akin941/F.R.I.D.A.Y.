import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "@/components/shell/Sidebar";
import { SidebarContent } from "@/components/shell/SidebarContent";
import { renderWithClient } from "@/test/render";

const { nav } = vi.hoisted(() => ({ nav: { pathname: "/agents/a1" } }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

const WORKSPACES = [
  { id: "w1", name: "Kişisel", type: "personal" as const, role: "owner" },
  { id: "w2", name: "Acme", type: "company" as const, role: "owner" },
];

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    user: { id: "u1", email: "owner@test.dev", name: "Owner", is_agent: false },
    loading: false,
    workspaces: WORKSPACES,
    activeWorkspace: WORKSPACES[0],
    setActiveWorkspaceId: vi.fn(),
  }),
}));

function summary(w1Pending: number, w2Pending: number, w1Harness = 0) {
  return {
    workspaces: [
      { workspace: "w1", pending_approvals: w1Pending, live_harness_jobs: w1Harness, live_agents: 0 },
      { workspace: "w2", pending_approvals: w2Pending, live_harness_jobs: 0, live_agents: 0 },
    ],
    totals: {
      pending_approvals: w1Pending + w2Pending,
      live_harness_jobs: w1Harness,
      live_agents: 0,
    },
  };
}

function stubSummary(body: unknown) {
  const fetchMock = vi.fn(async (input: string) => {
    if (input === "/api/activity/summary") return new Response(JSON.stringify(body));
    throw new Error(`unexpected fetch ${input}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("SidebarContent", () => {
  beforeEach(() => {
    nav.pathname = "/agents/a1";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the grouped navigation, without the voice entry", () => {
    stubSummary(summary(0, 0));
    renderWithClient(<SidebarContent />);

    const navigation = screen.getByRole("navigation", { name: "Ana menü" });
    const work = within(navigation).getByRole("list", { name: "Çalışma" });
    expect(within(work).getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Sohbet",
      "Onaylar",
      "Harness",
    ]);
    for (const group of ["Hafıza", "Firma", "Otomasyon", "Ayarlar"]) {
      expect(within(navigation).getByRole("list", { name: group })).toBeInTheDocument();
    }
    expect(within(navigation).queryByRole("link", { name: /Sesli/ })).not.toBeInTheDocument();
  });

  it("marks the current section with aria-current, detail routes included", () => {
    stubSummary(summary(0, 0));
    renderWithClient(<SidebarContent />);

    expect(screen.getByRole("link", { name: "Ajanlar" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Sohbet" })).not.toHaveAttribute("aria-current");
  });

  it("shows the active workspace's pending approvals and flags other workspaces", async () => {
    stubSummary(summary(3, 2, 1));
    renderWithClient(<SidebarContent />);

    expect(
      await screen.findByRole("link", {
        name: "Onaylar, 3 bekleyen onay, Diğer çalışma alanlarında 2 bekleyen onay",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Harness, 1 çalışan iş" })).toBeInTheDocument();
  });

  it("shows no approvals badge when nothing is pending anywhere", async () => {
    const fetchMock = stubSummary(summary(0, 0));
    renderWithClient(<SidebarContent />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.getByRole("link", { name: "Onaylar" })).toBeInTheDocument();
  });

  it("has a theme switch", () => {
    stubSummary(summary(0, 0));
    renderWithClient(<SidebarContent />);
    expect(screen.getByRole("radiogroup", { name: "Tema" })).toBeInTheDocument();
  });
});

describe("Sidebar (mobile)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens the navigation in a sheet and closes it on navigation", async () => {
    stubSummary(summary(0, 0));
    const user = userEvent.setup();
    renderWithClient(<Sidebar />);

    await user.click(screen.getByRole("button", { name: "Menüyü aç" }));
    const sheet = await screen.findByRole("dialog", { name: "Menü" });
    const link = within(sheet).getByRole("link", { name: "Görevler" });
    link.addEventListener("click", (event) => event.preventDefault());
    await user.click(link);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
