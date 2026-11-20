import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HarnessJobsView } from "@/components/harness/HarnessJobsView";
import { REPO_ID, implJob, repo } from "@/components/harness/test-fixtures";
import type { ApiHarnessJob } from "@/types/api";
import { renderWithClient } from "@/test/render";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function job(overrides: Partial<ApiHarnessJob> = {}): ApiHarnessJob {
  return {
    id: "j1",
    workspace: "w1",
    repo: "r1",
    goal: "Find the bug",
    agent: "claude_code",
    agent_routing_reason: "policy:default",
    sensitivity: "normal",
    status: "succeeded",
    failure_reason: "",
    created_by: "u1",
    started_at: null,
    elapsed_seconds: 0,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("HarnessJobsView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("loads and renders the job list", async () => {
    const row = job({ goal: "Find the bug" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    renderWithClient(<HarnessJobsView />);

    expect(await screen.findByText("Find the bug")).toBeInTheDocument();
  });

  it("shows an empty state when there are no jobs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    renderWithClient(<HarnessJobsView />);

    expect(await screen.findByText("Henüz harness işi yok.")).toBeInTheDocument();
  });

  it("starts a new job from the repo picker and shows it in the list", async () => {
    const created = job({ id: "new1", goal: "Audit the auth module", status: "pending" });
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input === "/api/harness/jobs") {
        return jsonResponse(created, 201);
      }
      if (input.startsWith("/api/harness/repos")) return jsonResponse([repo()]);
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni iş" }));
    // No raw repo-id input any more -- repos are picked from the registry.
    expect(screen.queryByLabelText("Depo kimliği")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));
    await userEvent.click(await screen.findByRole("option", { name: "friday" }));
    await userEvent.type(screen.getByLabelText("Hedef"), "Audit the auth module");
    await userEvent.click(screen.getByRole("button", { name: "İşi başlat" }));

    expect((await screen.findAllByText("Audit the auth module")).length).toBeGreaterThan(0);
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      workspace: "w1",
      repo: REPO_ID,
      goal: "Audit the auth module",
    });
  });

  it("switches to the implementation jobs tab; rows link to their routed detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.startsWith("/api/harness/implementation-jobs?")) {
          return jsonResponse([implJob({ status: "succeeded" })]);
        }
        return jsonResponse([]);
      }),
    );

    renderWithClient(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    const implTab = screen.getByRole("tab", { name: "Uygulama işleri" });
    expect(screen.getByRole("tab", { name: "Keşif işleri" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await userEvent.click(implTab);
    expect(implTab).toHaveAttribute("aria-selected", "true");

    expect(await screen.findByRole("link", { name: /Add a health endpoint/ })).toHaveAttribute(
      "href",
      "/harness/implementations/ij1?workspace=w1",
    );
  });

  it("moves between tabs with the arrow keys", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));

    renderWithClient(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    screen.getByRole("tab", { name: "Keşif işleri" }).focus();
    await userEvent.keyboard("{ArrowRight}");

    const implTab = screen.getByRole("tab", { name: "Uygulama işleri" });
    expect(implTab).toHaveAttribute("aria-selected", "true");
    expect(implTab).toHaveFocus();
    expect(await screen.findByText("Henüz uygulama işi yok.")).toBeInTheDocument();
  });

  it("opens the implementation form prefilled from a discovery job link, and links the new job", async () => {
    window.history.replaceState(null, "", "/harness?tab=implementation&discovery=d1&workspace=w1");
    const discovery = job({ id: "d1", repo: REPO_ID, goal: "Find the flaky test" });
    const created = implJob({ id: "ij9", goal: "Find the flaky test", discovery_job: "d1" });
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/harness/jobs/d1") return jsonResponse({ ...discovery, briefing: {} });
      if (input.startsWith("/api/harness/repos")) return jsonResponse([repo()]);
      if (init?.method === "POST" && input === "/api/harness/implementation-jobs") {
        return jsonResponse(created, 201);
      }
      if (input.startsWith("/api/harness/implementation-jobs?")) return jsonResponse([]);
      return jsonResponse([discovery]);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<HarnessJobsView />);

    // On the implementation tab, with the form prefilled from the job.
    expect(await screen.findByLabelText("Uygulama hedefi")).toHaveValue("Find the flaky test");
    expect(screen.getByRole("tab", { name: "Uygulama işleri" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByRole("combobox", { name: "Depo" })).toHaveTextContent("friday");

    await userEvent.click(screen.getByRole("button", { name: "Uygulama iste" }));

    expect(
      (await screen.findAllByRole("link", { name: /Find the flaky test/ })).length,
    ).toBeGreaterThan(0);
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      workspace: "w1",
      repo: REPO_ID,
      goal: "Find the flaky test",
      discovery_job: "d1",
    });
  });

  it("links each discovery job to its routed detail", async () => {
    const row = job({ id: "j1", goal: "Find the bug" });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([row])));

    renderWithClient(<HarnessJobsView />);

    expect(await screen.findByRole("link", { name: /Find the bug/ })).toHaveAttribute(
      "href",
      "/harness/jobs/j1?workspace=w1",
    );
  });

  it("lists every in-flight job of both kinds in the live strip, and none that finished", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.startsWith("/api/harness/implementation-jobs?")) {
          return jsonResponse([
            implJob({ id: "ij1", goal: "Gate waits", status: "pending_gate_b" }),
            implJob({ id: "ij2", goal: "Impl done", status: "succeeded" }),
          ]);
        }
        return jsonResponse([
          job({ id: "j1", goal: "Discovery runs", status: "running" }),
          job({ id: "j2", goal: "Discovery done", status: "succeeded" }),
        ]);
      }),
    );

    renderWithClient(<HarnessJobsView />);

    const live = (await screen.findByRole("heading", { name: /Canlı işler \(2\)/ })).closest(
      "section",
    )!;
    const links = within(live).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href")).sort()).toEqual([
      "/harness/implementations/ij1?workspace=w1",
      "/harness/jobs/j1?workspace=w1",
    ]);
    expect(within(live).queryByText("Impl done")).not.toBeInTheDocument();
    expect(within(live).queryByText("Discovery done")).not.toBeInTheDocument();
  });

  it("links to the repo registry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));
    renderWithClient(<HarnessJobsView />);
    expect(await screen.findByRole("link", { name: "Depolar" })).toHaveAttribute(
      "href",
      "/harness/repos",
    );
  });

  it("renders a Turkish label for the failure reason, never the raw value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([job({ status: "failed", failure_reason: "runner_error" })])),
    );

    renderWithClient(<HarnessJobsView />);

    expect(await screen.findByText(/Çalıştırıcı hatası/)).toBeInTheDocument();
    expect(screen.queryByText(/runner_error/)).not.toBeInTheDocument();
  });
});
