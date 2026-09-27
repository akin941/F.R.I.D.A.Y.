import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HarnessJobsView } from "@/components/harness/HarnessJobsView";
import { REPO_ID, implJob, repo } from "@/components/harness/test-fixtures";
import type { ApiHarnessJob } from "@/types/api";

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
  });

  it("loads and renders the job list", async () => {
    const row = job({ goal: "Find the bug" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    render(<HarnessJobsView />);

    expect(await screen.findByText("Find the bug")).toBeInTheDocument();
  });

  it("shows an empty state when there are no jobs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    render(<HarnessJobsView />);

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

    render(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni iş" }));
    // No raw repo-id input any more -- repos are picked from the registry.
    expect(screen.queryByLabelText("Depo kimliği")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));
    await userEvent.click(await screen.findByRole("option", { name: "jarvis" }));
    await userEvent.type(screen.getByLabelText("Hedef"), "Audit the auth module");
    await userEvent.click(screen.getByRole("button", { name: "İşi başlat" }));

    expect(await screen.findByText("Audit the auth module")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      workspace: "w1",
      repo: REPO_ID,
      goal: "Audit the auth module",
    });
  });

  it("switches to the implementation jobs tab and opens a job's detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.startsWith("/api/harness/implementation-jobs?")) {
          return jsonResponse([implJob()]);
        }
        if (input === "/api/harness/implementation-jobs/ij1") {
          return jsonResponse({
            ...implJob(),
            base_commit_sha: "",
            produced_commit_sha: "",
            changed_files: [],
            diff_summary: "",
            test_result: {},
            push_result: {},
            worktree_expires_at: null,
            raw_transcript: "",
            reviews: [],
          });
        }
        return jsonResponse([]);
      }),
    );

    render(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    const implTab = screen.getByRole("tab", { name: "Uygulama işleri" });
    expect(screen.getByRole("tab", { name: "Keşif işleri" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await userEvent.click(implTab);
    expect(implTab).toHaveAttribute("aria-selected", "true");

    await userEvent.click(await screen.findByRole("button", { name: /Add a health endpoint/ }));

    expect(
      await screen.findByRole("button", { name: /Uygulama işlerine dön/ }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "İş hattı" })).toBeInTheDocument();
  });

  it("moves between tabs with the arrow keys", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));

    render(<HarnessJobsView />);
    await screen.findByText("Henüz harness işi yok.");

    screen.getByRole("tab", { name: "Keşif işleri" }).focus();
    await userEvent.keyboard("{ArrowRight}");

    const implTab = screen.getByRole("tab", { name: "Uygulama işleri" });
    expect(implTab).toHaveAttribute("aria-selected", "true");
    expect(implTab).toHaveFocus();
    expect(await screen.findByText("Henüz uygulama işi yok.")).toBeInTheDocument();
  });

  it("requests an implementation from a finished discovery job, prefilled and linked", async () => {
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

    render(<HarnessJobsView />);
    await userEvent.click(await screen.findByText("Find the flaky test"));
    await userEvent.click(await screen.findByRole("button", { name: "Uygulama iste" }));

    // Now on the implementation tab, with the form prefilled from the job.
    expect(screen.getByRole("tab", { name: "Uygulama işleri" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText("Uygulama hedefi")).toHaveValue("Find the flaky test");
    expect(await screen.findByRole("combobox", { name: "Depo" })).toHaveTextContent("jarvis");

    await userEvent.click(screen.getByRole("button", { name: "Uygulama iste" }));

    expect(
      await screen.findByRole("button", { name: /Find the flaky test/ }),
    ).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      workspace: "w1",
      repo: REPO_ID,
      goal: "Find the flaky test",
      discovery_job: "d1",
    });
  });

  it("opens a job's detail view on click", async () => {
    const row = job({ id: "j1", goal: "Find the bug" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input === "/api/harness/jobs/j1") {
          return jsonResponse({ ...row, briefing: {} });
        }
        return jsonResponse([row]);
      }),
    );

    render(<HarnessJobsView />);
    await userEvent.click(await screen.findByText("Find the bug"));

    expect(await screen.findByRole("button", { name: /Harness işlerine dön/ })).toBeInTheDocument();
  });

  it("opens a job's detail view from the keyboard", async () => {
    const row = job({ id: "j1", goal: "Find the bug" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input === "/api/harness/jobs/j1") return jsonResponse({ ...row, briefing: {} });
        return jsonResponse([row]);
      }),
    );

    render(<HarnessJobsView />);
    const card = await screen.findByRole("button", { name: /Find the bug/ });
    card.focus();
    await userEvent.keyboard(" ");

    expect(await screen.findByRole("button", { name: /Harness işlerine dön/ })).toBeInTheDocument();
  });

  it("renders a Turkish label for the failure reason, never the raw value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([job({ status: "failed", failure_reason: "runner_error" })])),
    );

    render(<HarnessJobsView />);

    expect(await screen.findByText(/Çalıştırıcı hatası/)).toBeInTheDocument();
    expect(screen.queryByText(/runner_error/)).not.toBeInTheDocument();
  });
});
